import asyncio
import concurrent.futures
import json
import os
import re
import tempfile
import threading
import time
import types
from decimal import Decimal
from unittest.mock import patch

import pytest
import tornado.ioloop
import tornado.testing
from beancount import loader
from beancount.core.data import Transaction

from . import history_hints, jev, webserver
from .accept_rules_test import JOURNAL, loaded_reconciler, posting, transaction


def narrations(loaded):
    return [p.entries[0].narration for p in loaded.get_ai_pending()]


def test_ai_eligibility_and_disjoint_counts(tmp_path):
    rules = {'categorizer': 'rules'}
    two_groups = transaction(6, 'Two groups', 9, 'Expenses:FIXME')
    two_groups = two_groups._replace(postings=[
        two_groups.postings[0], posting('Expenses:FIXME', 4), posting('Expenses:FIXME', 5)])
    # Import rules changed the transaction but left the account unknown.
    partial = transaction(8, 'Partly by rules', 11, 'Expenses:FIXME')._replace(
        meta={'rule_meta': 'yes'})
    loaded, _ = loaded_reconciler(tmp_path, JOURNAL + '''
2020-01-09 * "Journal fixme"
  Assets:Bank -12 USD
  Expenses:FIXME 12 USD
''', [
        transaction(1, 'Rules coffee', 3, 'Expenses:Coffee', rules),
        transaction(2, 'Unknown', 7, 'Expenses:FIXME'),
        # Matches the manual transaction, so it needs review.
        transaction(5, 'Duplicate coffee', 4, 'Expenses:FIXME'),
        two_groups,
        transaction(7, 'Private', 10, 'Expenses:FIXME'),
        partial,
    ])
    loaded.jev_settings = loaded.jev_settings._replace(
        skip_patterns=[re.compile('Private')])
    assert narrations(loaded) == ['Unknown', 'Partly by rules', 'Journal fixme']
    assert loaded.count_ai_pending() == 3
    assert loaded.count_rules_pending() == 1
    assert loaded.get_ai_pending()[1].entries[0].meta['rule_meta'] == 'yes'

    # The skip check uses the same text that Classify with AI sends.
    for pending in loaded.get_ai_pending():
        expected = history_hints.candidate_snapshot(
            loaded._make_candidates_from_import_result(pending).candidates[-1])[1]
        actual = history_hints.candidate_snapshot(
            loaded.make_ai_candidate(pending.entries[0]))[1]
        assert actual['transaction'] == expected['transaction']
        assert actual['token'] == expected['token']

    loaded.jev_settings = None
    assert loaded.count_ai_pending() == 0


def test_accept_ai_candidate_writes_meta(tmp_path):
    loaded, journal = loaded_reconciler(tmp_path, JOURNAL, [
        transaction(2, 'Unknown', 7, 'Expenses:FIXME'),
        transaction(3, 'Other', 8, 'Expenses:FIXME'),
    ])
    pending = loaded.get_ai_pending()[0]
    loaded.accept_ai_candidate(pending.entries[0], 'Expenses:Coffee', 0.9349)
    assert narrations(loaded) == ['Other']
    entries, errors, _ = loader.load_file(str(journal))
    assert errors == []
    accepted = next(e for e in entries
                    if isinstance(e, Transaction) and e.narration == 'Unknown')
    posting = accepted.postings[1]
    assert posting.account == 'Expenses:Coffee'
    assert posting.meta['categorizer'] == 'jev'
    assert posting.meta['jev_confidence'] == Decimal('0.93')
    assert posting.meta['jev_account'] == 'Expenses:Coffee'
    assert accepted.postings[0].meta['source_desc'] == 'Unknown'


BATCH_JOURNAL = '''
2000-01-01 open Assets:Bank USD
2000-01-01 open Expenses:Coffee USD
2000-01-01 open Expenses:Groceries USD
2000-01-01 open Expenses:FIXME USD

2020-01-01 * "Shop" "Groceries"
  Assets:Bank -5 USD
  Expenses:Groceries 5 USD
''' + ''.join('''
2020-02-%02d * "Payee %d" "Pending %d"
  Assets:Bank -%d USD
  Expenses:FIXME %d USD
''' % (day, day, day, day * 10, day * 10) for day in range(1, 6))


class BatchTest(tornado.testing.AsyncTestCase):
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.journal = '%s/journal.bean' % self.directory.name
        ignored = '%s/ignored.bean' % self.directory.name
        with open(self.journal, 'w') as f:
            f.write(BATCH_JOURNAL)
        open(ignored, 'w').close()
        self.configured = patch.object(jev, 'configured', lambda: True)
        self.configured.start()
        self.app = webserver.Application(webserver.parse_arguments(
            [], journal_input=self.journal, ignored_journal=ignored,
            default_output=self.journal), ioloop=tornado.ioloop.IOLoop.current())

    def tearDown(self):
        if self.app.check_modification_observer is not None:
            self.app.check_modification_observer.stop()
        self.configured.stop()
        self.directory.cleanup()
        super().tearDown()

    async def until(self, condition):
        for _ in range(500):
            if condition():
                return
            await asyncio.sleep(.01)
        raise AssertionError('timeout')

    async def loaded(self):
        await self.until(lambda: self.app.current_state.get('ai_pending') is not None)
        return self.app.reconciler.loaded_future.result()

    async def run_batch(self, classify, mode='ai', n=None):
        loaded = await self.loaded()
        if n is None:
            n = self.app.current_state['ai_pending']
        with patch.object(jev, 'classify', side_effect=classify):
            self.app.start_jev_batch(mode, n)
            await self.until(lambda: not self.app.jev_running())
        return loaded

    def journal_text(self):
        with open(self.journal) as f:
            return f.read()

    @tornado.testing.gen_test
    async def test_threshold_and_counts(self):
        confidences = {'Pending 1': .95, 'Pending 2': .5, 'Pending 3': .9,
                       'Pending 4': .89, 'Pending 5': 1}
        seen = []

        def classify(context, instructions=None):
            narration = re.search(r'"Pending \d"', context['transaction']).group(0)[1:-1]
            seen.append(narration)
            return {'category': 'Expenses:Coffee', 'confidence': confidences[narration]}

        loaded = await self.loaded()
        assert self.app.current_state['ai_pending'] == 5
        assert self.app.current_state['jev_enabled'] is True
        assert self.app.current_state['jev_valid_confidence'] == .9
        await self.run_batch(classify)
        assert seen == ['Pending %d' % d for d in range(1, 6)]
        assert self.app.current_state['jev_run'] == dict(
            mode='ai', i=5, n=5, accepted=3, rules=0, below=2, errors=0, skipped=0, running=False)
        assert self.app.current_state['message'] == \
            'JEV: accepted 3 of 5, 2 below threshold, 0 errors, 0 skipped'
        assert self.app.current_state['ai_pending'] == 2
        assert [p.entries[0].narration for p in loaded.pending_data] == ['Pending 2', 'Pending 4']
        text = self.journal_text()
        assert text.count('categorizer: "jev"') == 3
        assert 'jev_confidence: 0.95' in text and 'jev_confidence: 1.00' in text
        assert loader.load_file(self.journal)[1] == []

        # No caching: a second run sends the remaining transactions again.
        seen.clear()
        await self.run_batch(classify)
        assert seen == ['Pending 2', 'Pending 4']

    @tornado.testing.gen_test
    async def test_stop_after_current_request_and_guards(self):
        started, release = threading.Event(), threading.Event()

        def classify(context, instructions=None):
            started.set()
            assert release.wait(4)
            return {'category': 'Expenses:Coffee', 'confidence': .99}

        loaded = await self.loaded()
        with patch.object(jev, 'classify', side_effect=classify):
            self.app.start_jev_batch('ai', 5)
            await self.until(started.is_set)
            assert self.app.jev_running()
            with pytest.raises(jev.JevError) as exc:
                self.app.start_jev_batch('ai', 5)
            assert exc.value.status == 409
            assert self.app.handle_select_candidate(
                {'generation': self.app.current_state['candidates_generation'], 'index': 0}) is None
            self.app.handle_accept_rules()
            self.app.stop_jev_batch()
            release.set()
            await self.until(lambda: not self.app.jev_running())
        assert self.app.current_state['jev_run']['accepted'] == 1
        assert self.app.current_state['message'].endswith(', stopped: stopped')
        assert len(loaded.pending_data) == 4

    @tornado.testing.gen_test
    async def test_fatal_error_stops(self):
        calls = []

        def classify(context, instructions=None):
            calls.append(1)
            raise jev.JevError('OpenRouter has insufficient credits.', fatal=True)

        await self.run_batch(classify)
        assert len(calls) == 1
        assert self.app.current_state['message'] == (
            'JEV: accepted 0 of 5, 0 below threshold, 0 errors, 0 skipped, '
            'stopped: OpenRouter has insufficient credits.')

    @tornado.testing.gen_test
    async def test_three_consecutive_errors_stop(self):
        answers = iter([jev.JevError('timeout', 504), {'category': 'Expenses:Coffee', 'confidence': .1},
                        jev.JevError('invalid', 502), jev.JevError('timeout', 504),
                        jev.JevError('timeout', 504)])

        def classify(context, instructions=None):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer

        await self.run_batch(classify)
        run = self.app.current_state['jev_run']
        assert (run['i'], run['below'], run['errors']) == (5, 1, 4)
        assert 'stopped: 3 errors in a row, last: timeout' in self.app.current_state['message']

    @tornado.testing.gen_test
    async def test_unknown_account_answer_is_an_error(self):
        def classify(context, instructions=None):
            return {'category': 'Expenses:FIXME', 'confidence': .99}

        await self.run_batch(classify)
        assert 'categorizer' not in self.journal_text()
        assert 'stopped: 3 errors in a row, last: JEV suggested an unknown account.' in \
            self.app.current_state['message']

    @tornado.testing.gen_test
    async def test_journal_reload_aborts_without_accepting(self):
        def classify(context, instructions=None):
            # Stands for a journal reload while the request is in flight.
            self.app.reconciler.loaded_future = concurrent.futures.Future()
            return {'category': 'Expenses:Coffee', 'confidence': .99}

        await self.run_batch(classify)
        assert 'categorizer' not in self.journal_text()
        assert self.app.current_state['message'].endswith('stopped: the journal was reloaded')

    @tornado.testing.gen_test
    async def test_unconfirmed_count_is_rejected_before_anything_happens(self):
        loaded = await self.loaded()
        accept_rules = []
        loaded.accept_rules_transaction = accept_rules.append
        with patch.object(jev, 'classify') as classify:
            for mode in ('ai', 'rules_ai'):
                for n in (0, 4, None, -1, '5'):
                    with pytest.raises(jev.JevError) as exc:
                        self.app.start_jev_batch(mode, n)
                    assert exc.value.status in (400, 409)
        assert not classify.called and not accept_rules
        assert self.app.current_state.get('jev_run') is None
        assert 'categorizer' not in self.journal_text()

    @tornado.testing.gen_test
    async def test_journal_modified_during_run_stops_cleanly(self):
        def classify(context, instructions=None):
            # An outside edit while the request is in flight.
            with open(self.journal, 'a') as f:
                f.write('\n; edited\n')
            os.utime(self.journal, (time.time() + 10, time.time() + 10))
            return {'category': 'Expenses:Coffee', 'confidence': .99}

        messages = []
        log_status = self.app.log_status
        self.app.log_status = lambda m: (messages.append(m), log_status(m))
        await self.run_batch(classify)
        assert 'categorizer' not in self.journal_text()
        assert self.app.current_state['jev_run']['accepted'] == 0
        assert messages[-1:] != [] and any(
            m.endswith('stopped: the journal was modified') for m in messages)

    @tornado.testing.gen_test
    async def test_vanished_items_are_counted_as_skipped(self):
        def classify(context, instructions=None):
            # Stands for another change removing the next item from pending.
            loaded = self.app.reconciler.loaded_future.result()
            if len(loaded.pending_data) == 5:
                loaded.pending_data.pop(1)
            return {'category': 'Expenses:Coffee', 'confidence': .99}

        await self.run_batch(classify)
        run = self.app.current_state['jev_run']
        assert (run['accepted'], run['skipped']) == (4, 1)

    def test_batch_handler_rejects_invalid_json(self):
        for body in (b'not json', b'null', b'[1]'):
            handler = webserver.JevBatchHandler.__new__(webserver.JevBatchHandler)
            handler.request = types.SimpleNamespace(body=body)
            handler.application = types.SimpleNamespace(start_jev_batch=lambda *a: None)
            statuses, written = [], []
            handler.set_header = lambda *a: None
            handler.set_status = statuses.append
            handler.write = written.append
            handler.post(None)
            assert statuses == [400] and json.loads(written[0]) == {'error': 'Invalid request.'}

    @tornado.testing.gen_test
    async def test_rules_ai_goes_by_date_and_disabled_jev_is_rejected(self):
        order = []
        loaded = await self.loaded()
        by_rules = {'Pending 2', 'Pending 4'}
        loaded.get_rules_transaction = lambda p: (
            p.entries[0] if p.entries[0].narration in by_rules else None)

        def accept_rules_transaction(transaction):
            order.append('rules ' + transaction.narration)
            return loaded.accept_ai_candidate(transaction, 'Expenses:Groceries', 1)

        loaded.accept_rules_transaction = accept_rules_transaction

        def classify(context, instructions=None):
            order.append('ai ' + re.search(r'"(Pending \d)"', context['transaction']).group(1))
            return {'category': 'Expenses:Coffee', 'confidence': .1}

        await self.run_batch(classify, mode='rules_ai')
        assert order == ['ai Pending 1', 'rules Pending 2', 'ai Pending 3',
                         'rules Pending 4', 'ai Pending 5']
        assert self.app.current_state['message'] == (
            'JEV: accepted 2 of 5 (2 by rules), 3 below threshold, 0 errors, 0 skipped')
        with pytest.raises(jev.JevError):
            self.app.start_jev_batch('other', 0)
        self.app.set_state(jev_enabled=False)
        with pytest.raises(jev.JevError) as exc:
            self.app.start_jev_batch('ai', 5)
        assert exc.value.status == 503


def test_invalid_settings_file_disables_ai(tmp_path):
    settings = tmp_path / 'settings.yaml'
    settings.write_text('jev:\n  valid_confidence: 200\n')
    loaded, _ = loaded_reconciler(tmp_path, JOURNAL, [transaction(2, 'Unknown', 7, 'Expenses:FIXME')],
                                  settings=str(settings))
    assert loaded.jev_settings is None
    assert 'valid_confidence' in loaded.jev_settings_error
    assert any(e[1].startswith('JEV disabled:') for e in loaded.errors)
    assert loaded.count_ai_pending() == 0

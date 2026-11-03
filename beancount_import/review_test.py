"""Editing journal transactions from the Transactions tab (review mode)."""

import asyncio
import difflib
import json
import os
import tempfile
from unittest.mock import patch

import tornado.testing
from beancount import loader
from beancount.core import data

from . import reconcile, webserver
from .accept_rules_test import FakeSource, loaded_reconciler

JOURNAL = """
2000-01-01 open Assets:Bank USD
2000-01-01 open Assets:Cash USD
2000-01-01 open Expenses:Coffee USD
2000-01-01 open Expenses:Groceries USD
2000-01-01 open Expenses:FIXME USD

2020-01-01 * "Shop" "Weekly shop" #food ^inv-1
  ref: "A-1"
  Assets:Bank -30 USD
    source_desc: "SHOP 1"
  Expenses:Groceries 20 USD
    categorizer: "jev"
    jev_confidence: 0.95
    jev_account: "Expenses:Groceries"
  Expenses:Coffee
    categorizer: "rules"

2020-01-02 * "Cafe" "Unknown"
  Assets:Bank -4 USD
  Expenses:FIXME 4 USD
"""


def journal_transactions(loaded, narration):
    return [e for e in loaded.editor.entries
            if isinstance(e, data.Transaction) and e.narration == narration]


def review(tmp_path, narration):
    loaded, journal = loaded_reconciler(tmp_path, JOURNAL, [])
    [txn] = journal_transactions(loaded, narration)
    candidates = loaded.make_review_candidates(txn)
    return loaded, journal, candidates


def check_journal(journal):
    entries, errors, _ = loader.load_file(str(journal))
    assert errors == []
    return entries


def test_category_postings_are_prefilled_groups(tmp_path):
    loaded, _, candidates = review(tmp_path, 'Weekly shop')
    [candidate] = candidates.candidates
    assert [(s.account_name, s.group_number, s.predicted_account_name)
            for s in candidate.substituted_accounts] == [
        ('Expenses:Groceries', 0, 'Expenses:Groceries'),
        ('Expenses:Coffee', 1, 'Expenses:Coffee')]
    # The source posting stays fixed; a non-source asset is editable.
    assert not loaded.is_category_posting(candidate.used_transactions[0].postings[0])
    assert loaded.is_category_posting(data.Posting('Assets:Cash', None, None, None, None, None))
    assert candidates.date.isoformat() == '2020-01-01'


def test_unchanged_confirm_leaves_file_identical(tmp_path):
    loaded, journal, candidates = review(tmp_path, 'Weekly shop')
    before = journal.read_bytes()
    loaded.accept_candidate(candidates.candidates[0])
    assert journal.read_bytes() == before
    assert len(journal_transactions(loaded, 'Weekly shop')) == 1


def test_change_rewrites_only_changed_lines(tmp_path):
    loaded, journal, candidates = review(tmp_path, 'Weekly shop')
    before = journal.read_text().splitlines()
    candidates.change_transaction(0, {
        'accounts': ['Expenses:Coffee', 'Expenses:Coffee'],
        'narration': 'Weekly groceries'})
    loaded.accept_candidate(candidates.candidates[0])
    after = journal.read_text().splitlines()
    changes = [line for line in difflib.ndiff(before, after) if line[:1] in '+-']
    assert changes == [
        '- 2020-01-01 * "Shop" "Weekly shop" #food ^inv-1',
        '+ 2020-01-01 * "Shop" "Weekly groceries" #food ^inv-1',
        '-   Expenses:Groceries 20 USD',
        '+   Expenses:Coffee    20 USD',
        '-     categorizer: "jev"',
        '-     jev_confidence: 0.95',
        '-     jev_account: "Expenses:Groceries"',
    ]
    # Unchanged postings keep their categorizer; no duplicate.
    [txn] = [e for e in check_journal(journal)
             if isinstance(e, data.Transaction) and e.payee == 'Shop']
    assert txn.narration == 'Weekly groceries'
    assert txn.tags == {'food'} and txn.links == {'inv-1'} and txn.meta['ref'] == 'A-1'
    assert txn.postings[2].meta['categorizer'] == 'rules'
    assert len(journal_transactions(loaded, 'Weekly groceries')) == 1
    assert journal_transactions(loaded, 'Weekly shop') == []


def test_reviewed_fixme_transaction_leaves_pending(tmp_path):
    loaded, journal, candidates = review(tmp_path, 'Unknown')
    assert [p.entries[0].narration for p in loaded.pending_data] == ['Unknown']
    [substitution] = candidates.candidates[0].substituted_accounts
    assert substitution.unknown_account_name == 'Expenses:FIXME'
    candidates.change_transaction(0, {'accounts': ['Expenses:Coffee']})
    loaded.accept_candidate(candidates.candidates[0])
    assert loaded.pending_data == []
    texts = [e for e in check_journal(journal)
             if isinstance(e, data.Transaction) and e.narration == 'Unknown']
    assert len(texts) == 1 and texts[0].postings[1].account == 'Expenses:Coffee'


COMMENTED_JOURNAL = """
2000-01-01 open Assets:Bank USD
2000-01-01 open Expenses:Coffee
2000-01-01 open Expenses:Groceries
2000-01-01 open Expenses:Travel

2020-01-01 * "Shop" "Commented"
  ; whole transaction note
  ref: "A-1"
  Assets:Bank                -30.00 USD
    ; bank note
  Expenses:Groceries          20.00 USD ; inline note
    categorizer: "jev"
  ; between postings
  Expenses:Groceries
  ; trailing note

2020-01-02 * "Hotel" "Priced"
  Assets:Bank       -9.23 USD
  Expenses:Groceries  8.50 EUR @ 1.0859 USD
"""


def test_comment_lines_and_account_only_changes_keep_text(tmp_path):
    loaded, journal = loaded_reconciler(tmp_path, COMMENTED_JOURNAL, [])
    before = journal.read_text()

    def edit(narration, accounts):
        [txn] = journal_transactions(loaded, narration)
        candidates = loaded.make_review_candidates(txn)
        if accounts:
            candidates.change_transaction(0, {'accounts': accounts})
        loaded.accept_candidate(candidates.candidates[0])

    # Comment lines inside the transaction: opens, unchanged Confirm is a no-op.
    edit('Commented', None)
    assert journal.read_text() == before

    # Only the account names change; amounts (elided too), alignment, inline
    # and whole-line comments stay.
    edit('Commented', ['Expenses:Coffee', 'Expenses:Travel'])
    edit('Priced', ['Expenses:Travel'])
    expected = before.replace(
        '  Expenses:Groceries          20.00 USD ; inline note\n'
        '    categorizer: "jev"\n',
        '  Expenses:Coffee             20.00 USD ; inline note\n').replace(
        '  Expenses:Groceries\n  ; trailing', '  Expenses:Travel\n  ; trailing').replace(
        '  Expenses:Groceries  8.50 EUR', '  Expenses:Travel     8.50 EUR')
    assert journal.read_text() == expected
    check_journal(journal)


SPELLING_JOURNAL = """
2000-01-01 open Assets:Bank USD
2000-01-01 open Expenses:Coffee
2000-01-01 open Expenses:Groceries

2020-01-01 txn "Shop; Ltd" "Header note" #food ^inv-1 ; kept note
  Assets:Bank\t-30.00 USD
  Expenses:Groceries\t30.00 USD
"""


def test_header_spelling_and_tab_kept(tmp_path):
    loaded, journal = loaded_reconciler(tmp_path, SPELLING_JOURNAL, [])
    before = journal.read_text()
    [txn] = journal_transactions(loaded, 'Header note')
    candidates = loaded.make_review_candidates(txn)
    candidates.change_transaction(0, {
        'accounts': ['Expenses:Coffee'], 'narration': 'Edited'})
    loaded.accept_candidate(candidates.candidates[0])
    assert journal.read_text() == before.replace(
        '"Header note" #food ^inv-1 ; kept', '"Edited" #food ^inv-1 ; kept').replace(
        'Expenses:Groceries\t', 'Expenses:Coffee\t')
    [txn] = [e for e in check_journal(journal)
             if isinstance(e, data.Transaction)]
    assert txn.payee == 'Shop; Ltd' and txn.narration == 'Edited'


def test_unchanged_confirm_keeps_crlf_file(tmp_path):
    loaded, journal = loaded_reconciler(
        tmp_path, JOURNAL.replace('\n', '\r\n'), [])
    before = journal.read_bytes()
    assert b'\r\n' in before
    [txn] = journal_transactions(loaded, 'Weekly shop')
    loaded.accept_candidate(loaded.make_review_candidates(txn).candidates[0])
    assert journal.read_bytes() == before


def test_rerender_header_line():
    from .journal_editor import rerender_header_line
    printed = '2020-01-01 * "A" "B"'
    assert rerender_header_line('2020-01-01 txn "A" "x"  ; c', printed) == (
        '2020-01-01 txn "A" "B"  ; c')
    assert rerender_header_line('2020-01-01 * "A;" "x"', printed) == printed
    assert rerender_header_line('2020-01-01 * "a\\"b; c" "x"', printed) == printed
    assert rerender_header_line('2020-01-01 ! "A" "x"', printed) == printed
    assert rerender_header_line('2020-01-01 * "A" "multi', printed) == printed
    assert rerender_header_line(None, printed) == printed


HTTP_JOURNAL = """
2000-01-01 open Assets:Bank USD
2000-01-01 open Expenses:Coffee USD
2000-01-01 open Expenses:Groceries USD
2000-01-01 open Expenses:FIXME USD
""" + ''.join("""
2020-01-0%d * "Shop" "Row %d"
  Assets:Bank -%d USD
  Expenses:Groceries %d USD
""" % (day, day, day, day) for day in range(1, 4)) + """
2020-01-04 * "Cafe" "Pending fixme"
  Assets:Bank -4 USD
  Expenses:FIXME 4 USD
"""

GROCERIES = {'search': 'groceries'}


def load_fake_sources(self):
    self.sources = [FakeSource([])]


class ReviewHTTPTest(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        self.directory = tempfile.TemporaryDirectory()
        self.journal = '%s/journal.bean' % self.directory.name
        ignored = '%s/ignored.bean' % self.directory.name
        with open(self.journal, 'w') as f:
            f.write(HTTP_JOURNAL)
        open(ignored, 'w').close()
        # Assets:Bank belongs to a data source, so only the expense is edited.
        self.sources = patch.object(reconcile.LoadedReconciler, '_load_sources',
                                    load_fake_sources)
        self.sources.start()
        self.app = webserver.Application(webserver.parse_arguments(
            [], journal_input=self.journal, ignored_journal=ignored,
            default_output=self.journal), ioloop=self.io_loop)
        return self.app

    def tearDown(self):
        if self.app.check_modification_observer is not None:
            self.app.check_modification_observer.stop()
        self.sources.stop()
        super().tearDown()
        self.directory.cleanup()

    async def generation(self):
        for _ in range(500):
            generation = self.app.current_state.get('journal_generation')
            if generation is not None:
                return generation
            await asyncio.sleep(.01)
        raise AssertionError('timeout')

    async def post(self, operation, **msg):
        response = await self.http_client.fetch(
            self.get_url('/%s/review/%s' % (self.app.secret_key, operation)),
            method='POST', body=json.dumps(msg), raise_error=False)
        return response.code, json.loads(response.body)

    def lineno(self, narration):
        with open(self.journal) as f:
            lines = f.read().split('\n')
        return next(i + 1 for i, line in enumerate(lines) if '"%s"' % narration in line)

    async def open(self, narration, flt=GROCERIES):
        code, body = await self.post(
            'open', generation=await self.generation(), filter=flt,
            filename=os.path.realpath(self.journal), lineno=self.lineno(narration))
        assert (code, body) == (200, None)
        return self.app.current_state['review']

    def confirm(self, flt=GROCERIES, **msg):
        state = self.app.current_state
        return self.app.handle_select_candidate({
            'index': 0, 'generation': state['candidates_generation'],
            'review_filter': flt, **msg})

    def change(self, accounts):
        self.app.handle_change_candidate({
            'generation': self.app.current_state['candidates_generation'],
            'candidate_index': 0, 'changes': {'accounts': accounts}})

    def journal_text(self):
        with open(self.journal) as f:
            return f.read()

    def review_narration(self):
        return self.app.review and self.app.review.narration

    @tornado.testing.gen_test
    async def test_open_errors(self):
        generation = await self.generation()
        filename = os.path.realpath(self.journal)
        ok = dict(generation=generation, filter={}, filename=filename,
                  lineno=self.lineno('Row 1'))
        assert (await self.post('open', **{**ok, 'lineno': 1}))[0] == 404
        assert (await self.post('open', **{**ok, 'filename': 'other.bean'}))[0] == 404
        assert (await self.post('open', **{**ok, 'generation': generation + 1}))[0] == 409
        assert (await self.post('open', **{**ok, 'filter': {'start': 'x'}}))[0] == 400
        assert (await self.post('open', **{**ok, 'filter': None}))[0] == 400
        assert (await self.post('open', **{**ok, 'move': 'sideways'}))[0] == 400
        # previous/next need the transaction under review.
        assert (await self.post('open', **{**ok, 'move': 'next'}))[0] == 409
        loaded = self.app.reconciler.loaded_future.result()
        with patch.object(loaded, 'make_review_candidates', side_effect=AssertionError):
            code, body = await self.post('open', **ok)
        assert code == 422 and 'cannot be rewritten line by line' in body['error']
        self.app.jev_run = {'running': True}
        code, body = await self.post('open', **ok)
        assert code == 409 and body == {'error': webserver.BATCH_RUNNING}
        assert self.app.review is None and self.app.current_state.get('review') is None

    @tornado.testing.gen_test
    async def test_open_keeps_pending_position_and_close_returns(self):
        await self.generation()
        pending_candidates = self.app.next_candidates
        pending_index = self.app.current_state['pending_index']
        assert await self.open('Row 2') == {
            'filename': os.path.realpath(self.journal), 'lineno': self.lineno('Row 2'),
            'offset': 1, 'count': 3}
        assert self.app.next_candidates is not pending_candidates
        assert self.app.current_state['pending_index'] == pending_index
        [s] = self.app.next_candidates.candidates[0].substituted_accounts
        assert s.account_name == 'Expenses:Groceries'
        # Not in the filtered list: no offset.
        assert (await self.open('Pending fixme'))['offset'] is None

        assert (await self.post('close'))[0] == 200
        assert self.app.review is None and self.app.current_state.get('review') is None
        assert self.app.current_state['pending_index'] == pending_index
        assert self.app.next_candidates.candidates[0].used_transactions[0].narration == \
            'Pending fixme'

    @tornado.testing.gen_test
    async def test_moves(self):
        generation = await self.generation()
        await self.open('Row 3')

        async def move(name):
            code, _ = await self.post('open', generation=generation, filter=GROCERIES,
                                      move=name)
            return code, self.review_narration(), self.app.current_state['review']['offset']

        assert await move('next') == (200, 'Row 2', 1)
        assert await move('next') == (200, 'Row 1', 2)
        assert await move('next') == (404, 'Row 1', 2)
        assert await move('previous') == (200, 'Row 2', 1)
        assert await move('first') == (200, 'Row 3', 0)
        assert await move('previous') == (404, 'Row 3', 0)
        assert await move('last') == (200, 'Row 1', 2)

    @tornado.testing.gen_test
    async def test_confirm_opens_next_row(self):
        await self.open('Row 3')
        old_generation, old_lineno = await self.generation(), self.lineno('Row 2')
        self.change(['Expenses:Coffee'])
        self.confirm()
        # A row of the list shown before Confirm is stale.
        code, _ = await self.post('open', generation=old_generation, filter=GROCERIES,
                                  filename=os.path.realpath(self.journal), lineno=old_lineno)
        assert code == 409
        # Changed in place, the next row of the list is open.
        assert self.journal_text() == HTTP_JOURNAL.replace(
            'Expenses:Groceries 3 USD', 'Expenses:Coffee    3 USD')
        assert self.review_narration() == 'Row 2'
        # Row 3 dropped out of the "groceries" list, so Row 2 is first now.
        assert self.app.current_state['review'] == {
            'filename': os.path.realpath(self.journal), 'lineno': self.lineno('Row 2'),
            'offset': 0, 'count': 2}

        # Unchanged: the file stays, the next row opens.
        before = self.journal_text()
        self.confirm()
        assert self.journal_text() == before
        assert self.review_narration() == 'Row 1'
        assert self.app.current_state['review']['offset'] == 1

        # The last row: back to the pending flow.
        self.confirm()
        assert self.app.review is None and self.app.current_state.get('review') is None
        assert self.app.next_candidates.candidates[0].used_transactions[0].narration == \
            'Pending fixme'

    @tornado.testing.gen_test
    async def test_confirm_without_filter_returns_to_pending(self):
        await self.open('Row 3')
        self.confirm(flt=None)
        assert self.app.review is None

    @tornado.testing.gen_test
    async def test_ignore_refused_and_other_actions_end_review(self):
        await self.open('Row 3')
        before = self.journal_text()
        assert self.confirm(ignore=True) is None
        assert self.journal_text() == before
        assert self.review_narration() == 'Row 3'

        state = self.app.current_state
        self.app.handle_skip({'generation': state['pending'][0], 'index': 0})
        assert self.app.review is None and self.app.current_state.get('review') is None

        await self.open('Row 3')
        self.app.handle_accept_rules()  # nothing to accept, review stays
        assert self.review_narration() == 'Row 3'
        loaded = self.app.reconciler.loaded_future.result()
        with patch.object(loaded, 'accept_rules_candidates', return_value=[
                reconcile.AcceptCandidateResult([], [])]):
            self.app.handle_accept_rules()
        assert self.app.review is None

        await self.open('Row 3')
        self.app.set_state(jev_enabled=True)
        with patch.object(webserver.jev, 'classify', return_value={
                'category': 'Expenses:Coffee', 'confidence': .1}):
            self.app.start_jev_batch('ai', 1)
            assert self.app.review is None
            while self.app.jev_running():
                await asyncio.sleep(.01)

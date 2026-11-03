import asyncio
import datetime
import json
import os
import tempfile
import urllib.parse
from unittest.mock import patch

import pytest
import tornado.testing
from beancount import loader

from . import transactions, webserver
from .jev_batch_test import BATCH_JOURNAL

JOURNAL = '''
2000-01-01 open Assets:Bank USD
2000-01-01 open Expenses:Coffee USD
2000-01-01 open Expenses:Food:Groceries USD
2000-01-01 open Expenses:Rent USD

2026-01-31 * "Coffee+Bar" "Flat white"
  Assets:Bank -4 USD
  Expenses:Coffee 4 USD
    categorizer: "rules"

2026-02-01 * "Landlord" "February rent"
  ref: "INV-77/2"
  Assets:Bank -900 USD
    source_desc: "SEPA Transfer"
  Expenses:Rent 900 USD

2026-02-01 * "Market" "Weekly shop"
  Assets:Bank -30 USD
  Expenses:Coffee 10 USD
    categorizer: "jev"
  Expenses:Food:Groceries 20 USD
    categorizer: "rules"

2026-02-28 * "Bakery" "Bread"
  Assets:Bank -3 USD
  Expenses:Food:Groceries 3 USD
'''


@pytest.fixture(scope='module')
def index():
    with tempfile.NamedTemporaryFile('w', suffix='.bean') as f:
        f.write(JOURNAL)
        f.flush()
        entries, errors, _ = loader.load_file(f.name)
        assert errors == []
        return transactions.Index(entries)


def narrations(index, **args):
    return [t.narration for t in index.filter(transactions.parse_filter(args))]


def test_newest_first_and_categorizers(index):
    assert narrations(index) == ['Bread', 'Weekly shop', 'February rent', 'Flat white']
    assert index.categorizers == ['jev', 'rules']


def test_dates_inclusive_and_open(index):
    assert narrations(index, start='2026-02-01', end='2026-02-01') == [
        'Weekly shop', 'February rent']
    assert narrations(index, start='2026-02-01') == ['Bread', 'Weekly shop', 'February rent']
    assert narrations(index, end='2026-01-31') == ['Flat white']
    assert narrations(index, start='', end='') == narrations(index)


def test_categorizer(index):
    assert narrations(index, categorizer='rules') == ['Weekly shop', 'Flat white']
    assert narrations(index, categorizer='jev') == ['Weekly shop']
    assert narrations(index, categorizer='') == ['Bread', 'February rent']
    assert narrations(index, categorizer='other') == []


@pytest.mark.parametrize('search, expected', [
    ('landlord', ['February rent']),          # payee
    ('FEBRUARY', ['February rent']),           # narration, case
    ('inv 77 2', ['February rent']),           # transaction meta, punctuation
    ('sepa-transfer', ['February rent']),      # posting meta
    ('food:groceries', ['Bread', 'Weekly shop']),  # account
    ('coffee bar', ['Flat white']),
    ('', ['Bread', 'Weekly shop', 'February rent', 'Flat white']),
    ('bean', []),                              # file name is not searchable
])
def test_search(index, search, expected):
    assert narrations(index, search=search) == expected


def test_combined_filters(index):
    assert narrations(index, start='2026-02-01', categorizer='rules', search='market') == [
        'Weekly shop']


@pytest.mark.parametrize('args', [
    {'start': '2026-13-01'}, {'end': 'yesterday'}, {'start': '2026-02-02', 'end': '2026-02-01'}])
def test_parse_filter_errors(args):
    with pytest.raises(ValueError):
        transactions.parse_filter(args)


def test_encode_item(index):
    item = transactions.encode_item(index.transactions[2])
    assert item['date'] == '2026-02-01'
    assert item['formatted'].startswith('2026-02-01 * "Landlord" "February rent"')
    assert 'ref: "INV-77/2"' in item['formatted']
    assert item['filename'].endswith('.bean')
    assert item['lineno'] == 12


class TransactionsHTTPTest(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        self.directory = tempfile.TemporaryDirectory()
        self.journal = '%s/journal.bean' % self.directory.name
        ignored = '%s/ignored.bean' % self.directory.name
        with open(self.journal, 'w') as f:
            f.write(BATCH_JOURNAL)
        with open(ignored, 'w') as f:
            f.write('2020-01-05 * "Ignored" "Ignored one"\n'
                    '  Assets:Bank -1 USD\n  Expenses:Coffee 1 USD\n')
        self.app = webserver.Application(webserver.parse_arguments(
            [], journal_input=self.journal, ignored_journal=ignored,
            default_output=self.journal), ioloop=self.io_loop)
        return self.app

    def tearDown(self):
        if self.app.check_modification_observer is not None:
            self.app.check_modification_observer.stop()
        super().tearDown()
        self.directory.cleanup()

    async def generation(self, previous=None):
        for _ in range(500):
            generation = self.app.current_state.get('journal_generation')
            if generation is not None and generation != previous:
                return generation
            await asyncio.sleep(.01)
        raise AssertionError('timeout')

    async def get(self, **args):
        query = urllib.parse.urlencode(args)
        response = await self.http_client.fetch(
            self.get_url('/%s/transactions?%s' % (self.app.secret_key, query)),
            raise_error=False)
        return response.code, json.loads(response.body)

    @tornado.testing.gen_test
    async def test_pages_errors_and_refresh(self):
        generation = await self.generation()
        # Nothing is computed until the tab asks.
        assert self.app.transactions_cache is None

        code, body = await self.get(generation=generation, limit=0)
        assert code == 200
        # The ignored journal is not listed.
        assert body == {'count': 6, 'categorizers': [], 'items': []}
        code, body = await self.get(generation=generation, search='ignored', limit=5)
        assert body['count'] == 0
        # `limit` is capped.
        code, body = await self.get(generation=generation, limit=1000000)
        assert code == 200 and len(body['items']) == 6
        with patch.object(transactions, 'encode_item', return_value={}) as enc:
            await self.get(generation=generation, limit=1000000)
            assert enc.call_count <= webserver.MAX_TRANSACTIONS_LIMIT
        code, body = await self.get(generation=generation, start='2020-02-01',
                                    offset=1, limit=2)
        assert body['count'] == 5
        assert [i['date'] for i in body['items']] == ['2020-02-04', '2020-02-03']
        assert 'Pending 4' in body['items'][0]['formatted']
        assert body['items'][0]['filename'] == os.path.realpath(self.journal)

        # The index and the last result are cached.
        index = self.app.transactions_cache[1]
        with patch.object(transactions.Index, 'filter',
                          side_effect=AssertionError('recomputed')):
            assert (await self.get(generation=generation, start='2020-02-01',
                                   limit=1))[0] == 200
        assert self.app.transactions_cache[1] is index

        assert (await self.get(generation=generation + 1))[0] == 404
        for bad in ({'start': 'x'}, {'offset': -1}, {'limit': 'a'},
                    {'start': '2020-02-02', 'end': '2020-02-01'}):
            assert (await self.get(generation=generation, **bad))[0] == 400
        assert (await self.get())[0] == 400

        # An accepted transaction bumps the generation and shows up.
        loaded = self.app.reconciler.loaded_future.result()
        pending = loaded.get_ai_pending()[0]
        loaded.accept_ai_candidate(pending.entries[0], 'Expenses:Coffee', .95)
        self.app.get_next_candidates(new_pending=True)
        new_generation = await self.generation(generation)
        assert (await self.get(generation=generation))[0] == 404
        code, body = await self.get(generation=new_generation, categorizer='jev', limit=10)
        assert body['count'] == 1 and body['categorizers'] == ['jev']
        assert 'Pending 1' in body['items'][0]['formatted']
        code, body = await self.get(generation=new_generation, categorizer='', limit=0)
        assert body['count'] == 5

    @tornado.testing.gen_test
    async def test_reload_resets_generation(self):
        generation = await self.generation()
        assert (await self.get(generation=generation, limit=0))[1]['count'] == 6
        with open(self.journal, 'a') as f:
            f.write('\n2020-03-01 * "New" "Outside edit"\n'
                    '  Assets:Bank -1 USD\n  Expenses:Coffee 1 USD\n')
        self.app.check_modification()
        assert self.app.current_state['journal_generation'] is None
        assert self.app.transactions_cache is None
        new_generation = await self.generation()
        code, body = await self.get(generation=new_generation, search='outside', limit=5)
        assert body['count'] == 1
        assert body['items'][0]['date'] == datetime.date(2020, 3, 1).isoformat()

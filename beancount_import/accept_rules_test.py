import datetime
from decimal import Decimal

from beancount.core.amount import Amount
from beancount.core.data import Posting, Transaction

from . import reconcile, webserver
from .source import ImportResult, Source

JOURNAL = """
2000-01-01 open Assets:Bank USD
2000-01-01 open Expenses:Coffee USD
2000-01-01 open Expenses:FIXME USD

2020-01-05 * "Manual coffee"
  Assets:Bank -4 USD
  Expenses:Coffee 4 USD
"""


def posting(account, number, meta=None):
    return Posting(account, Amount(Decimal(number), 'USD'), None, None, None, meta)


def transaction(day, narration, number, account, meta=None):
    return Transaction(
        meta={}, date=datetime.date(2020, 1, day), flag='*', payee=None,
        narration=narration, tags=frozenset(), links=frozenset(), postings=[
            posting('Assets:Bank', -number, {'source_desc': narration}),
            posting(account, number, meta)])


class FakeSource(Source):
    def __init__(self, transactions):
        super().__init__(log_status=lambda _: None)
        self.transactions = transactions

    @property
    def name(self):
        return 'fake'

    def prepare(self, journal, results):
        results.add_account('Assets:Bank')
        for entry in self.transactions:
            results.add_pending_entry(ImportResult(date=entry.date, entries=[entry], info=None))

    def is_posting_cleared(self, posting):
        return bool(posting.meta and 'source_desc' in posting.meta)


def loaded_reconciler(tmp_path, journal_text, transactions, **options):
    journal = tmp_path / 'journal.bean'
    journal.write_text(journal_text)
    ignored = tmp_path / 'ignored.bean'
    ignored.touch()
    options = vars(webserver.parse_arguments(
        [], journal_input=str(journal), ignored_journal=str(ignored),
        default_output=str(journal), data_sources=[], **options))
    owner = reconcile.Reconciler(str(journal), lambda _: None, str(ignored), options)
    owner.loaded_future.result()
    loaded = reconcile.LoadedReconciler(owner, sources=[FakeSource(transactions)])
    return loaded, journal


def test_accept_rules_candidates(tmp_path):
    rules = {'categorizer': 'rules'}
    loaded, journal = loaded_reconciler(tmp_path, JOURNAL, [
        transaction(1, 'Rules coffee', 3, 'Expenses:Coffee', rules),
        transaction(2, 'Other rules', 6, 'Expenses:Coffee', {'categorizer': 'jev'}),
        transaction(3, 'Unknown', 7, 'Expenses:FIXME', rules),
        transaction(4, 'Uncategorized', 8, 'Expenses:Coffee'),
        # Matches the manual transaction, so it needs review.
        transaction(5, 'Duplicate coffee', 4, 'Expenses:Coffee', rules),
    ])
    assert len(loaded.pending_data) == 5
    assert loaded.count_rules_pending() == 1

    results = loaded.accept_rules_candidates()

    assert len(results) == 1
    assert loaded.count_rules_pending() == 0
    assert [p.entries[0].narration for p in loaded.pending_data] == [
        'Other rules', 'Unknown', 'Uncategorized', 'Duplicate coffee']
    text = journal.read_text()
    assert '"Rules coffee"' in text and 'categorizer: "rules"' in text
    assert '"Duplicate coffee"' not in text

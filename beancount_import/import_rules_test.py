import datetime
from decimal import Decimal

from beancount.core.amount import Amount
from beancount.core.data import Posting, Transaction

from . import import_rules
from .accept_rules_test import loaded_reconciler

JOURNAL = """
2000-01-01 open Assets:Bank USD
2000-01-01 open Expenses:Coffee USD
2000-01-01 open Expenses:Music USD
2000-01-01 open Expenses:FIXME USD
"""

RULES = """
x-auto-confirm: &auto-confirm
  addPostingMeta:
    categorizer: rules

rules:
  - <<: *auto-confirm
    filter: 'payee:"^Spotify AB$"'
    setAccount: Expenses:Music
    setNarration: Spotify
    addTags: "#recurring"
    addMeta: {subscriptionName: Spotify}

  - filter: 'payee:"spotify"'
    setAccount: Expenses:Coffee
    addTags: "#music"

  - filter: "payee:'^IP \\"OSPANOVA\\"$'"
    setAccount: Expenses:Coffee
"""


def transaction(day, payee, number):
    return Transaction(
        meta={}, date=datetime.date(2020, 1, day), flag='*', payee=payee,
        narration='raw', tags=frozenset(), links=frozenset(), postings=[
            Posting('Assets:Bank', Amount(Decimal(-number), 'USD'), None, None,
                    None, {'source_desc': 'raw'}),
            Posting('Expenses:FIXME', Amount(Decimal(number), 'USD'), None,
                    None, None, {}),
        ])


def load(tmp_path, text):
    path = tmp_path / 'rules.yaml'
    path.write_text(text)
    return str(path)


def test_apply_rules(tmp_path):
    rules = import_rules.load_rules(load(tmp_path, RULES))

    txn, warnings = import_rules.apply_rules(rules, transaction(1, 'Spotify AB', 10))
    assert (txn.narration, txn.tags) == ('Spotify', {'recurring', 'music'})
    assert txn.meta['subscriptionName'] == 'Spotify'
    assert txn.postings[0].meta == {'source_desc': 'raw'}
    assert txn.postings[1].account == 'Expenses:Music'
    assert txn.postings[1].meta == {'categorizer': 'rules'}
    assert warnings == [
        'Spotify AB: rules 1 and 2 disagree on setAccount, using '
        "'Expenses:Music'"]

    txn, warnings = import_rules.apply_rules(rules, transaction(1, 'IP "OSPANOVA"', 5))
    assert txn.postings[1].account == 'Expenses:Coffee'
    assert txn.postings[1].meta == {}
    assert warnings == []

    grouped = transaction(1, 'IP "OSPANOVA"', 5)
    grouped.postings[1] = grouped.postings[1]._replace(account='Expenses:FIXME:A')
    txn, _ = import_rules.apply_rules(rules, grouped)
    assert txn.postings[1].account == 'Expenses:Coffee'

    original = transaction(1, 'Nobody', 1)
    assert import_rules.apply_rules(rules, original) == (original, [])


def test_invalid_rules(tmp_path):
    for text in ('rule: []', 'rules: [{filter: x, setCategory: A}]',
                 'rules: [{filter: x, addPostingMeta: {a: b}}]',
                 'rules: [{setAccount: A}]'):
        try:
            import_rules.load_rules(load(tmp_path, text))
        except ValueError:
            continue
        raise AssertionError(text)


def loaded_with_rules(tmp_path, rules_text):
    return loaded_reconciler(
        tmp_path, JOURNAL, [transaction(1, 'Spotify AB', 10),
                            transaction(2, 'IP "OSPANOVA"', 5),
                            transaction(3, 'Nobody', 1)],
        import_rules=load(tmp_path, rules_text))


def test_rules_in_reconciler(tmp_path):
    loaded, journal = loaded_with_rules(tmp_path, RULES)
    assert [p.entries[0].postings[1].account for p in loaded.pending_data] == [
        'Expenses:Music', 'Expenses:Coffee', 'Expenses:FIXME']
    assert [e[0] for e in loaded.errors] == ['warning']
    # Only the rule with the auto-confirm anchor marks the posting.
    assert loaded.count_rules_pending() == 1
    loaded.accept_rules_candidates()
    text = journal.read_text()
    assert '"Spotify AB" "Spotify" #music #recurring' in text
    assert 'categorizer: "rules"' in text and 'source_desc: "raw"' in text


def test_invalid_rules_in_reconciler(tmp_path):
    loaded, _ = loaded_with_rules(tmp_path, 'rules: [{filter: "payee:("}]')
    assert [e[0] for e in loaded.errors] == ['error']
    assert loaded.errors[0][1].startswith('Import rules disabled:')
    assert [p.entries[0].postings[1].account for p in loaded.pending_data] == [
        'Expenses:FIXME'] * 3

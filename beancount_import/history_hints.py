"""Candidate context and local history hints.

History matching adapted from fava-import-history-hints by viktor-235.
"""
import hashlib
import os
import re
from collections import Counter, defaultdict
from decimal import Decimal

from beancount.core import data
from beancount.parser.printer import EntryPrinter

from . import jev


def candidate_snapshot(candidate):
    """Restore unknown accounts in a copy; preserve edits and merged postings."""
    substitutions = {s.unique_name: s for s in candidate.substituted_accounts or []}
    transactions = [e for e in candidate.staged_changes_with_unique_account_names.get_diff().new_entries
                    if isinstance(e, data.Transaction)]
    if len(transactions) != 1:
        return None
    transaction = transactions[0]
    groups = {}
    postings = []
    for index, posting in enumerate(transaction.postings):
        substitution = substitutions.get(posting.account)
        if substitution is not None:
            posting = posting._replace(account=substitution.unknown_account_name)
            group = groups.setdefault(substitution.group_number, {
                "number": substitution.group_number, "posting_indices": [], "postings": []})
            group["posting_indices"].append(index)
            group["postings"].append(str(posting.units) + " · " + posting.account)
        postings.append(posting)
    transaction = transaction._replace(postings=postings)
    raw = EntryPrinter()(transaction).rstrip()
    # No random substitution IDs or predicted accounts: applying a suggestion
    # must not invalidate the answer to the original question.
    token = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return transaction, {"token": token, "payee": transaction.payee or "",
                         "transaction": raw, "groups": list(groups.values())}


def origin(entry):
    meta = entry.meta or {}
    filename, lineno = meta.get("filename"), meta.get("lineno")
    return (os.path.realpath(filename), lineno) if filename and lineno is not None else None


def history_entries(editor, candidate):
    excluded = {origin(t) for t in candidate.used_transactions}
    excluded.discard(None)
    return [e for e in editor.history_entries
            if not isinstance(e, data.Transaction) or origin(e) not in excluded]


def normalize(value):
    return " ".join(re.sub(r"[^\w]+", " ", (value or "").casefold(), flags=re.UNICODE).split())


def searchable_parts(txn):
    source_desc = next((str(p.meta["source_desc"]) for p in txn.postings
                        if p.meta and p.meta.get("source_desc")), "")
    parts = [txn.payee or "", txn.narration or "", source_desc]
    if txn.meta:
        parts.extend(str(value) for value in txn.meta.values())
    for posting in txn.postings:
        if posting.meta:
            parts.extend(str(value) for value in posting.meta.values())
    return parts


def summarize(entries, query):
    needle = normalize(query)
    counts = Counter()
    narrations = defaultdict(Counter)
    matches = 0
    for txn in entries:
        if not isinstance(txn, data.Transaction):
            continue
        if needle and needle not in normalize(" ".join(searchable_parts(txn))):
            continue
        matches += 1
        # Unknown accounts stay visible: past unclear bookings are useful context.
        for posting in txn.postings:
            account = posting.account
            if account.startswith(("Expenses:", "Income:")):
                counts[account] += 1
                if txn.narration:
                    narrations[account][txn.narration] += 1
    return {"transaction_count": matches, "accounts": [
        {"account": account, "count": count, "narrations": [
            {"narration": narration, "count": n}
            for narration, n in narrations[account].most_common(8)]}
        for account, count in counts.most_common()]}


def build_context(editor, candidate, group_number, account_filters=None):
    snapshot = candidate_snapshot(candidate)
    if snapshot is None:
        raise jev.JevError("Select a transaction candidate.", 422)
    transaction, info = snapshot
    group = next((g for g in info["groups"] if g["number"] == group_number), None)
    if group is None or type(group_number) is not int:
        raise jev.JevError("Select an editable posting group.", 422)
    is_category = jev.account_filter(account_filters)
    context = jev.build_context(history_entries(editor, candidate), transaction,
                                EntryPrinter()(transaction).rstrip(), is_category)
    context["target_group"] = {"number": group_number,
                               "posting_indices": group["posting_indices"],
                               "postings": group["postings"]}
    direction = _direction([transaction.postings[i] for i in group["posting_indices"]])
    if direction:
        context["target_group"]["direction"] = direction
    return context


def _direction(postings):
    """"money out" if the target postings are debited (spending or a transfer
    out of the source account), "money in" if credited, None otherwise."""
    numbers = [p.units.number if p.units is not None else None for p in postings]
    if not numbers or not all(isinstance(n, Decimal) and n for n in numbers):
        return None
    signs = {n > 0 for n in numbers}
    if len(signs) != 1:
        return None
    return "money out" if signs.pop() else "money in"

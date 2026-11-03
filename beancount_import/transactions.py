"""Journal transactions for the Transactions tab: filtering and encoding."""

import datetime
from typing import Any, Dict, Iterable, List, Mapping, NamedTuple, Optional

from beancount.core import data

from . import history_hints
from .reconcile import CATEGORIZER_KEY

# Location metadata is not searchable: every path would match its directory.
_HIDDEN_META = ('filename', 'lineno')


class Filter(NamedTuple):
    start: Optional[datetime.date]
    end: Optional[datetime.date]
    # None: all transactions; '': no posting has a categorizer; else a value.
    categorizer: Optional[str]
    # Normalized with `history_hints.normalize`.
    search: str


def _parse_date(value: str) -> Optional[datetime.date]:
    return datetime.date.fromisoformat(value) if value else None


def parse_filter(args: Mapping[str, str]) -> Filter:
    """Builds a filter from query arguments; raises ValueError on bad input.

    Dates are YYYY-MM-DD or empty (open bound). An absent `categorizer`
    means all transactions, an empty one means none.
    """
    if not isinstance(args, Mapping) or not all(isinstance(v, str) for v in args.values()):
        raise ValueError('expected an object of strings')
    result = Filter(start=_parse_date(args.get('start', '')),
                    end=_parse_date(args.get('end', '')),
                    categorizer=args.get('categorizer'),
                    search=history_hints.normalize(args.get('search', '')))
    if result.start and result.end and result.start > result.end:
        raise ValueError('start is after end')
    return result


def _meta_values(meta: Optional[Dict[str, Any]]) -> List[str]:
    return [str(value) for key, value in (meta or {}).items()
            if key not in _HIDDEN_META and not key.startswith('__')]


def search_text(txn: data.Transaction) -> str:
    parts = [txn.payee or '', txn.narration or ''] + _meta_values(txn.meta)
    for posting in txn.postings:
        parts.append(posting.account)
        parts.extend(_meta_values(posting.meta))
    return history_hints.normalize(' '.join(parts))


def categorizers_of(txn: data.Transaction) -> List[str]:
    return [str(p.meta[CATEGORIZER_KEY]) for p in txn.postings
            if p.meta and CATEGORIZER_KEY in p.meta]


class Index:
    """Transactions of the journal files (not plugin-processed), newest first."""

    def __init__(self, entries: Iterable[data.Directive]):
        self.transactions = sorted(
            (e for e in entries
             if isinstance(e, data.Transaction) and e.meta and e.meta.get('filename')),
            key=data.entry_sortkey, reverse=True)
        self._categorizers = [frozenset(categorizers_of(txn)) for txn in self.transactions]
        self.categorizers = sorted(frozenset().union(*self._categorizers))
        self._search_texts = {}  # type: Dict[int, str]

    def _search_text(self, index: int) -> str:
        text = self._search_texts.get(index)
        if text is None:
            text = self._search_texts[index] = search_text(self.transactions[index])
        return text

    def filter(self, flt: Filter) -> List[data.Transaction]:
        result = []
        for index, txn in enumerate(self.transactions):
            if flt.start is not None and txn.date < flt.start:
                continue
            if flt.end is not None and txn.date > flt.end:
                continue
            if flt.categorizer is not None:
                values = self._categorizers[index]
                matches = flt.categorizer in values if flt.categorizer else not values
                if not matches:
                    continue
            if flt.search and flt.search not in self._search_text(index):
                continue
            result.append(txn)
        return result


def encode_item(txn: data.Transaction) -> Dict[str, Any]:
    return {'date': txn.date.isoformat(),
            'formatted': history_hints.transaction_text(txn),
            'filename': txn.meta['filename'],
            'lineno': txn.meta.get('lineno')}

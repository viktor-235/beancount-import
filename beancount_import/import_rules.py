"""Import rules: filter-map style rules applied to pending imported transactions.

The YAML format and the actions are described in the "Import rules" section of
FORK.md.
"""

from typing import Any, Dict, List, NamedTuple, Tuple

import yaml
from beancount.core.data import Transaction

from .matching import is_unknown_account

_ACTIONS = ('setAccount', 'addPostingMeta', 'setPayee', 'setNarration',
            'addTags', 'addMeta')


class Rule(NamedTuple):
    index: int
    filter: Any  # fava.core.filters.AdvancedFilter
    actions: Dict[str, Any]


def load_rules(path: str) -> List[Rule]:
    from fava.core.filters import AdvancedFilter  # optional dependency
    with open(path, encoding='utf-8') as f:
        config = yaml.safe_load(f) or {}
    if not isinstance(config, dict):
        raise ValueError('%s: expected a mapping with "rules"' % path)
    unknown = [k for k in config if k != 'rules' and not k.startswith('x-')]
    if unknown:
        raise ValueError('%s: unknown top-level keys %s' % (path, unknown))
    rules = []
    for i, item in enumerate(config.get('rules') or [], 1):
        if not isinstance(item, dict):
            raise ValueError('%s: rule %d: expected a mapping' % (path, i))
        if 'addPostingMeta' in item and 'setAccount' not in item:
            raise ValueError('%s: rule %d: addPostingMeta needs setAccount' %
                             (path, i))
        if set(item) - {'filter', *_ACTIONS} or 'filter' not in item:
            raise ValueError('%s: rule %d: expected "filter" and %s, got %s' %
                             (path, i, ', '.join(_ACTIONS), sorted(item)))
        actions = {k: v for k, v in item.items() if k != 'filter'}
        rules.append(Rule(i, AdvancedFilter(item['filter']), actions))
    return rules


def apply_rules(rules: List[Rule], txn: Transaction
                ) -> Tuple[Transaction, List[str]]:
    """Applies matching rules in order; the first rule to set a field wins."""
    values = {}  # type: Dict[str, Any]
    set_by = {}  # type: Dict[str, int]
    # Some sources leave tags or meta as None.
    tags = set(txn.tags or ())
    meta = dict(txn.meta or {})
    warnings = []
    matched = False
    for rule in rules:
        if not rule.filter.apply([txn]):
            continue
        matched = True
        for key, value in rule.actions.items():
            if key == 'addTags':
                tags.update(value.replace('#', '').split())
            elif key == 'addMeta':
                meta.update(value)
            elif key not in values:
                values[key] = value
                set_by[key] = rule.index
            elif values[key] != value:
                warnings.append('%s: rules %d and %d disagree on %s, using %r' %
                                (txn.payee or txn.narration, set_by[key],
                                 rule.index, key, values[key]))
    if not matched:
        return txn, warnings
    postings = txn.postings
    if 'setAccount' in values:
        posting_meta = values.get('addPostingMeta', {})
        postings = [
            p._replace(account=values['setAccount'],
                       meta={**(p.meta or {}), **posting_meta})
            if is_unknown_account(p.account) else p for p in postings
        ]
    return txn._replace(
        payee=values.get('setPayee', txn.payee),
        narration=values.get('setNarration', txn.narration),
        tags=frozenset(tags), meta=meta, postings=postings), warnings

"""Manual JEV classification, adapted from fava-import-history-hints by viktor-235."""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter, defaultdict
from http.client import HTTPException
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from beancount.core import data

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "~typesafe/jev-latest"
TIMEOUT = 30
# Per account in `payee_history`: most frequent distinct narrations, shortened.
HISTORY_NARRATIONS = 5
HISTORY_NARRATION_LENGTH = 100
# Used when the browser sends no custom instructions (Settings → JEV: Prompt).
DEFAULT_INSTRUCTIONS = (
    "Which account should `target_group.postings` in `transaction` be booked to? "
    "`target_group.direction` tells whether money went out or came in; money in "
    "on an expense account is a refund. Asset and liability accounts are for "
    "transfers between own accounts, cash, currency exchange and debts. "
    "`payee_history` is this payee's past bookings per account with their "
    "narrations; the meaning of the transaction outweighs it."
)
# Keep in sync with frontend/jev_settings.ts.
DEFAULT_ACCOUNT_FILTERS = {
    "include": [r"^(Expenses|Income):", r"^(Assets|Liabilities):"],
    "exclude": [r"^Expenses:Unknown$", r"^Income:Unknown$", r"^Expenses:FIXME$",
                r":Unattributed"],
}


class JevError(Exception):
    """An error whose message is safe to show in the browser."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def configured() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY", "").strip())


def account_filter(filters: Any = None) -> Callable[[str], bool]:
    """Compile include/exclude regex lists into an account predicate."""
    if filters is None:
        filters = DEFAULT_ACCOUNT_FILTERS
    if not isinstance(filters, dict):
        raise JevError("Invalid JEV account filters.", 400)
    compiled = {}
    for key in ("include", "exclude"):
        patterns = filters.get(key)
        if not isinstance(patterns, list) or not all(
            isinstance(p, str) for p in patterns
        ):
            raise JevError("Invalid JEV account filters.", 400)
        try:
            compiled[key] = [re.compile(p) for p in patterns]
        except re.error as exc:
            raise JevError(f"Invalid JEV account pattern: {exc.pattern!r}.", 400)
    return lambda account: any(
        p.search(account) for p in compiled["include"]
    ) and not any(p.search(account) for p in compiled["exclude"])


def check_not_skipped(patterns: Any, transaction: str) -> None:
    """Reject a transaction matched by a skip pattern (multiline search)."""
    if patterns is None:
        return
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        raise JevError("Invalid JEV skip patterns.", 400)
    for pattern in patterns:
        try:
            matched = re.search(pattern, transaction, re.MULTILINE)
        except re.error:
            raise JevError(f"Invalid JEV skip pattern: {pattern!r}.", 400)
        if matched:
            raise JevError(f"Skipped for AI by the settings pattern {pattern}", 422)


def build_context(
    entries: list[Any],
    selected: data.Transaction,
    raw: str,
    is_category: Callable[[str], bool],
) -> dict[str, Any]:
    # Posting to an account that is not open on the transaction date fails
    # bean-check. Beancount sorts Close after same-day transactions, so the
    # close date itself is still valid.
    closed = {
        entry.account
        for entry in entries
        if isinstance(entry, data.Close) and entry.date < selected.date
    }
    categories = {}
    for entry in entries:
        if (
            not isinstance(entry, data.Open)
            or entry.date > selected.date
            or entry.account in closed
            or not is_category(entry.account)
        ):
            continue
        value = (entry.meta or {}).get("note")
        note = str(value) if value is not None else ""
        categories[entry.account] = {"note": note} if note.strip() else {}
    if not categories:
        raise JevError("No accounts match the JEV account filters.", 422)
    if len(categories) > 255:
        raise JevError("JEV Choice supports at most 255 categories.", 422)

    # Source accounts stay valid choices (currency exchange within one
    # multi-currency account), but would otherwise be counted in every payee
    # transaction. A past posting is a source if it is the statement side
    # (beancount-import's `source_desc`) or uses this transaction's own account;
    # it counts only where its account was also the counterpart (posted twice).
    own = {
        p.account
        for p in selected.postings
        if p.account.startswith(("Assets:", "Liabilities:"))
    }
    payee = _payee_key(selected.payee)
    counts: Counter[str] = Counter()
    # Narrations counted by normalized text; the first spelling seen is sent.
    narrations: defaultdict[str, Counter[str]] = defaultdict(Counter)
    spellings: dict[str, str] = {}
    if payee:
        for entry in entries:
            if (
                isinstance(entry, data.Transaction)
                and entry is not selected
                and _payee_key(entry.payee) == payee
            ):
                posted = Counter(p.account for p in entry.postings)
                sources = own | {
                    p.account for p in entry.postings if "source_desc" in (p.meta or {})
                }
                accounts = {
                    account
                    for account, n in posted.items()
                    if account in categories and (account not in sources or n > 1)
                }
                counts.update(accounts)
                narration = _history_narration(entry)
                if narration:
                    key = spellings.setdefault(_text_key(narration), narration)
                    for account in accounts:
                        narrations[account][key] += 1

    return {
        "transaction": raw,
        "categories": dict(sorted(categories.items())),
        "payee_history": {
            account: {
                "count": count,
                "narrations": [
                    text
                    for text, _ in narrations[account].most_common(HISTORY_NARRATIONS)
                ],
            }
            for account, count in sorted(counts.items(), key=lambda i: (-i[1], i[0]))
        },
    }


def _history_narration(entry: data.Transaction) -> str:
    """The narration, unless empty or just the payee repeated (bank imports)."""
    narration = " ".join((entry.narration or "").split())
    if not narration or _text_key(narration) == _text_key(entry.payee):
        return ""
    if len(narration) > HISTORY_NARRATION_LENGTH:
        narration = narration[: HISTORY_NARRATION_LENGTH - 1].rstrip() + "…"
    return narration


def _text_key(text: str | None) -> str:
    return " ".join(re.sub(r"[\W_]+", " ", (text or "").casefold()).split())


def _payee_key(payee: str | None) -> str:
    return " ".join((payee or "").casefold().split())


def build_request(
    context: dict[str, Any], model: Any = None, instructions: Any = None
) -> dict[str, Any]:
    """Build the same request body for local preview and classification."""
    if instructions is not None and not isinstance(instructions, str):
        raise JevError("Invalid JEV instructions.", 400)
    instructions = (instructions or "").strip() or DEFAULT_INSTRUCTIONS
    if model is None:
        model = os.environ.get("JEV_MODEL", "").strip() or DEFAULT_MODEL
    if not isinstance(model, str) or not model.strip():
        raise JevError("The jev_model setting must be a non-empty string.", 503)

    return {
        "model": model.strip(),
        # The accounts and notes reach the model as `criteria`; repeating them
        # in the state would double the request.
        "state": {k: v for k, v in context.items() if k != "categories"},
        "questions": {
            "category": {
                "type": "choice",
                "instructions": instructions,
                "criteria": context["categories"],
            }
        },
    }


def classify(
    context: dict[str, Any], model: Any = None, instructions: Any = None
) -> dict[str, Any]:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise JevError("Set OPENROUTER_API_KEY on the importer server to enable JEV.", 503)
    payload = build_request(context, model, instructions)
    req = Request(
        ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        ),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "X-Title": "beancount-import-jev",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=TIMEOUT) as response:
            result = json.load(response)
    except HTTPError as exc:
        exc.close()
        if exc.code in (401, 403):
            message = "OpenRouter rejected the API key. Check OPENROUTER_API_KEY."
        elif exc.code == 402:
            message = "OpenRouter has insufficient credits. Check your account balance."
        elif exc.code == 429:
            raise JevError(
                "OpenRouter rate limit reached. Try again later.", 429
            ) from None
        else:
            message = "OpenRouter could not classify the transaction. Try again later."
        raise JevError(message) from None
    except TimeoutError:
        raise JevError("JEV request timed out. Try again.", 504) from None
    except URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise JevError("JEV request timed out. Try again.", 504) from None
        raise JevError("Could not connect to OpenRouter. Try again.") from None
    except (OSError, HTTPException):
        raise JevError("Could not connect to OpenRouter. Try again.") from None
    except (ValueError, UnicodeError):
        raise JevError("OpenRouter returned an invalid JEV response.") from None

    answers = result.get("answers") if isinstance(result, dict) else None
    answer = answers.get("category") if isinstance(answers, dict) else None
    if not isinstance(answer, dict):
        raise JevError("OpenRouter returned an invalid JEV response.")
    category = answer.get("choice")
    confidence = answer.get("confidence")
    if (
        answer.get("type") != "choice"
        or not isinstance(category, str)
        or category not in context["categories"]
        or not _is_probability(confidence)
    ):
        raise JevError("OpenRouter returned an invalid JEV category or confidence.")
    classification = {"category": category, "confidence": confidence}
    # Keep older responses usable, without inventing a probability distribution.
    # The map may be sparse (only non-zero categories).
    if "probabilities" in answer:
        probabilities = answer["probabilities"]
        if (
            not isinstance(probabilities, dict)
            or not probabilities.keys() <= context["categories"].keys()
            or not all(_is_probability(value) for value in probabilities.values())
        ):
            raise JevError("OpenRouter returned invalid JEV category probabilities.")
        classification["probabilities"] = probabilities
    return classification


def _is_probability(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and 0 <= value <= 1
        and math.isfinite(value)
    )

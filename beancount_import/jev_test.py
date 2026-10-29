# Adapted from fava-import-history-hints by viktor-235.
from __future__ import annotations

import datetime
import io
import json
from email.message import Message
from http.client import IncompleteRead
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest
from beancount import loader
from beancount.core import data
from . import jev
from beancount.parser.printer import EntryPrinter

def _raw_transaction(txn):
    return EntryPrinter()(txn).rstrip()

LEDGER = """
2010-01-01 open Assets:Bank USD
2010-01-01 open Income:Salary USD
2010-01-01 open Income:Unknown USD
2010-01-01 open Expenses:Coffee USD
  note: "Кофе и выпечка; не продукты домой."
2010-01-01 open Expenses:Groceries USD
2010-01-01 open Expenses:Unused USD
2010-01-01 open Expenses:Unknown USD
2010-01-01 open Expenses:FIXME USD

2026-01-01 * "Coffee+Bar" "Coffee and snacks"
  Assets:Bank -10 USD
  Expenses:Coffee 3 USD
  Expenses:Coffee 2 USD
  Expenses:Groceries 5 USD

2026-01-02 * "  COFFEE+bar  " "Another coffee"
  Assets:Bank -3 USD
  Expenses:Coffee 3 USD

2026-01-03 * "Coffee Bar" "Different payee"
  Assets:Bank -4 USD
  Expenses:Groceries 4 USD

2026-01-04 * "Coffee+Bar branch" "Partial match"
  Assets:Bank -4 USD
  Expenses:Groceries 4 USD

2026-01-05 * "Coffee+Bar" "Uncategorized"
  Assets:Bank -4 USD
  Expenses:Unknown 4 USD

2026-01-06 * "Coffee+Bar" "Selected transaction" #synthetic ^synthetic-link
  imported_id: "synthetic-001"
  Assets:Bank -6 USD
    source_desc: "SYNTHETIC COFFEE+BAR"
  Expenses:Coffee 6 USD
"""

PROBABILITIES = {
    "Assets:Bank": 0,
    "Expenses:Coffee": 0.91,
    "Expenses:Groceries": 0.08,
    "Expenses:Unused": 0.01,
    "Income:Salary": 0,
}


@pytest.fixture(autouse=True)
def no_real_credentials(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("JEV_MODEL", raising=False)


@pytest.fixture
def entries():
    entries, errors, _ = loader.load_string(LEDGER)
    assert errors == []
    return entries


@pytest.fixture
def selected(entries):
    return next(
        entry
        for entry in entries
        if isinstance(entry, data.Transaction)
        and entry.narration == "Selected transaction"
    )


def _counts(context):
    return {a: h["count"] for a, h in context["payee_history"].items()}


NO_EXCLUDES = jev.account_filter({"include": ["^(Expenses|Income):"], "exclude": []})


@pytest.fixture
def context(entries, selected):
    return jev.build_context(
        entries, selected, _raw_transaction(selected), jev.account_filter()
    )


@pytest.fixture
def upstream(monkeypatch):
    calls = []

    def fake_urlopen(req, *, timeout):
        calls.append((req, timeout))
        return io.BytesIO(
            json.dumps(
                {
                    "answers": {
                        "category": {
                            "type": "choice",
                            "choice": "Expenses:Coffee",
                            "confidence": 0.87,
                            "probabilities": PROBABILITIES,
                        }
                    }
                }
            ).encode()
        )

    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-key")
    monkeypatch.setattr(jev, "urlopen", fake_urlopen)
    return calls


def test_context_includes_notes_unused_categories_and_complete_transaction(
    context, selected
):
    assert context["categories"] == {
        "Assets:Bank": {},
        "Expenses:Coffee": {"note": "Кофе и выпечка; не продукты домой."},
        "Expenses:Groceries": {},
        "Expenses:Unused": {},
        "Income:Salary": {},
    }
    raw = context["transaction"]
    assert raw == _raw_transaction(selected)
    assert "#synthetic" in raw and "^synthetic-link" in raw
    assert 'imported_id: "synthetic-001"' in raw
    assert 'source_desc: "SYNTHETIC COFFEE+BAR"' in raw
    assert "Assets:Bank" in raw and "Expenses:Coffee" in raw
    assert "6 USD" in raw


@pytest.mark.parametrize("note", [None, "", "  \t\n", "Useful description"])
def test_category_notes_omit_empty_values(entries, selected, note):
    account = next(
        entry
        for entry in entries
        if isinstance(entry, data.Open) and entry.account == "Expenses:Unused"
    )
    account.meta["note"] = note
    context = jev.build_context(
        entries, selected, _raw_transaction(selected), jev.account_filter()
    )
    expected = {"note": note} if note == "Useful description" else {}
    assert context["categories"]["Expenses:Unused"] == expected
    assert (
        jev.build_request(context)["questions"]["category"]["criteria"][
            "Expenses:Unused"
        ]
        == expected
    )


def test_history_uses_whole_payee_and_unique_categories_per_transaction(context):
    assert _counts(context) == {
        "Expenses:Coffee": 2,
        "Expenses:Groceries": 1,
    }


@pytest.mark.parametrize("payee", [None, "", "  ", "New Merchant"])
def test_no_history_for_missing_or_unseen_payee(entries, selected, payee):
    selected = selected._replace(payee=payee)
    context = jev.build_context(entries, selected, "raw", NO_EXCLUDES)
    assert _counts(context) == {}


def test_repeated_whitespace_in_payee_is_ignored(entries, selected):
    historical = selected._replace(payee="Coffee+Bar   Branch")
    selected = selected._replace(payee=" coffee+bar\tbranch ")
    context = jev.build_context(
        [*entries, historical, selected], selected, "raw", NO_EXCLUDES
    )
    assert _counts(context)["Expenses:Coffee"] == 1
    assert _counts(context)["Expenses:Groceries"] == 1


def test_custom_ignored_categories(entries, selected):
    filters = {**jev.DEFAULT_ACCOUNT_FILTERS}
    filters["exclude"] = [*filters["exclude"], "^Expenses:Coffee$"]
    context = jev.build_context(entries, selected, "raw", jev.account_filter(filters))
    assert "Expenses:Coffee" not in context["categories"]
    assert _counts(context) == {"Expenses:Groceries": 1}


def test_default_filters_skip_unknown_accounts(context):
    assert not {"Expenses:Unknown", "Income:Unknown", "Expenses:FIXME"} & set(
        context["categories"]
    )
    assert "Income:Salary" in context["categories"]
    assert "Assets:Bank" in context["categories"]
    assert jev.account_filter()("Expenses:Groceries")
    assert not jev.account_filter()("Expenses:Unattributed:Bank:Cash")
    assert not jev.account_filter()("Income:Unattributed:Bank:Cash")


def test_include_patterns_can_select_other_roots(entries, selected):
    wallet = next(e for e in entries if isinstance(e, data.Open))._replace(
        account="Assets:Wallet"
    )
    is_category = jev.account_filter({"include": ["^Assets:", "Coffee$"], "exclude": []})
    context = jev.build_context([*entries, wallet], selected, "raw", is_category)
    assert set(context["categories"]) == {
        "Assets:Bank", "Assets:Wallet", "Expenses:Coffee"
    }


@pytest.mark.parametrize(
    "filters",
    [[], {"include": "^Expenses:"}, {"include": [1], "exclude": []},
     {"include": ["("], "exclude": []}],
)
def test_invalid_account_filters(filters):
    with pytest.raises(jev.JevError) as exc:
        jev.account_filter(filters)
    assert exc.value.status == 400


@pytest.mark.parametrize("count", [0, 255, 256])
def test_category_limits_without_truncation(entries, selected, count):
    template = next(entry for entry in entries if isinstance(entry, data.Open))
    opens = [template._replace(account=f"Expenses:Category{i}") for i in range(count)]
    if count == 255:
        context = jev.build_context(opens, selected, "raw", NO_EXCLUDES)
        assert len(context["categories"]) == 255
    else:
        with pytest.raises(jev.JevError) as exc:
            jev.build_context(opens, selected, "raw", NO_EXCLUDES)
        assert exc.value.status == 422


def test_openrouter_request_and_result(context, upstream):
    assert jev.classify(context) == {
        "category": "Expenses:Coffee",
        "confidence": 0.87,
        "probabilities": PROBABILITIES,
    }
    assert len(upstream) == 1
    req, timeout = upstream[0]
    assert timeout == 30
    assert req.full_url == jev.ENDPOINT
    assert req.method == "POST"
    assert req.get_header("Authorization") == "Bearer synthetic-key"
    assert req.get_header("Content-type") == "application/json"
    body = json.loads(req.data)
    assert body["model"] == "~typesafe/jev-latest"
    assert body["state"] == {k: v for k, v in context.items() if k != "categories"}
    assert "categories" not in body["state"]
    assert list(body["questions"]) == ["category"]
    assert body["questions"]["category"]["type"] == "choice"
    assert body["questions"]["category"]["criteria"] == context["categories"]
    assert "payee_history" in body["questions"]["category"]["instructions"]


def test_model_precedence(context, upstream, monkeypatch):
    monkeypatch.setenv("JEV_MODEL", "synthetic-env-model")
    jev.classify(context)
    jev.classify(context, model="synthetic-config-model")
    assert [json.loads(req.data)["model"] for req, _ in upstream] == [
        "synthetic-env-model",
        "synthetic-config-model",
    ]


@pytest.mark.parametrize("model", [12, [], {}, "", "   "])
def test_invalid_model_is_rejected(context, upstream, model):
    with pytest.raises(jev.JevError) as exc:
        jev.classify(context, model=model)
    assert exc.value.status == 503
    assert not upstream


@pytest.mark.parametrize(
    "answer",
    [
        {"choice": "Expenses:Coffee", "confidence": 0.8},
        {"type": "noul", "choice": "Expenses:Coffee", "confidence": 0.8},
        {"type": "choice", "choice": "Expenses:MadeUp", "confidence": 0.8},
        {"type": "choice", "choice": ["Expenses:Coffee"], "confidence": 0.8},
        *[
            {"type": "choice", "choice": "Expenses:Coffee", "confidence": confidence}
            for confidence in [None, True, "0.8", -0.1, 1.1, float("nan"), float("inf")]
        ],
    ],
)
def test_invalid_answers_are_rejected(context, upstream, monkeypatch, answer):
    monkeypatch.setattr(
        jev,
        "urlopen",
        lambda *_a, **_kw: io.BytesIO(
            json.dumps({"answers": {"category": answer}}).encode()
        ),
    )
    with pytest.raises(jev.JevError, match="invalid"):
        jev.classify(context)


@pytest.mark.parametrize("response", [[], None, {}, {"answers": []}, {"answers": None}])
def test_invalid_response_structure(context, upstream, monkeypatch, response):
    monkeypatch.setattr(
        jev, "urlopen", lambda *_a, **_kw: io.BytesIO(json.dumps(response).encode())
    )
    with pytest.raises(jev.JevError, match="invalid"):
        jev.classify(context)


@pytest.mark.parametrize("confidence", [0, 1])
def test_confidence_boundaries_are_valid(context, upstream, monkeypatch, confidence):
    answer = {"type": "choice", "choice": "Expenses:Coffee", "confidence": confidence}
    monkeypatch.setattr(
        jev,
        "urlopen",
        lambda *_a, **_kw: io.BytesIO(
            json.dumps({"answers": {"category": answer}}).encode()
        ),
    )
    assert jev.classify(context) == {
        "category": "Expenses:Coffee",
        "confidence": confidence,
    }


@pytest.mark.parametrize(
    "probabilities",
    [
        None,
        [],
        {"Expenses:MadeUp": 0.5},
        {**PROBABILITIES, "Expenses:MadeUp": 0},
        *[
            {**PROBABILITIES, "Expenses:Groceries": value}
            for value in [True, "0.08", None, -0.1, 1.1, float("nan"), float("inf")]
        ],
    ],
)
def test_invalid_probabilities_are_rejected(
    context, upstream, monkeypatch, probabilities
):
    answer = {
        "type": "choice",
        "choice": "Expenses:Coffee",
        "confidence": 0.87,
        "probabilities": probabilities,
    }
    monkeypatch.setattr(
        jev,
        "urlopen",
        lambda *_a, **_kw: io.BytesIO(
            json.dumps({"answers": {"category": answer}}).encode()
        ),
    )
    with pytest.raises(jev.JevError, match="invalid.*probabilities"):
        jev.classify(context)


@pytest.mark.parametrize("probabilities", [{}, {"Expenses:Coffee": 0.87}])
def test_sparse_probabilities_are_accepted(
    context, upstream, monkeypatch, probabilities
):
    answer = {
        "type": "choice",
        "choice": "Expenses:Coffee",
        "confidence": 0.87,
        "probabilities": probabilities,
    }
    monkeypatch.setattr(
        jev,
        "urlopen",
        lambda *_a, **_kw: io.BytesIO(
            json.dumps({"answers": {"category": answer}}).encode()
        ),
    )
    assert jev.classify(context)["probabilities"] == probabilities


@pytest.mark.parametrize("body", [b"not-json", b"\xff"])
def test_invalid_json_response(context, upstream, monkeypatch, body):
    monkeypatch.setattr(jev, "urlopen", lambda *_a, **_kw: io.BytesIO(body))
    with pytest.raises(jev.JevError, match="invalid"):
        jev.classify(context)


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (TimeoutError("synthetic-key private data"), 504),
        (URLError(TimeoutError("private data")), 504),
        (URLError("synthetic-key private data"), 502),
        (ConnectionResetError("synthetic-key private data"), 502),
        (IncompleteRead(b"private data"), 502),
        *[
            (
                HTTPError(
                    jev.ENDPOINT, code, "private data", Message(), io.BytesIO(b"private")
                ),
                429 if code == 429 else 502,
            )
            for code in [401, 403, 402, 429, 500, 529]
        ],
    ],
)
def test_transport_errors_are_sanitized_without_retries(
    context, upstream, monkeypatch, error, status
):
    calls = []

    def fail(*_a, **_kw):
        calls.append(1)
        raise error

    monkeypatch.setattr(jev, "urlopen", fail)
    with pytest.raises(jev.JevError) as exc:
        jev.classify(context)
    assert exc.value.status == status
    assert "private" not in str(exc.value) and "synthetic-key" not in str(exc.value)
    assert len(calls) == 1


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_key_never_calls_openrouter(context, monkeypatch, key):
    if key is not None:
        monkeypatch.setenv("OPENROUTER_API_KEY", key)
    monkeypatch.setattr(
        jev, "urlopen", lambda *_a, **_kw: pytest.fail("unexpected call")
    )
    assert not jev.configured()
    with pytest.raises(jev.JevError) as exc:
        jev.classify(context)
    assert exc.value.status == 503




def test_closed_accounts_are_skipped_after_close_date(entries, selected):
    day = datetime.timedelta(days=1)
    close = data.Close(
        data.new_metadata("x", 0), selected.date - day, "Expenses:Coffee"
    )
    context = jev.build_context(
        [*entries, close], selected, "raw", jev.account_filter()
    )
    assert "Expenses:Coffee" not in context["categories"]
    # Beancount still accepts postings on the close date.
    context = jev.build_context(
        [*entries, close._replace(date=selected.date)],
        selected, "raw", jev.account_filter()
    )
    assert "Expenses:Coffee" in context["categories"]


def test_accounts_opened_after_transaction_are_skipped(entries, selected):
    day = datetime.timedelta(days=1)
    later = data.Open(
        data.new_metadata("x", 0), selected.date + day, "Expenses:Pets", None, None
    )
    context = jev.build_context(
        [*entries, later], selected, "raw", jev.account_filter()
    )
    assert "Expenses:Pets" not in context["categories"]
    context = jev.build_context(
        [*entries, later._replace(date=selected.date)],
        selected, "raw", jev.account_filter()
    )
    assert "Expenses:Pets" in context["categories"]


def test_source_account_counted_only_as_counterpart(entries, selected):
    template = next(e for e in entries if isinstance(e, data.Transaction))
    exchange = template._replace(
        date=selected.date,
        postings=[
            template.postings[0]._replace(account="Assets:Bank"),
            template.postings[1]._replace(account="Assets:Bank"),
        ],
    )
    context = jev.build_context(
        [*entries, exchange], selected, "raw", jev.account_filter()
    )
    assert "Assets:Bank" in context["categories"]
    assert _counts(context) == {
        "Assets:Bank": 1,
        "Expenses:Coffee": 2,
        "Expenses:Groceries": 1,
    }


def test_skip_patterns_search_transaction_lines():
    raw = '2026-01-06 * "Coffee+Bar" "Selected"\n  Assets:Bank -6 USD\n  Expenses:FIXME 6 USD'
    jev.check_not_skipped(None, raw)
    jev.check_not_skipped([], raw)
    jev.check_not_skipped(["^Assets:Bank"], raw)  # indented posting line
    with pytest.raises(jev.JevError) as exc:
        jev.check_not_skipped(["Nothing", r"^\s+Assets:Bank"], raw)
    assert exc.value.status == 422 and "Assets:Bank" in str(exc.value)
    with pytest.raises(jev.JevError) as exc:
        jev.check_not_skipped(["coffee\\+bar|Coffee\\+Bar"], raw)
    assert exc.value.status == 422


@pytest.mark.parametrize("patterns", ["^x", [1], ["("]])
def test_invalid_skip_patterns(patterns):
    with pytest.raises(jev.JevError) as exc:
        jev.check_not_skipped(patterns, "raw")
    assert exc.value.status == 400


@pytest.mark.parametrize("instructions", [None, "", "   "])
def test_default_instructions(context, instructions):
    body = jev.build_request(context, instructions=instructions)
    assert body["questions"]["category"]["instructions"] == jev.DEFAULT_INSTRUCTIONS


def test_custom_instructions(context):
    body = jev.build_request(context, instructions="  Pick a category.\n")
    assert body["questions"]["category"]["instructions"] == "Pick a category."
    with pytest.raises(jev.JevError) as exc:
        jev.build_request(context, instructions=["x"])
    assert exc.value.status == 400


def test_payee_history_lists_narrations_per_account(entries, selected):
    template = next(e for e in entries if isinstance(e, data.Transaction))
    def booked(narration, payee="Coffee+Bar"):
        return template._replace(payee=payee, narration=narration)
    extra = [
        booked("Latte"), booked("latte "), booked("Latte"),
        booked("COFFEE BAR"),  # payee repeated: skipped
        booked(""),
        booked("x" * 150),
        booked("Espresso"), booked("Cake"),
        booked("Other payee", payee="Elsewhere"),
    ]
    context = jev.build_context(
        [*entries, *extra], selected, "raw", jev.account_filter()
    )
    history = context["payee_history"]
    assert list(history) == ["Expenses:Coffee", "Expenses:Groceries"]
    coffee = history["Expenses:Coffee"]
    assert coffee["count"] == 10
    assert coffee["narrations"][0] == "Latte"
    assert coffee["narrations"][:2] == ["Latte", "Coffee and snacks"]
    assert "latte" not in coffee["narrations"]  # merged into "Latte"
    assert "COFFEE BAR" not in coffee["narrations"]
    assert "Other payee" not in coffee["narrations"]
    assert len(coffee["narrations"]) == jev.HISTORY_NARRATIONS
    long = next(n for n in coffee["narrations"] if n.startswith("x"))
    assert len(long) == jev.HISTORY_NARRATION_LENGTH and long.endswith("…")


def test_statement_side_postings_are_not_payee_history(entries, selected):
    template = next(e for e in entries if isinstance(e, data.Transaction))
    paid_elsewhere = template._replace(
        postings=[
            template.postings[0]._replace(
                account="Assets:Wallet", meta={"source_desc": "COFFEE+BAR"}
            ),
            template.postings[1],
        ]
    )
    wallet = next(e for e in entries if isinstance(e, data.Open))._replace(
        account="Assets:Wallet"
    )
    context = jev.build_context(
        [*entries, wallet, paid_elsewhere], selected, "raw", jev.account_filter()
    )
    assert "Assets:Wallet" in context["categories"]
    assert "Assets:Wallet" not in context["payee_history"]
    assert _counts(context)["Expenses:Coffee"] == 3

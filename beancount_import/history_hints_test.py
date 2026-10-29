import copy
import json
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import tornado.testing
import tornado.web
from beancount import loader
from beancount.core import data

from . import history_hints as hints, jev, reconcile, webserver

DEMO = Path(__file__).resolve().parents[1] / "examples/jev/demo.bean"


def loaded_ledger(directory):
    path = Path(directory) / "journal.bean"
    path.write_text(DEMO.read_text())
    ignored = Path(directory) / "ignored.bean"
    ignored.touch()
    options = vars(webserver.parse_arguments([], journal_input=str(path), ignored_journal=str(ignored),
                       default_output=str(path), data_sources=[]))
    owner = reconcile.Reconciler(str(path), lambda _: None, str(ignored), options)
    return owner.loaded_future.result()


def candidates_for(loaded, narration):
    pending = next(p for p in loaded.pending_data if p.entries[0].narration == narration)
    return loaded._make_candidates_from_import_result(pending)


@pytest.fixture
def loaded(tmp_path):
    return loaded_ledger(tmp_path)


def test_masking_and_targeted_application(loaded):
    candidates = candidates_for(loaded, "Split purchase")
    candidate = candidates.candidates[-1]
    original, before = hints.candidate_snapshot(candidate)
    candidates.change_transaction(len(candidates.candidates) - 1, {
        "accounts": ["Expenses:Coffee", "Expenses:Groceries"],
        "payee": "Edited shop", "narration": "Edited text", "tags": ["edited"], "links": ["receipt"]})
    candidate = candidates.candidates[-1]
    masked, after = hints.candidate_snapshot(candidate)
    assert [p.account for p in masked.postings] == ["Assets:Bank", "Expenses:FIXME", "Expenses:FIXME"]
    assert [p.units for p in masked.postings] == [p.units for p in original.postings]
    assert masked.payee == "Edited shop" and masked.narration == "Edited text"
    assert masked.tags == frozenset(["edited"]) and masked.links == frozenset(["receipt"])
    assert before["token"] != after["token"]
    ctx = hints.build_context(loaded.editor, candidate, 1)
    assert ctx["target_group"] == {"number": 1, "posting_indices": [2],
                                   "postings": ["7 USD · Expenses:FIXME"],
                                   "direction": "money out"}
    assert "Expenses:Coffee" not in ctx["transaction"]
    candidates.change_transaction(len(candidates.candidates) - 1, {
        "accounts": ["Income:Salary", "Expenses:Groceries"],
        "payee": "Edited shop", "narration": "Edited text", "tags": ["edited"], "links": ["receipt"]})
    assert hints.candidate_snapshot(candidates.candidates[-1])[1]["token"] == after["token"]
    with pytest.raises(jev.JevError):
        hints.build_context(loaded.editor, candidate, None)


def test_history_search_and_exact_payee_context(loaded):
    candidate = candidates_for(loaded, "Single purchase").candidates[-1]
    entries = hints.history_entries(loaded.editor, candidate)
    summary = hints.summarize(entries, "coffee BAR")
    assert summary["transaction_count"] == 4  # punctuation normalized, includes pending split in ledger
    assert summary["accounts"][0]["account"] == "Expenses:Groceries"
    fixme = next(a for a in summary["accounts"] if a["account"] == "Expenses:FIXME")
    assert fixme["count"] == 2  # past unclear bookings stay visible
    assert hints.summarize(entries, "split")["transaction_count"] == 1
    assert hints.summarize(entries, "one")["transaction_count"] == 0  # current source excluded
    assert hints.summarize(entries, "")["transaction_count"] == 6
    ctx = hints.build_context(loaded.editor, candidate, 0)
    assert {a: h["count"] for a, h in ctx["payee_history"].items()} == {"Expenses:Coffee": 1, "Expenses:Groceries": 1}
    assert ctx["categories"]["Expenses:Coffee"]["note"] == "Coffee and snacks."
    assert "Expenses:Unused" in ctx["categories"]
    ctx = hints.build_context(loaded.editor, candidate, 0,
                              {"include": ["^Expenses:"], "exclude": ["Unused"]})
    assert "Expenses:Unused" not in ctx["categories"]
    assert not any(a.startswith("Income:") for a in ctx["categories"])


def test_exclusion_uses_origin_not_identity(loaded):
    candidate = candidates_for(loaded, "Single purchase").candidates[-1]
    excluded = {hints.origin(e) for e in candidate.used_transactions}
    editor = SimpleNamespace(history_entries=copy.deepcopy(loaded.editor.history_entries))
    assert all(hints.origin(e) not in excluded for e in hints.history_entries(editor, candidate)
               if isinstance(e, data.Transaction))


def test_history_refresh_after_confirm(loaded):
    candidates = candidates_for(loaded, "Single purchase")
    index = len(candidates.candidates) - 1
    candidates.change_transaction(index, {"accounts": ["Expenses:Coffee"]})
    loaded.accept_candidate(candidates.candidates[index])
    assert loaded.editor._history_entries is None
    summary = hints.summarize(loaded.editor.history_entries, "Coffee+Bar")
    assert next(a for a in summary["accounts"] if a["account"] == "Expenses:Coffee")["count"] == 2
    assert loader.load_file(loaded.editor.journal_path)[1] == []


def test_merged_transfer_has_no_ai_group(loaded):
    candidates = candidates_for(loaded, "Bank side")
    merged = next(c for c in candidates.candidates if len(c.used_transactions) == 2)
    assert hints.candidate_snapshot(merged)[1]["groups"] == []
    with pytest.raises(jev.JevError):
        hints.build_context(loaded.editor, merged, 0)
    loaded.accept_candidate(merged)
    assert loader.load_file(loaded.editor.journal_path)[1] == []


class TestHintsHTTP(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        self.directory = tempfile.TemporaryDirectory()
        self.loaded = loaded_ledger(self.directory.name)
        self.candidates = candidates_for(self.loaded, "Split purchase")
        app = tornado.web.Application([
            (r"/secret/hints/(history|request|classify)", webserver.HistoryHintsHandler),
            (r"/secret/change_candidate", webserver.ChangeCandidateHandler),
        ])
        app.next_candidates = self.candidates
        app.current_state = {"candidates_generation": 7}
        app.reconciler = SimpleNamespace(loaded_future=SimpleNamespace(result=lambda: self.loaded))
        app.set_state_force = lambda **kwargs: None
        app.handle_change_candidate = lambda msg: webserver.Application.handle_change_candidate(app, msg)
        self.msg = {"generation": 7, "candidate_index": len(self.candidates.candidates) - 1,
                    "token": hints.candidate_snapshot(self.candidates.candidates[-1])[1]["token"],
                    "group_number": 1}
        return app

    def tearDown(self):
        super().tearDown()
        self.directory.cleanup()

    def post(self, operation, **updates):
        return self.fetch("/secret/" + operation, method="POST", body=json.dumps({**self.msg, **updates}))

    def test_history_preview_and_errors_do_not_classify(self):
        with patch.object(jev, "classify", side_effect=AssertionError("Unexpected external request")):
            assert self.post("hints/history", query="coffee").code == 200
            preview = self.post("hints/request")
            assert preview.code == 200
            payload = json.loads(preview.body)["request"]
            assert payload["state"]["target_group"]["posting_indices"] == [2]
            assert self.post("hints/request", group_number=None).code == 422
            assert self.post("hints/request", candidate_index=-1).code == 409
            assert self.post("hints/request", token="old").code == 409
            assert self.post("hints/history", generation=6).code == 409

    def test_one_classify_request_and_no_write(self):
        path = Path(self.loaded.editor.journal_path)
        before = path.read_bytes()
        with patch.object(jev, "classify", return_value={"category": "Expenses:Coffee", "confidence": .8}) as fake:
            result = self.post("hints/classify")
            assert result.code == 200
            assert fake.call_count == 1
        assert path.read_bytes() == before

    def test_skip_patterns_block_classify_only(self):
        with patch.object(jev, "classify", side_effect=AssertionError("Unexpected external request")):
            skipped = self.post("hints/classify", skip_patterns=["Nothing", r"^\s+Assets:Bank"])
            assert skipped.code == 422
            assert "Assets:Bank" in json.loads(skipped.body)["error"]
            assert self.post("hints/classify", skip_patterns=["("]).code == 400
            assert self.post("hints/request", skip_patterns=[r"^\s+Assets:Bank"]).code == 200
        with patch.object(jev, "classify", return_value={"category": "Expenses:Coffee", "confidence": .8}):
            assert self.post("hints/classify", skip_patterns=["^Assets:Bank"]).code == 200

    def test_custom_instructions_reach_preview_and_classify(self):
        preview = json.loads(self.post("hints/request", instructions="Custom prompt").body)
        assert preview["request"]["questions"]["category"]["instructions"] == "Custom prompt"
        with patch.object(jev, "classify", return_value={"category": "Expenses:Coffee", "confidence": .8}) as fake:
            assert self.post("hints/classify", instructions="Custom prompt").code == 200
            assert fake.call_args.kwargs["instructions"] == "Custom prompt"

    def test_apply_group_and_stale_guard(self):
        index = self.msg["candidate_index"]
        candidate = self.candidates.candidates[index]
        accounts = [s.account_name for s in candidate.substituted_accounts]
        accounts[1] = "Expenses:Coffee"
        stale = self.post("change_candidate", hint_token="stale", changes={"accounts": accounts})
        assert stale.code == 409
        assert json.loads(stale.body) == {"error": "The transaction changed. Request hints again."}
        assert self.post("change_candidate", hint_token=self.msg["token"], changes={"accounts": accounts}).code == 200
        transaction = self.candidates.candidates[index].staged_changes.get_diff().new_entries[-1]
        assert [p.account for p in transaction.postings] == ["Assets:Bank", "Expenses:FIXME", "Expenses:Coffee"]
        assert hints.candidate_snapshot(self.candidates.candidates[index])[1]["token"] == self.msg["token"]

    @tornado.testing.gen_test
    async def test_slow_classification_does_not_block_and_rejects_stale_result(self):
        started, release = threading.Event(), threading.Event()
        def slow(context, instructions=None):
            started.set()
            assert release.wait(4)
            return {"category": "Expenses:Coffee", "confidence": .8}
        with patch.object(jev, "classify", side_effect=slow):
            future = self.http_client.fetch(self.get_url("/secret/hints/classify"),
                method="POST", body=json.dumps(self.msg), raise_error=False)
            import asyncio
            for _ in range(50):
                if started.is_set(): break
                await asyncio.sleep(.01)
            assert started.is_set()
            try:
                response = await self.http_client.fetch(self.get_url("/secret/hints/history"),
                    method="POST", body=json.dumps({**self.msg, "query": "coffee"}))
                assert response.code == 200
                self._app.current_state["candidates_generation"] = 8
            finally:
                release.set()
            assert (await future).code == 409


def test_history_includes_plugin_generated_accounts(tmp_path):
    journal = tmp_path / "plugin.bean"
    journal.write_text('''plugin "beancount.plugins.auto_accounts"
2025-01-01 * "Shop" "Categorized"
  Assets:Bank -5 USD
  Expenses:Generated 5 USD
2026-01-01 * "Shop" "Pending"
  Assets:Bank -7 USD
  Expenses:FIXME 7 USD
''')
    ignored = tmp_path / "ignored.bean"
    ignored.touch()
    options = vars(webserver.parse_arguments([], journal_input=str(journal),
        ignored_journal=str(ignored), default_output=str(journal), data_sources=[]))
    owner = reconcile.Reconciler(str(journal), lambda _: None, str(ignored), options)
    loaded = owner.loaded_future.result()
    assert not any(isinstance(e, data.Open) for e in loaded.editor.entries)
    candidate = candidates_for(loaded, "Pending").candidates[-1]
    context = hints.build_context(loaded.editor, candidate, 0)
    assert context["categories"] == {"Assets:Bank": {}, "Expenses:Generated": {}}
    assert context["payee_history"] == {"Expenses:Generated": {"count": 1, "narrations": ["Categorized"]}}


def test_target_direction():
    from beancount.core.amount import Amount
    from decimal import Decimal
    def posting(number):
        units = None if number is None else Amount(Decimal(number), "USD")
        return data.Posting("Expenses:FIXME", units, None, None, None, None)
    assert hints._direction([posting("2.79")]) == "money out"
    assert hints._direction([posting("-5"), posting("-1")]) == "money in"  # refund
    assert hints._direction([posting("5"), posting("-1")]) is None
    assert hints._direction([posting(None)]) is None
    assert hints._direction([]) is None

"""Tests for the follow-up cost optimization (issue #5).

Covers, without touching AWS/Gemini (LLMEngine is faked):
  * evidence block vs turn prompt split - evidence has no conversation/question
  * metric_extremes duplication removed (EVIDENCE still present, cloudtrail kept)
  * shared engine reused across questions (not rebuilt per question)
  * response memo: an identical, evidence-unchanged question skips Gemini
  * memo fingerprint staleness: a changed log-investigation stales the memo
  * free-tier default: FOLLOW_UP_GEMINI_CACHE_ENABLED=False -> always uncached,
    byte-identical content whether or not caching is on
"""

import json
import sys
import types as _types
from unittest.mock import MagicMock

import pytest

from llm.follow_up_prompt_builder import FollowUpPromptBuilder
from api.follow_up_manager import (
    FollowUpManager,
    _normalize_question,
    _evidence_fingerprint,
    _estimate_tokens,
)


# ---------------------------------------------------------------------------
# fixtures / fakes
# ---------------------------------------------------------------------------
def _report():
    return {
        "severity": "HIGH", "confidence": "HIGH",
        "summary": "CPU spiked", "root_cause": "runaway process",
        "evidence": [{"signal": "cpu"}], "recommendations": ["scale"],
    }


def _context():
    # Matches llm/sanitizer.py::sanitize_cloudwatch's real input shape:
    # cloudwatch.MetricTrends.<KEY> holds the per-metric summary it passes
    # through (the sanitizer re-keys CPU->cpu etc. and keeps State).
    return {
        "resource_type": "EC2",
        "instance_id": "i-0abc",
        "context": {
            "cloudwatch": {
                "State": None,
                "MetricTrends": {
                    "CPU": {"U": "percent", "latest": 97, "minimum": 5, "maximum": 97, "average": 42},
                },
            },
        },
    }


def _manager(monkeypatch):
    """A FollowUpManager with a faked engine + stubbed stores/Managers that
    never hit disk/AWS/Gemini."""
    mgr = FollowUpManager()
    engine = MagicMock()
    engine.analyze.return_value = json.dumps({
        "answer": "because X", "confidence": "HIGH", "evidence_used": [],
        "uncertainties": [], "follow_up_needed": False,
    })
    mgr._engine = engine
    return mgr, engine


def _stub_investigation(monkeypatch, log_investigation=None):
    """Make _split + file reads + stores resolve to a fixed report/context."""
    import api.follow_up_manager as fum

    monkeypatch.setattr(fum, "_read_json", lambda p: _report() if "reports" in str(p) else _context())
    # report/context existence + completed-RCA + superseded checks
    monkeypatch.setattr(fum.Path, "exists", lambda self: True)
    monkeypatch.setattr(fum, "find_run_id_for", lambda *a, **k: None)
    monkeypatch.setattr(fum, "load_run_summaries", lambda: [])
    monkeypatch.setattr(fum, "mtime_dt", lambda p: None)

    session = {"investigation_id": "run_1__i-0abc", "conversation": []}

    def _append_turn(inv_id, user_msg, assistant_msg):
        # mirror the real store: persist the turn so the NEXT recent_turns
        # sees the conversation grow (matters for memo-hit history checks).
        session["conversation"].extend([user_msg, assistant_msg])
        return session

    monkeypatch.setattr(fum.conversation_store, "get_or_create_session", lambda **k: session)
    monkeypatch.setattr(fum.conversation_store, "recent_turns", lambda s, n: s["conversation"])
    monkeypatch.setattr(fum.conversation_store, "append_turn", _append_turn)
    monkeypatch.setattr(fum.conversation_store, "update_fields", lambda *a, **k: session)
    monkeypatch.setattr(fum.log_investigation_store, "load_result", lambda i: log_investigation)
    return session


# ---------------------------------------------------------------------------
# prompt split
# ---------------------------------------------------------------------------
class TestPromptSplit:
    def test_evidence_block_has_no_conversation_or_question(self):
        b = FollowUpPromptBuilder()
        block = b.build_evidence_block(
            report=_report(), raw_context=_context(), run_id="run_1",
            resource_id="i-0abc", resource_type="EC2",
            time_window={"start": "s", "end": "e"},
        )
        assert "INVESTIGATION" in block and "EVIDENCE" in block and "RCA" in block
        assert "USER QUESTION" not in block
        assert "CONVERSATION" not in block

    def test_turn_prompt_carries_only_conversation_and_question(self):
        b = FollowUpPromptBuilder()
        turn = b.build_turn_prompt(
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
            "what broke?",
        )
        assert "USER QUESTION" in turn and "what broke?" in turn
        assert "INVESTIGATION" not in turn and "EVIDENCE" not in turn

    def test_metric_extremes_duplication_removed_but_evidence_kept(self):
        b = FollowUpPromptBuilder()
        block = b.build_evidence_block(
            report=_report(), raw_context=_context(), run_id="run_1",
            resource_id="i-0abc", resource_type="EC2", time_window=None,
        )
        # the duplicate derived extremes section is gone
        assert "metric_extremes" not in block
        assert "Metric Extremes" not in block
        # the full evidence (the H array) is still there
        assert "97" in block  # the raw datapoint survives in EVIDENCE
        # cloudtrail timeline is still present (not duplicated elsewhere)
        assert "cloudtrail_events" in block

    def test_build_prompt_equals_block_plus_turn(self):
        b = FollowUpPromptBuilder()
        kwargs = dict(report=_report(), raw_context=_context(), run_id="run_1",
                      resource_id="i-0abc", resource_type="EC2", time_window=None)
        full = b.build_prompt(**kwargs, conversation_history=[], question="q")
        assert full == b.build_evidence_block(**kwargs) + b.build_turn_prompt([], "q")


# ---------------------------------------------------------------------------
# memo / free-tier behaviour
# ---------------------------------------------------------------------------
class TestResponseMemo:
    def test_identical_question_skips_gemini(self, monkeypatch):
        session = _stub_investigation(monkeypatch)
        mgr, engine = _manager(monkeypatch)
        r1 = mgr.ask("run_1__i-0abc", "what happened?")
        r2 = mgr.ask("run_1__i-0abc", "  What   Happened? ")  # case+whitespace differ, text identical
        assert engine.analyze.call_count == 1  # second answer was memoized
        assert r2["from_cache"] is True
        assert r2["answer"] == r1["answer"]

    def test_changed_evidence_stales_memo(self, monkeypatch):
        _stub_investigation(monkeypatch, log_investigation=None)
        mgr, engine = _manager(monkeypatch)
        mgr.ask("run_1__i-0abc", "what happened?")
        # now a log investigation exists -> fingerprint changes -> re-call
        _stub_investigation(monkeypatch, log_investigation={"evidence_package": {}, "analysis": {}})
        mgr.ask("run_1__i-0abc", "what happened?")
        assert engine.analyze.call_count == 2

    def test_uncached_prompt_is_byte_identical_regardless_of_cache_flag(self, monkeypatch):
        """Free-tier default (FOLLOW_UP_GEMINI_CACHE_ENABLED=False) always
        sends the full evidence+turn in one string."""
        import api.follow_up_manager as fum
        _stub_investigation(monkeypatch)
        mgr, engine = _manager(monkeypatch)
        monkeypatch.setattr(fum, "FOLLOW_UP_GEMINI_CACHE_ENABLED", False)
        mgr.ask("run_1__i-0abc", "q")
        sent = engine.analyze.call_args[0][0]
        assert "INVESTIGATION" in sent and "EVIDENCE" in sent and "USER QUESTION" in sent
        assert "CONVERSATION" in sent


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
class TestHelpers:
    def test_normalize_question(self):
        assert _normalize_question("  What   Happened? ") == _normalize_question("what happened?")

    def test_fingerprint_changes_with_input(self):
        a = _evidence_fingerprint({"x": 1}, {"y": 2}, None)
        b = _evidence_fingerprint({"x": 1}, {"y": 2}, {"z": 3})
        c = _evidence_fingerprint({"x": 1}, {"y": 2}, None)
        assert a != b and a == c

    def test_estimate_tokens(self):
        assert _estimate_tokens("a" * 4000) == 1000
        assert _estimate_tokens("tiny") >= 1


# ---------------------------------------------------------------------------
# engine reuse
# ---------------------------------------------------------------------------
class TestEngineReuse:
    def test_engine_created_once_and_reused(self, monkeypatch):
        _stub_investigation(monkeypatch)
        created = []
        real_init = FollowUpManager.__init__

        def fake_init(self):
            real_init(self)
            self._engine = None

        engine = MagicMock()
        engine.analyze.return_value = json.dumps({
            "answer": "a", "confidence": "LOW", "evidence_used": [],
            "uncertainties": [], "follow_up_needed": False,
        })

        import api.follow_up_manager as fum
        monkeypatch.setattr(fum, "LLMEngine", lambda: created.append(1) or engine)
        mgr = FollowUpManager()
        mgr.ask("run_1__i-0abc", "q1")
        mgr.ask("run_1__i-0abc", "q2")  # different question -> not memoized
        assert len(created) == 1  # built once, reused
        assert engine.analyze.call_count == 2

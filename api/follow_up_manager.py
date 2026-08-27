"""
=========================================================
AI SRE AGENT
Module : Follow-Up Service
Purpose:
    Answers evidence-grounded follow-up questions about a completed
    infrastructure investigation. Completely separate from
    api/investigation_manager.py (which runs collectors -> Gemini RCA) and
    api/cost_explorer_manager.py - this module never collects new AWS
    telemetry and never starts a new investigation; it only reads an
    EXISTING report + context (output/reports/<resource_id>.json,
    output/context/<resource_id>.json - the same files
    analyzer/report_writer.py and context/context_builder.py already
    produce) and asks Gemini a follow-up question about them.

    investigation_id is f"{run_id}__{resource_id}" (see
    _split_investigation_id) - an opaque identifier the dashboard already
    has both halves of at the exact point it renders a completed report
    (see dashboard/components/views/report_viewer.py). A mismatch between
    the run_id half and the resource's *current* report (using the same
    "advisory run_id" derivation utils/dashboard_export.py already uses to
    tag reports.json) means the resource has been reinvestigated since -
    the original report is immutable, so a follow-up about a superseded
    run is rejected with a clear message rather than silently answered
    against newer evidence.
=========================================================
"""

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from config.settings import FOLLOW_UP_MAX_QUESTION_LENGTH, FOLLOW_UP_PROMPT_HISTORY_MESSAGES
from llm.follow_up_prompt_builder import FollowUpPromptBuilder
from llm.llm_engine import LLMEngine
from utils import conversation_store
from utils.dashboard_export import CONTEXT_DIR, REPORTS_DIR, find_run_id_for, load_run_summaries, mtime_dt
from utils.logger import get_logger

logger = get_logger("FollowUpManager")

SUMMARY_DIR = Path("output/summary")


class InvestigationNotFoundError(Exception):
    """No report/context exists for the requested resource, or
    investigation_id is malformed."""


class InvestigationSupersededError(Exception):
    """The resource has been reinvestigated since the run_id encoded in
    investigation_id - the original report is immutable, so the follow-up
    cannot be grounded in evidence that no longer matches what's on disk."""


class InvalidQuestionError(Exception):
    """Empty or oversized question."""


class FollowUpUnavailableError(Exception):
    """Gemini call failed/timed out. Conversation state is untouched -
    nothing is persisted for a request that never got an answer."""


def _split_investigation_id(investigation_id: str):
    if not investigation_id or "__" not in investigation_id:
        raise InvestigationNotFoundError(f"Malformed investigation_id: {investigation_id!r}")
    run_id, _, resource_id = investigation_id.rpartition("__")
    if not run_id or not resource_id:
        raise InvestigationNotFoundError(f"Malformed investigation_id: {investigation_id!r}")
    return run_id, resource_id


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _time_window_for(run_id: str) -> Optional[Dict[str, Optional[str]]]:
    """Best-effort - output/summary/<run_id>.json (utils/execution_summary.py's
    own output) has the run's real start_time/end_time, if that run's
    summary hasn't rotated out of MAX_RUN_HISTORY. None (never a
    fabricated window) when it's gone."""
    data = _read_json(SUMMARY_DIR / f"{run_id}.json")
    if not data:
        return None
    return {"start": data.get("start_time"), "end": data.get("end_time")}


def _parse_response(raw_response: str) -> Dict[str, Any]:
    """Parses Gemini's JSON response into the follow-up schema. Never
    crashes on a malformed response - falls back to a friendly, honest
    message (never a bare "No answer available") and marks parsed=False
    so logs/tests can distinguish a genuine Gemini answer from a parse
    failure."""
    try:
        data = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError):
        return {
            "answer": (
                "The investigation data is available, but the AI could not produce a "
                "well-formed answer for this question. Please try rephrasing it."
            ),
            "confidence": "LOW", "evidence_used": [],
            "uncertainties": ["Gemini's response for this question could not be parsed."],
            "follow_up_needed": False, "parsed": False,
        }

    if not isinstance(data, dict) or not data.get("answer"):
        return {
            "answer": (
                "The investigation data is available, but the AI did not return a usable "
                "answer for this question. Please try again."
            ),
            "confidence": "LOW", "evidence_used": [],
            "uncertainties": ["Gemini's response for this question was missing an answer."],
            "follow_up_needed": False, "parsed": False,
        }

    confidence = str(data.get("confidence") or "LOW").upper()
    if confidence not in ("HIGH", "MEDIUM", "LOW"):
        confidence = "LOW"

    return {
        "answer": data["answer"],
        "confidence": confidence,
        "evidence_used": data.get("evidence_used") or [],
        "uncertainties": data.get("uncertainties") or [],
        "follow_up_needed": bool(data.get("follow_up_needed", False)),
        "parsed": True,
    }


class FollowUpManager:

    def ask(self, investigation_id: str, question: str) -> Dict[str, Any]:
        question = (question or "").strip()
        if not question:
            raise InvalidQuestionError("Question must not be empty.")
        if len(question) > FOLLOW_UP_MAX_QUESTION_LENGTH:
            raise InvalidQuestionError(
                f"Question is too long ({len(question)} characters, max {FOLLOW_UP_MAX_QUESTION_LENGTH})."
            )

        run_id, resource_id = _split_investigation_id(investigation_id)

        report_path = REPORTS_DIR / f"{resource_id}.json"
        context_path = CONTEXT_DIR / f"{resource_id}.json"

        if not report_path.exists() or not context_path.exists():
            raise InvestigationNotFoundError(
                f"No investigation report found for resource '{resource_id}'. Run an "
                "investigation for this resource before asking a follow-up question."
            )

        report = _read_json(report_path)
        raw_context = _read_json(context_path)

        if not report or not any(report.get(k) for k in ("summary", "root_cause", "severity")):
            raise InvestigationNotFoundError(
                f"Resource '{resource_id}' has no completed RCA yet - this investigation "
                "is not ready for follow-up questions."
            )

        # Immutability check: output/reports/<resource_id>.json is
        # "latest per resource," not versioned per run (see module
        # docstring). Confirm the run_id the caller claims still matches
        # what's actually on disk, using the exact same advisory-run_id
        # derivation the dashboard feed already uses to tag reports.json,
        # so this check is provably consistent with what the user saw
        # when they opened this report.
        current_run_id = find_run_id_for(load_run_summaries(), mtime_dt(report_path))
        if current_run_id and current_run_id != run_id:
            raise InvestigationSupersededError(
                f"This report has been superseded by a newer investigation of "
                f"'{resource_id}' (run {current_run_id}). Reopen the current report to "
                "ask follow-up questions."
            )

        resource_type = raw_context.get("resource_type")
        time_window = _time_window_for(run_id)

        session = conversation_store.get_or_create_session(
            investigation_id=investigation_id,
            run_id=run_id,
            resource_id=resource_id,
            resource_type=resource_type,
            report_reference=str(report_path),
            context_reference=str(context_path),
        )

        history = conversation_store.recent_turns(session, FOLLOW_UP_PROMPT_HISTORY_MESSAGES)

        prompt = FollowUpPromptBuilder().build_prompt(
            report=report,
            raw_context=raw_context,
            run_id=run_id,
            resource_id=resource_id,
            resource_type=resource_type,
            time_window=time_window,
            conversation_history=history,
            question=question,
        )

        # -----------------------------------------------------------
        # Phase 2 seam (not implemented): an "is evidence sufficient?"
        # check would run here, before the Gemini call, and could
        # short-circuit straight to an "evidence insufficient" answer
        # without spending a Gemini call at all. Phase 3 would extend
        # this same point to plan/execute an approved tool (via a future
        # ToolRegistry/ToolExecutor - never eval()/exec()/shell) and
        # re-enter with new evidence before calling Gemini. Neither
        # exists yet - every follow-up in this phase goes straight to
        # Gemini with the evidence already assembled above.
        # -----------------------------------------------------------

        started = time.monotonic()
        try:
            raw_response = LLMEngine().analyze(prompt)
        except Exception as exc:
            logger.error(f"Follow-up Gemini call failed for investigation_id={investigation_id}: {exc}")
            raise FollowUpUnavailableError(
                "The investigation data is available, but the AI follow-up analysis is "
                "temporarily unavailable. Please try again."
            ) from exc
        gemini_latency_seconds = time.monotonic() - started

        answer = _parse_response(raw_response)

        now = datetime.now(timezone.utc).isoformat()
        user_message = {"message_id": str(uuid.uuid4()), "role": "user", "content": question, "timestamp": now}
        assistant_message = {
            "message_id": str(uuid.uuid4()),
            "role": "assistant",
            "content": answer["answer"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "evidence_used": answer["evidence_used"],
            "confidence": answer["confidence"],
        }

        conversation_store.append_turn(investigation_id, user_message, assistant_message)

        logger.info(
            f"follow_up investigation_id={investigation_id} "
            f"gemini_latency_seconds={gemini_latency_seconds:.2f} "
            f"response_parsed={answer['parsed']} confidence={answer['confidence']}"
        )

        return {
            "investigation_id": investigation_id,
            "question": question,
            "answer": answer["answer"],
            "confidence": answer["confidence"],
            "evidence_used": answer["evidence_used"],
            "uncertainties": answer["uncertainties"],
            "follow_up_needed": answer["follow_up_needed"],
        }

    def get_conversation(self, investigation_id: str) -> Dict[str, Any]:
        _split_investigation_id(investigation_id)  # validates shape; raises if malformed
        session = conversation_store.load_session(investigation_id)
        if session is None:
            return {"investigation_id": investigation_id, "conversation": []}
        return session

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

    If a Log Investigation (api/log_investigation_manager.py) has been
    performed for this same investigation_id, its ALREADY-PERSISTED,
    ALREADY-SANITIZED result (utils/log_investigation_store.py::load_result -
    the existing, public read function) is read here too and handed to
    FollowUpPromptBuilder - this module never re-fetches logs, never
    re-sanitizes, and never calls AWS on behalf of a follow-up question.
=========================================================
"""

import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from config.settings import (
    FOLLOW_UP_GEMINI_CACHE_ENABLED,
    FOLLOW_UP_GEMINI_CACHE_MIN_TOKENS,
    FOLLOW_UP_GEMINI_CACHE_TTL_SECONDS,
    FOLLOW_UP_MAX_QUESTION_LENGTH,
    FOLLOW_UP_PROMPT_HISTORY_MESSAGES,
    FOLLOW_UP_RESPONSE_CACHE_ENABLED,
    GEMINI_MODEL,
)
from llm.follow_up_prompt_builder import FollowUpPromptBuilder
from llm.llm_engine import LLMEngine
from utils import conversation_store, log_investigation_store
from utils.dashboard_export import CONTEXT_DIR, REPORTS_DIR, find_run_id_for, load_run_summaries, mtime_dt
from utils.logger import get_logger
from utils.path_safety import validate_file_id

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
    """Splits f"{run_id}__{resource_id}" AND validates every half for
    filesystem safety (issue #1): a shape-only check admits traversal
    strings like "run__../../x" that would otherwise be joined into
    output/reports/, output/conversations/ and similar paths. Any failure
    is raised as InvestigationNotFoundError (400/404 to the caller) so an
    invalid ID can never reach a Path join."""
    if not investigation_id or "__" not in investigation_id:
        raise InvestigationNotFoundError(f"Malformed investigation_id: {investigation_id!r}")
    run_id, _, resource_id = investigation_id.rpartition("__")
    if not run_id or not resource_id:
        raise InvestigationNotFoundError(f"Malformed investigation_id: {investigation_id!r}")
    try:
        validate_file_id(run_id, "run_id")
        validate_file_id(resource_id, "resource_id")
        validate_file_id(investigation_id, "investigation_id")
    except ValueError as exc:
        raise InvestigationNotFoundError(f"Malformed investigation_id: {exc}") from exc
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


def _normalize_question(question: str) -> str:
    """Case/whitespace-insensitive key for the response memo, so "What
    happened?" and "  what happened " map to the same entry."""
    return re.sub(r"\s+", " ", question.strip().lower())


def _evidence_fingerprint(*parts) -> str:
    """Stable fingerprint of the evidence an answer was grounded in. A stored
    memo/cache is only reused while this is unchanged - so running a Log
    Investigation later (which changes its part) automatically stales prior
    memos instead of serving evidence that no longer matches."""
    h = hashlib.sha256()
    for part in parts:
        h.update(json.dumps(part, sort_keys=True, default=str).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) used only to decide whether the
    evidence block clears Gemini's 1024-token caching floor. Deliberately
    approximate - the exact count is Gemini's, and a wrong-by-a-bit estimate
    only flips between 'skip the cache attempt' and 'attempt + gracefully
    fall back', both safe."""
    return max(1, len(text) // 4)


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

    def __init__(self):
        # Reused across questions (issue #5) instead of rebuilt per question.
        self._engine: Optional[LLMEngine] = None

    def ask(self, investigation_id: str, question: str) -> Dict[str, Any]:
        question = (question or "").strip()
        if not question:
            raise InvalidQuestionError("Question must not be empty.")
        if len(question) > FOLLOW_UP_MAX_QUESTION_LENGTH:
            raise InvalidQuestionError(
                f"Question is too long ({len(question)} characters, max {FOLLOW_UP_MAX_QUESTION_LENGTH})."
            )

        # One engine per process, lazily created and reused across questions
        # (issue #5) - the old code built a new genai.Client per question.
        if self._engine is None:
            self._engine = LLMEngine()

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

        # Reuses the EXISTING, already-public read function - None (not an
        # error) when "Investigate Logs" has never been clicked for this
        # investigation. No AWS call, no re-fetch, no re-sanitization.
        log_investigation = log_investigation_store.load_result(investigation_id)

        # --- Response memo (issue #5, free-tier saving) -------------------
        # Identical, evidence-unchanged questions are answered from a local
        # memo instead of spending another Gemini call. The fingerprint is
        # derived from the evidence sources (report + context + log
        # investigation), so a memo is only ever reused while the evidence
        # it was produced from is unchanged - if a Log Investigation is run
        # later, the fingerprint changes and stales memos are ignored.
        builder = FollowUpPromptBuilder()
        evidence_fingerprint = _evidence_fingerprint(report, raw_context, log_investigation)
        memo_key = _normalize_question(question)
        memos = session.setdefault("response_memo", {})

        if FOLLOW_UP_RESPONSE_CACHE_ENABLED:
            hit = memos.get(memo_key)
            if hit and hit.get("fingerprint") == evidence_fingerprint:
                logger.info(
                    f"follow_up investigation_id={investigation_id} memo=hit "
                    f"(identical question, evidence unchanged) - no Gemini call"
                )
                cached = hit["response"]
                return {
                    "investigation_id": investigation_id,
                    "question": question,
                    "answer": cached["answer"],
                    "confidence": cached["confidence"],
                    "evidence_used": cached["evidence_used"],
                    "uncertainties": cached["uncertainties"],
                    "follow_up_needed": cached["follow_up_needed"],
                    "from_cache": True,
                }

        # --- Immutable evidence block (issue #5) --------------------------
        # Build + sanitize ONCE. The per-question prompt is just the recent
        # conversation + the question - no re-sanitize, no re-derive.
        evidence_block = builder.build_evidence_block(
            report=report,
            raw_context=raw_context,
            run_id=run_id,
            resource_id=resource_id,
            resource_type=resource_type,
            time_window=time_window,
            log_investigation=log_investigation,
        )
        turn_prompt = builder.build_turn_prompt(history, question)

        raw_response = self._generate(
            investigation_id=investigation_id,
            session=session,
            evidence_block=evidence_block,
            turn_prompt=turn_prompt,
        )

        answer = _parse_response(raw_response)

        # Persist the memo after a real (parsed) answer so a later identical
        # question on unchanged evidence is served locally.
        if answer["parsed"]:
            memos[memo_key] = {
                "fingerprint": evidence_fingerprint,
                "response": {
                    "answer": answer["answer"],
                    "confidence": answer["confidence"],
                    "evidence_used": answer["evidence_used"],
                    "uncertainties": answer["uncertainties"],
                    "follow_up_needed": answer["follow_up_needed"],
                },
            }

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

        # Persist the response memo so an identical question survives a
        # restart and a duplicate *in-flight* request doesn't double-spend.
        # append_turn already wrote the session; this second write adds the
        # (small) memo. Best-effort - a memo is a hint, not state.
        if answer["parsed"]:
            try:
                conversation_store.update_fields(
                    investigation_id, {"response_memo": memos}
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"could not persist response memo for {investigation_id}: {exc}")

        logger.info(
            f"follow_up investigation_id={investigation_id} gen_source={self._last_gen_source} "
            f"gemini_latency_seconds={self._last_gemini_latency:.2f} "
            f"response_parsed={answer['parsed']} confidence={answer['confidence']} memo=miss"
        )

        return {
            "investigation_id": investigation_id,
            "question": question,
            "answer": answer["answer"],
            "confidence": answer["confidence"],
            "evidence_used": answer["evidence_used"],
            "uncertainties": answer["uncertainties"],
            "follow_up_needed": answer["follow_up_needed"],
            "from_cache": False,
        }

    # ------------------------------------------------------------------
    # Generation path (issue #5)
    # ------------------------------------------------------------------
    def _generate(self, *, investigation_id, session, evidence_block, turn_prompt) -> str:
        """Produce a raw Gemini response for this question. Uses a
        server-side cached_contents block as the immutable evidence when
        FOLLOW_UP_GEMINI_CACHE_ENABLED and the block is large enough
        (>=FOLLOW_UP_GEMINI_CACHE_MIN_TOKENS); every cache failure (free
        tier storage quota 0, expired/deleted cache, below the token floor)
        degrades gracefully to the single uncached prompt, which sends
        Gemini byte-identical content. Sets the observability attributes
        _last_gemini_latency/_last_gen_source read by ask()."""

        started = time.monotonic()
        self._last_gemini_latency = 0.0
        self._last_gen_source = "uncached"

        try:
            if FOLLOW_UP_GEMINI_CACHE_ENABLED:
                raw = self._generate_cached(
                    investigation_id=investigation_id,
                    session=session,
                    evidence_block=evidence_block,
                    turn_prompt=turn_prompt,
                )
            else:
                raw = self._engine.analyze(evidence_block + turn_prompt)
        except Exception as exc:
            logger.error(f"Follow-up Gemini call failed for investigation_id={investigation_id}: {exc}")
            raise FollowUpUnavailableError(
                "The investigation data is available, but the AI follow-up analysis is "
                "temporarily unavailable. Please try again."
            ) from exc
        self._last_gemini_latency = time.monotonic() - started
        return raw

    def _generate_cached(self, *, investigation_id, session, evidence_block, turn_prompt) -> str:
        """Paid-tier path: cache the evidence block once, then reference it
        per question. Re-creates the cache on expiry/deletion mid-conversation
        (sliding TTL - see FOLLOW_UP_GEMINI_CACHE_TTL_SECONDS) so a cached
        conversation effectively never dies; it only evaporates when the user
        stops asking. Falls back to uncached on ANY cache error so a question
        is never dropped because caching misbehaved."""
        if _estimate_tokens(evidence_block) < FOLLOW_UP_GEMINI_CACHE_MIN_TOKENS:
            self._last_gen_source = "uncached_below_floor"
            return self._engine.analyze(evidence_block + turn_prompt)

        meta = session.get("cache_meta") or {}
        cache_name = meta.get("name")

        for attempt in (1, 2):
            if cache_name is None:
                try:
                    cache = self._engine.create_cache(
                        model=GEMINI_MODEL,
                        contents=evidence_block,
                        ttl_seconds=FOLLOW_UP_GEMINI_CACHE_TTL_SECONDS,
                        display_name=f"followup-{investigation_id}",  # already path-validated
                    )
                    cache_name = cache.name
                    session["cache_meta"] = {"name": cache_name}
                    self._save_cache_meta(investigation_id, session)
                except Exception as exc:  # free tier quota 0, network, etc.
                    logger.warning(f"follow_up cache create failed ({exc}); using uncached prompt")
                    self._last_gen_source = "uncached_create_failed"
                    return self._engine.analyze(evidence_block + turn_prompt)
            try:
                raw = self._engine.analyze_with_cache(turn_prompt, cache_name)
                self._last_gen_source = "cached" if attempt == 1 else "cached_recreated"
                return raw
            except Exception as exc:
                logger.warning(f"follow_up cached call failed on attempt {attempt} ({exc})")
                if attempt == 1:
                    cache_name = None  # recreate once, then give up -> uncached
                else:
                    self._last_gen_source = "uncached_after_cache_errors"
                    return self._engine.analyze(evidence_block + turn_prompt)

        return self._engine.analyze(evidence_block + turn_prompt)

    @staticmethod
    def _save_cache_meta(investigation_id: str, session: Dict[str, Any]) -> None:
        """Persist cache_meta alongside the session so a server restart can
        reuse (or transparently recreate) the same cache instead of leaking a
        new one per boot. Reuses the store's own atomic write + per-id lock.
        Failures never break the question - cache_meta is a hint, not state."""
        try:
            conversation_store.update_fields(investigation_id, {"cache_meta": session.get("cache_meta")})
        except Exception as exc:  # noqa: BLE001 - persistence is best-effort
            logger.debug(f"could not persist cache_meta for {investigation_id}: {exc}")

    def get_conversation(self, investigation_id: str) -> Dict[str, Any]:
        _split_investigation_id(investigation_id)  # validates shape; raises if malformed
        session = conversation_store.load_session(investigation_id)
        if session is None:
            return {"investigation_id": investigation_id, "conversation": []}
        return session

"""
=========================================================
AI SRE AGENT
Module : Conversation Store
Purpose:
    Persist Follow-Up Question conversations, one JSON file per
    investigation - output/conversations/<investigation_id>.json.
    Completely separate from the RCA pipeline's own output/reports/ and
    output/context/ trees: this module only ever *reads references* to
    those files (report_reference/context_reference), it never
    duplicates their content, and it never writes to them.

    investigation_id is an opaque, pre-built string
    (f"{run_id}__{resource_id}" - see api/follow_up_service.py) - this
    module treats it as nothing more than a filename stem.

    Concurrency: one threading.Lock per investigation_id, created lazily.
    This matches the same in-process-only concurrency model already used
    by api/investigation_manager.py (InvestigationManager._lock) and
    api/cost_explorer_manager.py (CostExplorerManager._lock) - it
    prevents two concurrent follow-up requests for the SAME investigation
    from interleaving their read-modify-write and corrupting/losing a
    turn, but (like those two existing managers) does not provide
    multi-process/multi-worker locking. Documented limitation, not a new
    gap introduced by this feature.
=========================================================
"""

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import FOLLOW_UP_MAX_CONVERSATION_MESSAGES

CONVERSATIONS_DIR = Path("output/conversations")

_locks_guard = threading.Lock()
_locks: Dict[str, threading.Lock] = {}


def _lock_for(investigation_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(investigation_id)
        if lock is None:
            lock = threading.Lock()
            _locks[investigation_id] = lock
        return lock


def _path_for(investigation_id: str) -> Path:
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    return CONVERSATIONS_DIR / f"{investigation_id}.json"


def _atomic_write(path: Path, data: dict) -> None:
    """Write-to-temp + os.replace() - the same technique already used by
    utils/cost_dashboard_export.py, so a reader never observes a
    half-written file."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def load_session(investigation_id: str) -> Optional[Dict[str, Any]]:
    """None when no conversation has ever been started for this
    investigation - a valid, honest "no history yet" state, not an
    error."""
    path = _path_for(investigation_id)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def get_or_create_session(
    investigation_id: str,
    run_id: str,
    resource_id: str,
    resource_type: Optional[str],
    report_reference: str,
    context_reference: str,
) -> Dict[str, Any]:
    """Returns the existing session for this investigation_id, or creates
    a fresh, empty one. Session metadata (run_id/resource_id/references)
    is set once at creation and never mutated afterward - the
    investigation itself is immutable; see FollowUpService's own
    run_id cross-check for what happens when a caller's claimed
    investigation_id no longer matches the resource's current report."""
    with _lock_for(investigation_id):
        existing = load_session(investigation_id)
        if existing is not None:
            return existing

        session = {
            "investigation_id": investigation_id,
            "run_id": run_id,
            "resource_id": resource_id,
            "resource_type": resource_type,
            "report_reference": report_reference,
            "context_reference": context_reference,
            "conversation": [],
        }
        _atomic_write(_path_for(investigation_id), session)
        return session


def append_turn(
    investigation_id: str,
    user_message: Dict[str, Any],
    assistant_message: Dict[str, Any],
) -> Dict[str, Any]:
    """Appends one user turn + one assistant turn, trims the stored
    conversation to FOLLOW_UP_MAX_CONVERSATION_MESSAGES (oldest first),
    and returns the updated session. Guarded by the same per-investigation
    lock get_or_create_session() uses, so two concurrent follow-up
    requests for the same investigation_id can't interleave their
    read-modify-write and silently drop a turn."""
    with _lock_for(investigation_id):
        session = load_session(investigation_id)
        if session is None:
            raise ValueError(f"No conversation session exists for investigation_id={investigation_id!r}")

        session["conversation"].append(user_message)
        session["conversation"].append(assistant_message)

        overflow = len(session["conversation"]) - FOLLOW_UP_MAX_CONVERSATION_MESSAGES
        if overflow > 0:
            session["conversation"] = session["conversation"][overflow:]

        _atomic_write(_path_for(investigation_id), session)
        return session


def recent_turns(session: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    """The last `limit` stored turns - what actually goes into a follow-up
    prompt, bounded independently of how long the full stored history
    (FOLLOW_UP_MAX_CONVERSATION_MESSAGES) has grown."""
    conversation = session.get("conversation") or []
    if limit <= 0:
        return []
    return conversation[-limit:]

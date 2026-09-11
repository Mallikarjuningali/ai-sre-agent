"""
=========================================================
AI SRE AGENT
Module : Log Investigation Store
Purpose:
    Persist the optional Log Investigation feature's results, one JSON
    file per investigation - output/log_investigations/<investigation_id>.json.
    Mirrors utils/conversation_store.py's exact persistence pattern
    (atomic write, one file per opaque investigation_id), but holds a
    single LATEST result per investigation_id (overwritten on re-click),
    not a growing conversation - Log Investigation is a one-shot
    enrichment action, not an ongoing dialogue.

    Never writes to output/reports/ or output/context/ - this module only
    ever reads references to those files (report_reference/
    context_reference), exactly like conversation_store.py does for
    Follow-Up Q&A. The original RCA report is never mutated by this
    feature.

    investigation_id is the same opaque f"{run_id}__{resource_id}" string
    Follow-Up Q&A already established (see api/follow_up_manager.py) -
    this module treats it as nothing more than a filename stem.

    Concurrency: one threading.Lock per investigation_id, created lazily -
    identical mechanism to conversation_store.py's _lock_for(), so two
    concurrent "Investigate Logs" clicks for the SAME investigation can't
    interleave their write and corrupt the result.
=========================================================
"""

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

LOG_INVESTIGATIONS_DIR = Path("output/log_investigations")

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
    LOG_INVESTIGATIONS_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_INVESTIGATIONS_DIR / f"{investigation_id}.json"


def _atomic_write(path: Path, data: dict) -> None:
    """Write-to-temp + os.replace() - the same technique already used by
    utils/conversation_store.py and utils/cost_dashboard_export.py, so a
    reader never observes a half-written file."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp_path, path)


def load_result(investigation_id: str) -> Optional[Dict[str, Any]]:
    """None when this investigation has never had a Log Investigation run
    - a valid, honest "not yet investigated" state, not an error."""
    path = _path_for(investigation_id)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_result(
    investigation_id: str,
    run_id: str,
    resource_id: str,
    resource_type: Optional[str],
    report_reference: str,
    context_reference: str,
    evidence_package: Dict[str, Any],
    analysis: Dict[str, Any],
    investigation_plan: Optional[Dict[str, Any]] = None,
    sources: Optional[Any] = None,
    incident_window: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Overwrites this investigation_id's single stored result -
    re-clicking "Investigate Logs" replaces the prior result, it is never
    appended to (unlike Follow-Up's growing conversation).

    investigation_plan/sources/incident_window are optional, additive
    fields (default None) - existing callers that omit them keep working
    unchanged; api/log_investigation_manager.py now always supplies them."""

    with _lock_for(investigation_id):
        result = {
            "investigation_id": investigation_id,
            "run_id": run_id,
            "resource_id": resource_id,
            "resource_type": resource_type,
            "report_reference": report_reference,
            "context_reference": context_reference,
            "investigation_plan": investigation_plan,
            "sources": sources,
            "incident_window": incident_window,
            "evidence_package": evidence_package,
            "analysis": analysis,
        }
        _atomic_write(_path_for(investigation_id), result)
        return result

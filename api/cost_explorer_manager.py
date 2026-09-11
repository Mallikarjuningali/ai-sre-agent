"""
=========================================================
AI SRE AGENT
Module : Cost Explorer Manager
Purpose:
    Runs a Cost Explorer refresh on demand for the FastAPI layer, in the
    background, and tracks its progress for
    GET /cost-explorer/status/{run_id} - mirroring
    api/investigation_manager.py's own run_id/thread/status-polling
    pattern (see PHASES below), applied here to Cost Explorer's own
    pipeline. A refresh is a handful of boto3 calls plus one Gemini
    call - individually fast, but for the same UI-responsiveness reason
    Full Investigation moved off a blocking HTTP request, this one does
    too.

    Completely separate from api/investigation_manager.py: its own
    lock, its own `_runs` table, its own run_id namespace - a cost
    refresh can never collide with (or be blocked by) an infra
    investigation, and a run_id from one manager is never valid on the
    other's status endpoint.

    Every progress phase below corresponds to a REAL boundary between
    this pipeline's own AWS/Gemini calls (see the on_progress hooks
    threaded into collector/cost_explorer.py::main() and
    analyzer/cost_analyzer.py::CostAnalyzer.run()) - never a synthetic,
    time-based, or interpolated percentage.
=========================================================
"""
from __future__ import annotations

import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import collector.cost_explorer as cost_explorer_collector
from analyzer.cost_analyzer import CostAnalyzer
from utils.cost_dashboard_export import export as export_cost_feed
from utils.logger import get_logger

IST = ZoneInfo("Asia/Kolkata")

logger = get_logger("CostExplorerManager")

PHASES = [
    ("COLLECTING_COST_DATA", "Collecting AWS cost data"),
    ("COLLECTING_ANOMALY_DATA", "Collecting anomaly data"),
    ("BUILDING_CONTEXT", "Building cost context"),
    ("RUNNING_AI_ANALYSIS", "Running AI analysis"),
    ("PERSISTING_REPORT", "Persisting report"),
]

PHASE_LABELS = dict(PHASES)

# Phase-anchor percents - mark real phase-boundary progress, exactly the
# same convention api/investigation_manager.py's own PHASES use (a fixed
# percent per real stage transition, never a time-based interpolation).
PHASE_PERCENTS = {
    "COLLECTING_COST_DATA": 15,
    "COLLECTING_ANOMALY_DATA": 40,
    "BUILDING_CONTEXT": 60,
    "RUNNING_AI_ANALYSIS": 75,
    "PERSISTING_REPORT": 90,
}


class CostExplorerBusyError(Exception):
    """Raised when a Cost Explorer refresh is requested while one is already running."""


class CostExplorerManager:

    def __init__(self):
        self._lock = threading.Lock()
        self._busy = False
        self._runs = {}

    # -----------------------------------------------------
    # Public API - called by api/app.py
    # -----------------------------------------------------

    def start_refresh(
        self, from_date: str | None = None, to_date: str | None = None, tag_key: str | None = None
    ) -> dict:
        """from_date/to_date (optional "YYYY-MM-DD" strings, both
        required together) request a Month/Period Comparison for that
        user-selected range, in addition to the always-run default
        current/previous cost data. tag_key (optional, e.g.
        "Environment"/"Team"/"Project"/"Application" - any real AWS Cost
        Allocation Tag, never hardcoded) requests a tag-based cost
        allocation breakdown for the current period - see
        collector/cost_explorer.py's main() for exactly how both are
        used. Returns immediately with {run_id, status: "QUEUED",
        started_at} - the actual refresh runs in a background thread;
        poll get_status(run_id) for progress."""

        run_id = self._claim_run_id()

        thread = threading.Thread(
            target=self._execute_refresh,
            args=(run_id, from_date, to_date, tag_key),
            daemon=True,
        )
        thread.start()

        return self._start_response(run_id)

    def get_status(self, run_id: str) -> dict | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            snapshot = dict(run)
            snapshot["phases"] = [dict(phase) for phase in run["phases"]]

        started_at = datetime.fromisoformat(snapshot["started_at"])
        elapsed = max(0, int((datetime.now(IST) - started_at).total_seconds()))
        snapshot["elapsed_seconds"] = elapsed

        return snapshot

    # -----------------------------------------------------
    # Run bookkeeping - mirrors InvestigationManager's own, one
    # independent copy so a run_id/lock/_busy flag never crosses managers.
    # -----------------------------------------------------

    def _claim_run_id(self) -> str:
        with self._lock:
            if self._busy:
                raise CostExplorerBusyError(
                    "A Cost Explorer refresh is already running. Wait for it to finish."
                )
            self._busy = True

            run_id = datetime.now(IST).strftime("%d-%m-%Y_%H-%M-%S_cost")

            self._runs[run_id] = {
                "run_id": run_id,
                "status": "QUEUED",
                "phase": None,
                "phase_label": "Queued",
                "percent": 0,
                "started_at": datetime.now(IST).isoformat(),
                "error": None,
                "report": None,
                "phases": [
                    {"key": key, "label": label, "state": "pending"}
                    for key, label in PHASES
                ],
            }

            return run_id

    def _start_response(self, run_id: str) -> dict:
        with self._lock:
            run = self._runs[run_id]
            return {"run_id": run["run_id"], "status": run["status"], "started_at": run["started_at"]}

    def _make_progress_callback(self, run_id: str):

        def on_progress(phase_key: str):
            with self._lock:
                run = self._runs.get(run_id)
                if run is None:
                    return

                reached_active = False
                for phase in run["phases"]:
                    if phase["key"] == phase_key:
                        phase["state"] = "active"
                        reached_active = True
                    elif reached_active:
                        phase["state"] = "pending"
                    else:
                        phase["state"] = "done"

                run["status"] = "RUNNING"
                run["phase"] = phase_key
                run["phase_label"] = PHASE_LABELS[phase_key]
                run["percent"] = PHASE_PERCENTS[phase_key]

        return on_progress

    def _finish(self, run_id: str, status: str, error: str | None = None, report=None):
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                run["status"] = status
                run["error"] = error
                run["report"] = report
                if status == "COMPLETED":
                    run["percent"] = 100
                    run["phase_label"] = "Completed"
                    for phase in run["phases"]:
                        phase["state"] = "done"
                elif status == "FAILED":
                    run["phase_label"] = "Failed"
            self._busy = False

    # -----------------------------------------------------
    # Background worker - reuses the existing collector/CostAnalyzer/
    # export entry points only, unchanged in shape from the previous
    # synchronous refresh() implementation.
    # -----------------------------------------------------

    def _execute_refresh(self, run_id: str, from_date: str | None, to_date: str | None, tag_key: str | None = None):
        on_progress = self._make_progress_callback(run_id)

        try:
            logger.info(f"Cost Explorer refresh started (run_id={run_id})")

            cost_explorer_collector.main(
                from_date=from_date, to_date=to_date, tag_key=tag_key, on_progress=on_progress
            )

            report = CostAnalyzer().run(on_progress=on_progress)

            export_cost_feed()

            logger.info(f"Cost Explorer refresh completed (run_id={run_id})")

            self._finish(run_id, "COMPLETED", report=report)

        except Exception as exc:

            logger.error(f"Cost Explorer refresh failed (run_id={run_id})")
            logger.exception(exc)

            self._finish(run_id, "FAILED", error=str(exc))

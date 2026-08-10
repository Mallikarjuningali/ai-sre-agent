"""
=========================================================
AI SRE AGENT
Module : Investigation Manager
Purpose:
    Runs an investigation on demand for the FastAPI layer and tracks its
    progress for GET /investigation/status/{run_id}.

    This module does not collect metrics, build context or call the LLM
    itself. It only sequences the collectors' and Analyzer's existing
    entry points - the same ones main.py already uses:

        collector.cloudwatch.main
        collector.alb.main
        collector.autoscaling.main
        collector.cloudtrail.main
        -> Analyzer.run_all() / Analyzer.run()

    Each investigation runs in a background thread so the HTTP request
    that started it can return immediately with a run_id.
=========================================================
"""

import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import collector.alb as alb_collector
import collector.autoscaling as autoscaling_collector
import collector.cloudtrail as cloudtrail_collector
import collector.cloudwatch as cloudwatch_collector
from analyzer.analyzer import Analyzer
from utils.dashboard_export import export as export_dashboard_feed
from utils.logger import get_logger

IST = ZoneInfo("Asia/Kolkata")

logger = get_logger("InvestigationManager")

PHASES = [
    ("COLLECTING_METRICS", "Collecting Metrics"),
    ("BUILDING_CONTEXT", "Building Context"),
    ("RUNNING_AI_ANALYSIS", "Running AI Analysis"),
    ("GENERATING_RCA", "Generating RCA"),
    ("PUBLISHING_DASHBOARD", "Publishing Dashboard"),
]

PHASE_LABELS = dict(PHASES)


class InvestigationBusyError(Exception):
    """Raised when an investigation is requested while one is already running.

    The collector/context-builder pipeline shares one output/ directory tree
    (raw/, context/, reports/) and archive_manager assumes a single current
    run, so two investigations can't safely run at the same time.
    """


class InvestigationManager:

    def __init__(self):
        self._lock = threading.Lock()
        self._busy = False
        self._runs = {}

    # -----------------------------------------------------
    # Public API - called by api/app.py
    # -----------------------------------------------------

    def start_full(self) -> dict:
        run_id = self._claim_run_id()
        thread = threading.Thread(target=self._execute_full, args=(run_id,), daemon=True)
        thread.start()
        return self._start_response(run_id)

    def start_resource(self, resource_type: str, resource_id: str) -> dict:
        run_id = self._claim_run_id(resource_type=resource_type, resource_id=resource_id)
        thread = threading.Thread(
            target=self._execute_resource,
            args=(run_id, resource_id),
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

        percent = snapshot["percent"]
        if snapshot["status"] == "RUNNING" and percent > 0:
            snapshot["remaining_seconds_estimate"] = max(0, int(elapsed * (100 - percent) / percent))
        else:
            snapshot["remaining_seconds_estimate"] = None

        return snapshot

    # -----------------------------------------------------
    # Run bookkeeping
    # -----------------------------------------------------

    def _claim_run_id(self, resource_type=None, resource_id=None) -> str:
        with self._lock:
            if self._busy:
                raise InvestigationBusyError(
                    "Another investigation is already running. Wait for it to finish."
                )
            self._busy = True

            run_id = datetime.now(IST).strftime("%d-%m-%Y_%H-%M-%S")

            self._runs[run_id] = {
                "run_id": run_id,
                "status": "QUEUED",
                "phase": None,
                "phase_label": "Queued",
                "percent": 0,
                "current_resource": resource_id,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "started_at": datetime.now(IST).isoformat(),
                "error": None,
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

        def on_progress(phase_key: str, percent: int, current_resource: str | None = None):
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
                run["percent"] = percent
                if current_resource:
                    run["current_resource"] = current_resource

        return on_progress

    def _finish(self, run_id: str, status: str, error: str | None = None):
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                run["status"] = status
                run["error"] = error
                if status == "COMPLETED":
                    run["percent"] = 100
                    run["phase_label"] = "Completed"
                    for phase in run["phases"]:
                        phase["state"] = "done"
                elif status == "FAILED":
                    run["phase_label"] = "Failed"
            self._busy = False

    # -----------------------------------------------------
    # Background workers - reuse existing collector/Analyzer code only
    # -----------------------------------------------------

    def _execute_full(self, run_id: str):
        on_progress = self._make_progress_callback(run_id)
        try:
            on_progress("COLLECTING_METRICS", 2)
            cloudwatch_collector.main()

            on_progress("COLLECTING_METRICS", 5)
            alb_collector.main()

            on_progress("COLLECTING_METRICS", 8)
            autoscaling_collector.main()

            on_progress("COLLECTING_METRICS", 10)
            cloudtrail_collector.main()

            analyzer = Analyzer()
            analyzer.run_all(run_id=run_id, on_progress=on_progress)

            self._finish(run_id, "COMPLETED")

        except Exception as exc:
            logger.error(f"Full investigation {run_id} failed")
            logger.exception(exc)
            self._finish(run_id, "FAILED", error=str(exc))

    def _execute_resource(self, run_id: str, resource_id: str):
        on_progress = self._make_progress_callback(run_id)
        try:
            on_progress("COLLECTING_METRICS", 2, resource_id)
            cloudwatch_collector.main()

            on_progress("COLLECTING_METRICS", 5, resource_id)
            alb_collector.main()

            on_progress("COLLECTING_METRICS", 8, resource_id)
            autoscaling_collector.main()

            on_progress("COLLECTING_METRICS", 10, resource_id)
            cloudtrail_collector.main()

            analyzer = Analyzer()
            context_filename = f"{resource_id}.json"

            analyzer.run(context_filename, on_progress=on_progress, resource_id=resource_id)

            on_progress("PUBLISHING_DASHBOARD", 95, resource_id)
            try:
                export_dashboard_feed()
            except Exception as exc:
                logger.error(f"Dashboard feed export failed after resource investigation {run_id}.")
                logger.exception(exc)

            self._finish(run_id, "COMPLETED")

        except Exception as exc:
            logger.error(f"Resource investigation {run_id} ({resource_id}) failed")
            logger.exception(exc)
            self._finish(run_id, "FAILED", error=str(exc))

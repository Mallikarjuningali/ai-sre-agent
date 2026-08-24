"""
=========================================================
AI SRE AGENT
Module : Cost Explorer Manager
Purpose:
    Runs a Cost Explorer refresh on demand for the FastAPI layer.
    Completely separate from api/investigation_manager.py - a Cost
    Explorer refresh is a single, fast (low single-digit seconds)
    boto3 call sequence, not a multi-minute multi-collector
    investigation, so this runs synchronously with no background
    thread / run_id / status-polling machinery. It also keeps its own
    lock, independent of InvestigationManager's, so a cost refresh can
    never collide with (or be blocked by) an infra investigation.
=========================================================
"""

import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import collector.cost_explorer as cost_explorer_collector
from analyzer.cost_analyzer import CostAnalyzer
from utils.cost_dashboard_export import export as export_cost_feed
from utils.logger import get_logger

IST = ZoneInfo("Asia/Kolkata")

logger = get_logger("CostExplorerManager")


class CostExplorerBusyError(Exception):
    """Raised when a Cost Explorer refresh is requested while one is already running."""


class CostExplorerManager:

    def __init__(self):
        self._lock = threading.Lock()
        self._busy = False

    def refresh(self) -> dict:

        with self._lock:
            if self._busy:
                raise CostExplorerBusyError(
                    "A Cost Explorer refresh is already running. Wait for it to finish."
                )
            self._busy = True

        started_at = datetime.now(IST).isoformat()

        try:
            logger.info("Cost Explorer refresh started")

            cost_explorer_collector.main()

            report = CostAnalyzer().run()

            export_cost_feed()

            logger.info("Cost Explorer refresh completed")

            return {
                "status": "COMPLETED",
                "started_at": started_at,
                "finished_at": datetime.now(IST).isoformat(),
                "report": report,
            }

        except Exception as exc:

            logger.error("Cost Explorer refresh failed")
            logger.exception(exc)

            raise

        finally:

            with self._lock:
                self._busy = False

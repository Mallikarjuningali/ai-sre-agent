"""
ReportService — backs the Investigation page (AI root cause reports).

Contract (reports.json) is a JSON array of Report objects, exactly per the
AegisOps contract:

    {
      "instance_id": "i-123456",
      "severity": "HIGH",
      "summary": "ALB timeout",
      "root_cause": "Nginx service stopped",
      "evidence": ["...", "..."],
      "recommendations": ["...", "..."]
    }

To let the Investigation page link a report back to the execution that
produced it and render an optional resource topology, the backend may also
publish "run_id", "report_id", "resource_type", "detected_at" and
"resource_tree" on each record. All are read defensively; the UI renders
an empty state rather than inventing data when they're absent.
"""
from __future__ import annotations

from .cache import cached
from .data_source import DataSource

REPORTS_KEY = "reports.json"


class ReportService:
    def __init__(self, data_source: DataSource, refresh_interval_seconds: int = 30):
        self._ds = data_source
        self._ttl = refresh_interval_seconds

    def list_reports(self) -> list[dict]:
        def _load():
            if not self._ds.exists(REPORTS_KEY):
                return []
            raw = self._ds.read_json(REPORTS_KEY)
            return raw if isinstance(raw, list) else []

        return cached(f"reports::{REPORTS_KEY}", self._ttl, _load)

    def get_reports_for_run(self, run_id: str) -> list[dict]:
        return [r for r in self.list_reports() if r.get("run_id") == run_id]

    def get_report(self, report_id: str) -> dict | None:
        for report in self.list_reports():
            if report.get("report_id") == report_id or report.get("instance_id") == report_id:
                return report
        return None

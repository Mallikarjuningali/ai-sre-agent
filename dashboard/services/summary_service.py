"""
SummaryService — backs the Dashboard Overview page.

Reads a single pre-aggregated JSON object (`summary.json`) that the backend
publishes after each execution. The dashboard does not compute KPIs itself;
it only renders whatever the backend reports. See services/CONTRACT.md for
the full object shape.
"""
from __future__ import annotations

from typing import Any

from .cache import cached
from .data_source import DataSource

SUMMARY_KEY = "summary.json"

_EMPTY_SUMMARY: dict[str, Any] = {
    "generated_at": None,
    "kpis": {},
    "infrastructure_health": {},
    "latest_execution": {},
    "incidents_by_severity_trend": [],
    "top_affected_resources": [],
    "investigation_trend": [],
    "recent_investigations": [],
}


class SummaryService:
    def __init__(self, data_source: DataSource, refresh_interval_seconds: int = 30):
        self._ds = data_source
        self._ttl = refresh_interval_seconds

    def get_overview(self) -> dict:
        def _load():
            if not self._ds.exists(SUMMARY_KEY):
                return dict(_EMPTY_SUMMARY)
            raw = self._ds.read_json(SUMMARY_KEY)
            merged = dict(_EMPTY_SUMMARY)
            merged.update(raw or {})
            return merged

        return cached(f"summary::{SUMMARY_KEY}", self._ttl, _load)

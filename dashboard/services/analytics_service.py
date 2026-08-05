"""
AnalyticsService — backs the Analytics page.

Reads a single pre-aggregated JSON object (`analytics.json`) covering
incident trend, severity trend, resource distribution, collector
statistics and execution time trend. See services/CONTRACT.md.
"""
from __future__ import annotations

from typing import Any

from .cache import cached
from .data_source import DataSource

ANALYTICS_KEY = "analytics.json"

_EMPTY_ANALYTICS: dict[str, Any] = {
    "incident_trend": [],
    "severity_trend": [],
    "resource_distribution": [],
    "collector_statistics": [],
    "execution_time_trend": [],
}


class AnalyticsService:
    def __init__(self, data_source: DataSource, refresh_interval_seconds: int = 30):
        self._ds = data_source
        self._ttl = refresh_interval_seconds

    def get_analytics(self) -> dict:
        def _load():
            if not self._ds.exists(ANALYTICS_KEY):
                return dict(_EMPTY_ANALYTICS)
            raw = self._ds.read_json(ANALYTICS_KEY)
            merged = dict(_EMPTY_ANALYTICS)
            merged.update(raw or {})
            return merged

        return cached(f"analytics::{ANALYTICS_KEY}", self._ttl, _load)

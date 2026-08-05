"""
CollectorService — health of each backend data collector
(CloudWatch, CloudTrail, ALB, Auto Scaling, ...).

Contract (collectors.json), exactly as published by the backend:
    {
      "CloudWatch": "Healthy",
      "CloudTrail": "Healthy",
      "ALB": "Healthy",
      "AutoScaling": "Healthy"
    }

Values are free-text status strings from the backend (e.g. "Healthy",
"Degraded", "Down"). The UI treats anything not case-insensitively equal
to "healthy" as needing attention.
"""
from __future__ import annotations

from .cache import cached
from .data_source import DataSource

COLLECTORS_KEY = "collectors.json"


class CollectorService:
    def __init__(self, data_source: DataSource, refresh_interval_seconds: int = 30):
        self._ds = data_source
        self._ttl = refresh_interval_seconds

    def get_collector_health(self) -> dict[str, str]:
        def _load():
            if not self._ds.exists(COLLECTORS_KEY):
                return {}
            raw = self._ds.read_json(COLLECTORS_KEY)
            return raw if isinstance(raw, dict) else {}

        return cached(f"collectors::{COLLECTORS_KEY}", self._ttl, _load)

    @staticmethod
    def is_healthy(status: str) -> bool:
        return str(status).strip().lower() == "healthy"

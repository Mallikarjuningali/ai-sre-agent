"""
HistoryService — backs the Execution History page.

Contract (executions.json) is a JSON array of Execution objects. The core
fields are exactly what the backend publishes per the AegisOps contract:

    {
      "run_id": "20260804_102030",
      "execution_time": 23,
      "successful": 5,
      "failed": 0,
      "resources": 5
    }

The backend may additionally publish "type", "status" and "start_time" on
each record (shown in History/Overview tables); these are read
defensively and fall back to a derived/neutral value when absent so the
UI never fabricates business data, it only tolerates a partial record.
"""
from __future__ import annotations

from .cache import cached
from .data_source import DataSource

EXECUTIONS_KEY = "executions.json"


class HistoryService:
    def __init__(self, data_source: DataSource, refresh_interval_seconds: int = 30):
        self._ds = data_source
        self._ttl = refresh_interval_seconds

    def list_executions(self) -> list[dict]:
        def _load():
            if not self._ds.exists(EXECUTIONS_KEY):
                return []
            raw = self._ds.read_json(EXECUTIONS_KEY)
            return raw if isinstance(raw, list) else []

        records = cached(f"history::{EXECUTIONS_KEY}", self._ttl, _load)
        return sorted(records, key=lambda r: r.get("start_time") or r.get("run_id") or "", reverse=True)

    @staticmethod
    def search(executions: list[dict], query: str) -> list[dict]:
        if not query:
            return executions
        needle = query.strip().lower()
        return [
            record
            for record in executions
            if needle in str(record.get("run_id", "")).lower()
            or needle in str(record.get("type", "")).lower()
        ]

    @staticmethod
    def filter_by_status(executions: list[dict], status: str) -> list[dict]:
        if not status or status == "All":
            return executions
        return [r for r in executions if str(r.get("status", "")).lower() == status.lower()]

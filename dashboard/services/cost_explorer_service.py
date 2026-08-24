"""
CostExplorerService / CostRefreshService — back the Cost Explorer page.
Completely separate from every existing dashboard service.

Read side (CostExplorerService) follows the exact same DataSource +
cached() pattern as AnalyticsService/SummaryService/etc. Write side
(CostRefreshService) follows the exact same *Backend ABC +
Local(Unavailable)/Rest + factory pattern as ExecutionService -
triggering a real AWS Cost Explorer refresh always goes through the
FastAPI backend, never boto3 directly from the dashboard.

Contract (six separate JSON keys, published by
utils/cost_dashboard_export.py -> output/cost/dashboard_feed/*.json, and
served in REST mode by api/app.py's matching /cost-explorer/* routes -
the keys below are deliberately the same path shape as those routes so
RestApiDataSource.read_json(key) hits them with no translation):

    cost-explorer/summary   -> {total_cost, previous_cost, change_percent, currency, period, generated_at}
    cost-explorer/history   -> {daily_history: [[date, value], ...], currency}
    cost-explorer/services  -> {service_breakdown: [{service, cost, currency}], currency}
    cost-explorer/regions   -> {region_breakdown: [{region, cost, currency}], currency}
    cost-explorer/anomalies -> {status, reason, anomalies: [...]}
    cost-explorer/report    -> the Gemini cost report (severity/summary/root_cause/evidence/recommendations/...)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .cache import cached
from .config import AppConfig
from .data_source import DataSource

SUMMARY_KEY = "cost-explorer/summary"
HISTORY_KEY = "cost-explorer/history"
SERVICES_KEY = "cost-explorer/services"
REGIONS_KEY = "cost-explorer/regions"
ANOMALIES_KEY = "cost-explorer/anomalies"
REPORT_KEY = "cost-explorer/report"

_EMPTY_SUMMARY: dict[str, Any] = {
    "total_cost": None, "previous_cost": None, "change_percent": None,
    "currency": None, "period": {}, "generated_at": None,
}
_EMPTY_HISTORY: dict[str, Any] = {"daily_history": [], "currency": None}
_EMPTY_SERVICES: dict[str, Any] = {"service_breakdown": [], "currency": None}
_EMPTY_REGIONS: dict[str, Any] = {"region_breakdown": [], "currency": None}
_EMPTY_ANOMALIES: dict[str, Any] = {
    "status": "unavailable", "reason": "No cost data published yet", "anomalies": [],
}
_EMPTY_REPORT: dict[str, Any] = {}


class CostExplorerService:
    def __init__(self, data_source: DataSource, refresh_interval_seconds: int = 30):
        self._ds = data_source
        self._ttl = refresh_interval_seconds

    def _get(self, key: str, empty: dict) -> dict:
        def _load():
            if not self._ds.exists(key):
                return dict(empty)
            raw = self._ds.read_json(key)
            merged = dict(empty)
            merged.update(raw or {})
            return merged

        return cached(f"cost_explorer::{key}", self._ttl, _load)

    def get_summary(self) -> dict:
        return self._get(SUMMARY_KEY, _EMPTY_SUMMARY)

    def get_history(self) -> dict:
        return self._get(HISTORY_KEY, _EMPTY_HISTORY)

    def get_services(self) -> dict:
        return self._get(SERVICES_KEY, _EMPTY_SERVICES)

    def get_regions(self) -> dict:
        return self._get(REGIONS_KEY, _EMPTY_REGIONS)

    def get_anomalies(self) -> dict:
        return self._get(ANOMALIES_KEY, _EMPTY_ANOMALIES)

    def get_report(self) -> dict:
        return self._get(REPORT_KEY, _EMPTY_REPORT)


# -----------------------------------------------------------------------
# Refresh (write/action) side
# -----------------------------------------------------------------------

class CostExplorerActionError(Exception):
    """Raised when a Cost Explorer refresh cannot reach a backend."""


class CostExplorerBackend(ABC):
    @abstractmethod
    def refresh(self) -> dict:
        """Trigger a fresh AWS Cost Explorer query. Returns the backend's result dict."""


class UnavailableCostExplorerBackend(CostExplorerBackend):
    """Local/S3 data sources have no live endpoint to trigger a refresh through."""

    _MESSAGE = "Refreshing Cost Explorer data requires a REST backend. Configure one on the Settings page."

    def refresh(self) -> dict:
        raise CostExplorerActionError(self._MESSAGE)


class RestCostExplorerBackend(CostExplorerBackend):
    """Calls the real backend endpoint."""

    def __init__(self, base_url: str, api_key: str | None = None):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def _headers(self) -> dict:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def refresh(self) -> dict:
        import requests

        url = f"{self._base_url}/cost-explorer/refresh"
        try:
            # A cost refresh runs several boto3 calls plus one Gemini call
            # synchronously (see api/cost_explorer_manager.py) - a longer
            # timeout than the simple delete-executions call needs.
            response = requests.post(url, json={}, headers=self._headers(), timeout=60)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 - surface as CostExplorerActionError to callers
            raise CostExplorerActionError(f"Failed to POST {url}: {exc}") from exc


def get_cost_explorer_backend(config: AppConfig) -> CostExplorerBackend:
    """Factory: mirrors get_execution_backend()/get_investigation_backend() - the single switch point."""
    if config.data_source_type == "rest" and config.rest_base_url:
        return RestCostExplorerBackend(base_url=config.rest_base_url, api_key=config.rest_api_key)
    return UnavailableCostExplorerBackend()


class CostRefreshService:
    def __init__(self, backend: CostExplorerBackend):
        self._backend = backend

    @property
    def is_live(self) -> bool:
        return isinstance(self._backend, RestCostExplorerBackend)

    def refresh(self) -> dict:
        return self._backend.refresh()

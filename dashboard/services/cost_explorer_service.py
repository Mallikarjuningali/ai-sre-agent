"""
CostExplorerService / CostRefreshService — back the Cost Explorer page.
Completely separate from every existing dashboard service.

Read side (CostExplorerService) follows the exact same DataSource +
cached() pattern as AnalyticsService/SummaryService/etc. Write side
(CostRefreshService) follows the exact same *Backend ABC +
Local(Unavailable)/Rest + factory pattern as ExecutionService -
triggering a real AWS Cost Explorer refresh always goes through the
FastAPI backend, never boto3 directly from the dashboard.

Contract (published by utils/cost_dashboard_export.py ->
output/cost/dashboard_feed/*.json, and served in REST mode by
api/app.py's matching /cost-explorer/* routes - the keys below are
deliberately the same path shape as those routes so
RestApiDataSource.read_json(key) hits them with no translation). See
context/cost_context_builder.py's docstring for the full data model
these are all built from (current_period/previous_period/change/
service_comparison/region_comparison/anomalies/comparison, each period
being {from, to, currency, gross_cost, credits, net_cost, daily_history,
service_breakdown, region_breakdown} with service_breakdown/
region_breakdown entries shaped {service|region, gross_cost, credits,
net_cost, currency}):

    cost-explorer/summary   -> {currency, period, generated_at,
                                 current_period, previous_period, change}
    cost-explorer/history   -> {daily_history: [[date, value], ...], currency}
    cost-explorer/credits   -> {total, currency, history, by_service, by_region,
                                 resource_level_attribution_available: false,
                                 resource_level_attribution_note}
    cost-explorer/services  -> {service_breakdown, service_comparison, currency}
    cost-explorer/regions   -> {region_breakdown, region_comparison, currency, region_credit_note}
    cost-explorer/anomalies -> {status, reason, anomalies: [...]}
    cost-explorer/comparison -> {selected_period, comparison_period, difference,
                                 percentage_change, service_comparison, region_comparison,
                                 credit_comparison, anomaly_comparison} or null (no comparison run yet)
    cost-explorer/report    -> the Gemini cost report (severity/summary/root_cause/evidence/recommendations/...,
                               plus an additive comparison_analysis when a comparison was part of this run)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .cache import cached
from .config import AppConfig
from .data_source import DataSource

SUMMARY_KEY = "cost-explorer/summary"
HISTORY_KEY = "cost-explorer/history"
CREDITS_KEY = "cost-explorer/credits"
SERVICES_KEY = "cost-explorer/services"
REGIONS_KEY = "cost-explorer/regions"
ANOMALIES_KEY = "cost-explorer/anomalies"
TAGS_KEY = "cost-explorer/tags"
FORECAST_KEY = "cost-explorer/forecast"
COMPARISON_KEY = "cost-explorer/comparison"
REPORT_KEY = "cost-explorer/report"

_EMPTY_PERIOD: dict[str, Any] = {
    "from": None, "to": None, "currency": None, "gross_cost": None,
    "credits": {"total": None, "currency": None, "history": []},
    "net_cost": None, "daily_history": [], "service_breakdown": [], "region_breakdown": [],
}
_EMPTY_CHANGE: dict[str, Any] = {
    "gross_cost_change": None, "gross_cost_change_percent": None,
    "credits_change": None, "net_cost_change": None, "net_cost_change_percent": None,
}
_EMPTY_SUMMARY: dict[str, Any] = {
    "currency": None, "period": {}, "generated_at": None,
    "current_period": dict(_EMPTY_PERIOD), "previous_period": dict(_EMPTY_PERIOD),
    "change": dict(_EMPTY_CHANGE),
}
_EMPTY_HISTORY: dict[str, Any] = {"daily_history": [], "currency": None}
_EMPTY_CREDITS: dict[str, Any] = {
    "total": None, "currency": None, "history": [], "by_service": [], "by_region": [],
    "resource_level_attribution_available": False,
    "resource_level_attribution_note": (
        "AWS Cost Explorer reports these credits at the service/region level. "
        "Individual resource attribution is not available from the current billing data."
    ),
}
_EMPTY_SERVICES: dict[str, Any] = {"service_breakdown": [], "service_comparison": [], "currency": None}
_EMPTY_REGIONS: dict[str, Any] = {"region_breakdown": [], "region_comparison": [], "currency": None, "region_credit_note": None}
_EMPTY_ANOMALIES: dict[str, Any] = {
    "status": "unavailable", "reason": "No cost data published yet", "anomalies": [],
    "requested_start": None, "requested_end": None,
    "analyzed_start": None, "analyzed_end": None,
    "supported_from": None, "supported": None, "partial": False,
}
_EMPTY_COMPARISON: dict[str, Any] = {}
_EMPTY_REPORT: dict[str, Any] = {}
_EMPTY_TAGS: dict[str, Any] = {}
_EMPTY_FORECAST: dict[str, Any] = {}


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

    def get_credits(self) -> dict:
        return self._get(CREDITS_KEY, _EMPTY_CREDITS)

    def get_services(self) -> dict:
        return self._get(SERVICES_KEY, _EMPTY_SERVICES)

    def get_regions(self) -> dict:
        return self._get(REGIONS_KEY, _EMPTY_REGIONS)

    def get_anomalies(self) -> dict:
        return self._get(ANOMALIES_KEY, _EMPTY_ANOMALIES)

    def get_comparison(self) -> dict:
        """Empty dict when no Month/Period Comparison has been requested
        yet - a valid "not run" state, distinct from a comparison that
        genuinely found $0 difference."""
        return self._get(COMPARISON_KEY, _EMPTY_COMPARISON)

    def get_tags(self) -> dict:
        """Empty dict when no tag key has been requested yet - a valid
        "not run" state, distinct from status="empty" (a real AWS answer
        of zero cost for an activated tag)."""
        return self._get(TAGS_KEY, _EMPTY_TAGS)

    def get_forecast(self) -> dict:
        """Empty dict when no refresh has ever run with forecasting
        enabled - a valid "not run" state, distinct from
        status="unavailable" (AWS was asked and could not forecast)."""
        return self._get(FORECAST_KEY, _EMPTY_FORECAST)

    def get_report(self) -> dict:
        return self._get(REPORT_KEY, _EMPTY_REPORT)


# -----------------------------------------------------------------------
# Refresh (write/action) side
# -----------------------------------------------------------------------

class CostExplorerActionError(Exception):
    """Raised when a Cost Explorer refresh cannot reach a backend."""


class CostExplorerBackend(ABC):
    @abstractmethod
    def start_refresh(
        self, from_date: str | None = None, to_date: str | None = None, tag_key: str | None = None
    ) -> dict:
        """Start a fresh AWS Cost Explorer query in the background.
        from_date/to_date (optional, both required together) request a
        Month/Period Comparison for that user-selected range in the same
        refresh. tag_key (optional, any real AWS Cost Allocation Tag key)
        requests a tag-based cost allocation breakdown in the same
        refresh. Returns {run_id, status, started_at} immediately - the
        actual refresh runs asynchronously; poll get_status(run_id)."""

    @abstractmethod
    def get_status(self, run_id: str) -> dict | None:
        """Poll refresh progress. Returns None if the backend has nothing for this run yet."""


class UnavailableCostExplorerBackend(CostExplorerBackend):
    """Local/S3 data sources have no live endpoint to trigger a refresh through."""

    _MESSAGE = "Refreshing Cost Explorer data requires a REST backend. Configure one on the Settings page."

    def start_refresh(
        self, from_date: str | None = None, to_date: str | None = None, tag_key: str | None = None
    ) -> dict:
        raise CostExplorerActionError(self._MESSAGE)

    def get_status(self, run_id: str) -> dict | None:
        return None


class RestCostExplorerBackend(CostExplorerBackend):
    """Calls the real backend endpoints."""

    def __init__(self, base_url: str, api_key: str | None = None):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def _headers(self) -> dict:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def start_refresh(
        self, from_date: str | None = None, to_date: str | None = None, tag_key: str | None = None
    ) -> dict:
        import requests

        url = f"{self._base_url}/cost-explorer/refresh"
        # Empty body ({}) when no comparison dates/tag key are selected -
        # matches exactly what every caller already sent before these
        # features existed, so the existing endpoint contract is
        # unaffected.
        payload = {}
        if from_date:
            payload["from_date"] = from_date
        if to_date:
            payload["to_date"] = to_date
        if tag_key:
            payload["tag_key"] = tag_key
        try:
            # Starting a refresh only claims a run_id and spawns a
            # background thread on the backend - it returns almost
            # immediately now, same short timeout as other start-a-run
            # calls (see services/investigation_service.py).
            response = requests.post(url, json=payload, headers=self._headers(), timeout=15)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 - surface as CostExplorerActionError to callers
            raise CostExplorerActionError(f"Failed to POST {url}: {exc}") from exc

    def get_status(self, run_id: str) -> dict | None:
        import requests

        url = f"{self._base_url}/cost-explorer/status/{run_id}"
        try:
            response = requests.get(url, headers=self._headers(), timeout=10)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            raise CostExplorerActionError(f"Failed to GET {url}: {exc}") from exc


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

    def start_refresh(
        self, from_date: str | None = None, to_date: str | None = None, tag_key: str | None = None
    ) -> dict:
        return self._backend.start_refresh(from_date=from_date, to_date=to_date, tag_key=tag_key)

    def get_run_status(self, run_id: str) -> dict | None:
        return self._backend.get_status(run_id)

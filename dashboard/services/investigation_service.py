"""
InvestigationService — starts a new AI investigation run and polls its
progress.

Unlike every other service in this package, this one *writes* (it asks the
backend to start work) rather than only reading published JSON, so it does
not go through the read-only DataSource abstraction — it goes through an
InvestigationBackend instead, mirroring the same local/REST plug-point
pattern as services/data_source.py, so wiring a real backend never touches
UI code.

Contract:

    POST /investigation/full
      request:  {}
      response: { "run_id": "...", "status": "QUEUED", "started_at": "..." }

    POST /investigation/resource
      request:  { "resource_type": "EC2 Instance", "resource_id": "i-..." }
      response: { "run_id": "...", "status": "QUEUED", "started_at": "..." }

    GET /investigation/status/{run_id}
      response: {
        "run_id": "...", "status": "RUNNING",
        "phase": "RUNNING_AI_ANALYSIS", "phase_label": "Running AI Analysis",
        "percent": 62, "current_resource": "i-...",
        "elapsed_seconds": 145, "remaining_seconds_estimate": 90,
        "phases": [ { "key": "...", "label": "...", "state": "done|active|pending" } ]
      }

See services/CONTRACT.md for the full write-up. There is no "local" backend
that can start a real investigation — there is nothing to POST to when the
dashboard is just reading fixtures off disk — so local/S3 data source modes
resolve to UnavailableInvestigationBackend, which fails with a clear message
rather than faking a run.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .config import AppConfig


class InvestigationActionError(Exception):
    """Raised when starting or polling an investigation cannot reach a backend."""


class InvestigationBackend(ABC):
    @abstractmethod
    def start_full(self) -> dict:
        """Start a full-infrastructure investigation. Returns {run_id, status, started_at}."""

    @abstractmethod
    def start_resource(self, resource_type: str, resource_id: str) -> dict:
        """Start a single-resource investigation. Returns {run_id, status, started_at}."""

    @abstractmethod
    def get_status(self, run_id: str) -> dict | None:
        """Poll run progress. Returns None if the backend has nothing for this run yet."""


class UnavailableInvestigationBackend(InvestigationBackend):
    """Local/S3 data sources have no live endpoint to POST a new run to."""

    _MESSAGE = "Live investigations require a REST backend. Configure one on the Settings page."

    def start_full(self) -> dict:
        raise InvestigationActionError(self._MESSAGE)

    def start_resource(self, resource_type: str, resource_id: str) -> dict:
        raise InvestigationActionError(self._MESSAGE)

    def get_status(self, run_id: str) -> dict | None:
        return None


class RestInvestigationBackend(InvestigationBackend):
    """Calls the real backend endpoints. Ready to use the moment a backend exists."""

    def __init__(self, base_url: str, api_key: str | None = None):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def _headers(self) -> dict:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _post(self, path: str, payload: dict) -> dict:
        import requests

        url = f"{self._base_url}{path}"
        try:
            response = requests.post(url, json=payload, headers=self._headers(), timeout=15)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 - surface as InvestigationActionError to callers
            raise InvestigationActionError(f"Failed to POST {url}: {exc}") from exc

    def start_full(self) -> dict:
        return self._post("/investigation/full", {})

    def start_resource(self, resource_type: str, resource_id: str) -> dict:
        return self._post("/investigation/resource", {"resource_type": resource_type, "resource_id": resource_id})

    def get_status(self, run_id: str) -> dict | None:
        import requests

        url = f"{self._base_url}/investigation/status/{run_id}"
        try:
            response = requests.get(url, headers=self._headers(), timeout=10)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            raise InvestigationActionError(f"Failed to GET {url}: {exc}") from exc

    @property
    def base_url(self) -> str:
        return self._base_url


def get_investigation_backend(config: AppConfig) -> InvestigationBackend:
    """Factory: mirrors get_data_source() — the single switch point."""
    if config.data_source_type == "rest" and config.rest_base_url:
        return RestInvestigationBackend(base_url=config.rest_base_url, api_key=config.rest_api_key)
    return UnavailableInvestigationBackend()


class InvestigationService:
    def __init__(self, backend: InvestigationBackend):
        self._backend = backend

    @property
    def is_live(self) -> bool:
        return isinstance(self._backend, RestInvestigationBackend)

    def start_full_investigation(self) -> dict:
        return self._backend.start_full()

    def start_resource_investigation(self, resource_type: str, resource_id: str) -> dict:
        return self._backend.start_resource(resource_type, resource_id)

    def get_run_status(self, run_id: str) -> dict | None:
        return self._backend.get_status(run_id)

"""
LogInvestigationService (dashboard client) — triggers the optional Log
Investigation stage for a completed investigation report and reads back
its persisted result. Completely separate from Cost Explorer and from
FollowUpService (own routes, own output directory, own manager).

Like FollowUpService/InvestigationService/ResourceDiscoveryService, this
is a *write-ish/live* action (it triggers real AWS + Gemini calls
server-side), so it goes through a Backend ABC (local/S3 data sources
have nothing to POST to), never a direct boto3/Gemini call from the
dashboard itself. See services/CONTRACT.md.

Contract:

    POST /investigation/{investigation_id}/logs
      request:  (no body)
      response: { "investigation_id", "resource_id", "resource_type",
                  "evidence_package": {...}, "analysis": {...} }

    GET /investigation/{investigation_id}/logs
      response: { "investigation_id", "investigated": bool, ... } -
                  investigated is false (with no other keys) if
                  "Investigate Logs" has never been clicked for this
                  investigation - not an error.

investigation_id is built by the caller as f"{run_id}__{resource_id}" -
the exact same shape/derivation FollowUpService already uses, both values
the Report Viewer already has in scope for a rendered report.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .config import AppConfig


class LogInvestigationActionError(Exception):
    """Raised when a log investigation cannot reach a backend, or the
    backend rejects it (investigation not found/superseded, unsupported
    resource type, AWS/Gemini unavailable) - the HTTP status/detail is
    folded into the message so the UI can show it directly."""


class LogInvestigationBackend(ABC):
    @abstractmethod
    def investigate(self, investigation_id: str) -> dict:
        """Trigger the log investigation. Returns the structured result dict."""

    @abstractmethod
    def get_results(self, investigation_id: str) -> dict:
        """Returns the existing result for this investigation, or
        {"investigated": False} if none exists yet."""


class UnavailableLogInvestigationBackend(LogInvestigationBackend):
    """Local/S3 data sources have no live endpoint to trigger a log
    investigation through."""

    _MESSAGE = "Log investigation requires a REST backend. Configure one on the Settings page."

    def investigate(self, investigation_id: str) -> dict:
        raise LogInvestigationActionError(self._MESSAGE)

    def get_results(self, investigation_id: str) -> dict:
        # Reading a prior result is harmless to no-op when there's no
        # live backend - "not yet investigated" is the honest answer,
        # not an error, since local/S3 mode never had one to begin with.
        return {"investigation_id": investigation_id, "investigated": False}


class RestLogInvestigationBackend(LogInvestigationBackend):
    """Calls the real backend endpoints."""

    def __init__(self, base_url: str, api_key: str | None = None):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def _headers(self) -> dict:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def investigate(self, investigation_id: str) -> dict:
        import requests

        url = f"{self._base_url}/investigation/{investigation_id}/logs"
        try:
            # A log investigation fetches a bounded log window (AWS calls)
            # AND makes one Gemini call server-side - a generous timeout to
            # tolerate both without a false failure.
            response = requests.post(url, headers=self._headers(), timeout=90)
            if response.status_code >= 400:
                detail = response.json().get("detail") if response.headers.get("content-type", "").startswith("application/json") else response.text
                raise LogInvestigationActionError(str(detail) if detail else f"Log investigation request failed ({response.status_code}).")
            return response.json()
        except LogInvestigationActionError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface as LogInvestigationActionError to callers
            raise LogInvestigationActionError(f"Failed to POST {url}: {exc}") from exc

    def get_results(self, investigation_id: str) -> dict:
        import requests

        url = f"{self._base_url}/investigation/{investigation_id}/logs"
        try:
            response = requests.get(url, headers=self._headers(), timeout=15)
            if response.status_code == 404:
                return {"investigation_id": investigation_id, "investigated": False}
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            raise LogInvestigationActionError(f"Failed to GET {url}: {exc}") from exc


def get_log_investigation_backend(config: AppConfig) -> LogInvestigationBackend:
    """Factory: mirrors get_follow_up_backend()/get_investigation_backend()/
    get_cost_explorer_backend() - the single switch point."""
    if config.data_source_type == "rest" and config.rest_base_url:
        return RestLogInvestigationBackend(base_url=config.rest_base_url, api_key=config.rest_api_key)
    return UnavailableLogInvestigationBackend()


class LogInvestigationService:
    def __init__(self, backend: LogInvestigationBackend):
        self._backend = backend

    @property
    def is_live(self) -> bool:
        return isinstance(self._backend, RestLogInvestigationBackend)

    def investigate(self, investigation_id: str) -> dict:
        return self._backend.investigate(investigation_id)

    def get_results(self, investigation_id: str) -> dict:
        return self._backend.get_results(investigation_id)

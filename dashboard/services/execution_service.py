"""
ExecutionService — deletes one or more completed executions from the
Execution History page's "Delete Selected" action.

Like InvestigationService, this is a *write* path, so it goes through an
ExecutionBackend rather than the read-only DataSource - see
services/CONTRACT.md, "Execution management" section, for the request/
response contract, and dashboard/services/investigation_service.py for the
identical local/REST plug-point pattern this mirrors.

Contract:

    DELETE /executions
      request:  { "run_ids": ["06-08-2026_15-23-12", ...] }
      response: { "deleted": [...], "not_found": [...] }

There is no "local" backend that can delete anything real - local/S3 data
source modes resolve to UnavailableExecutionBackend, which fails with a
clear message rather than pretending to delete fixture data.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .config import AppConfig


class ExecutionActionError(Exception):
    """Raised when deleting executions cannot reach a backend."""


class ExecutionBackend(ABC):
    @abstractmethod
    def delete_executions(self, run_ids: list[str]) -> dict:
        """Delete the given run_ids. Returns {"deleted": [...], "not_found": [...]}."""


class UnavailableExecutionBackend(ExecutionBackend):
    """Local/S3 data sources have no live endpoint to DELETE a run through."""

    _MESSAGE = "Deleting executions requires a REST backend. Configure one on the Settings page."

    def delete_executions(self, run_ids: list[str]) -> dict:
        raise ExecutionActionError(self._MESSAGE)


class RestExecutionBackend(ExecutionBackend):
    """Calls the real backend endpoint."""

    def __init__(self, base_url: str, api_key: str | None = None):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def _headers(self) -> dict:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def delete_executions(self, run_ids: list[str]) -> dict:
        import requests

        url = f"{self._base_url}/executions"
        try:
            response = requests.delete(url, json={"run_ids": run_ids}, headers=self._headers(), timeout=15)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 - surface as ExecutionActionError to callers
            raise ExecutionActionError(f"Failed to DELETE {url}: {exc}") from exc


def get_execution_backend(config: AppConfig) -> ExecutionBackend:
    """Factory: mirrors get_investigation_backend() - the single switch point."""
    if config.data_source_type == "rest" and config.rest_base_url:
        return RestExecutionBackend(base_url=config.rest_base_url, api_key=config.rest_api_key)
    return UnavailableExecutionBackend()


class ExecutionService:
    def __init__(self, backend: ExecutionBackend):
        self._backend = backend

    @property
    def is_live(self) -> bool:
        return isinstance(self._backend, RestExecutionBackend)

    def delete_executions(self, run_ids: list[str]) -> dict:
        return self._backend.delete_executions(run_ids)

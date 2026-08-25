"""
ResourceDiscoveryService — triggers live AWS resource discovery for the
Single Resource Investigation launcher's "Refresh Resources" button.

Unlike ResourceService (which reads the cached resources.json dashboard
feed - a byproduct of the last investigation that happened to publish one),
this calls GET /investigation/resources directly, so the picker can be
populated before any investigation - Full or Single - has ever run. Mirrors
the same local/REST backend switch pattern as investigation_service.py and
cost_explorer_service.py's refresh side: a *write-ish/live* action goes
through a Backend, not the read-only DataSource abstraction, so wiring a
real backend never touches UI code. See services/CONTRACT.md.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .config import AppConfig


class ResourceDiscoveryActionError(Exception):
    """Raised when live resource discovery cannot reach a backend."""


class ResourceDiscoveryBackend(ABC):
    @abstractmethod
    def discover(self) -> dict:
        """Query AWS for currently available EC2/ALB/ASG resources. Returns
        the same {resource_type: [{"id":..., "label":...}]} shape as
        resources.json."""


class UnavailableResourceDiscoveryBackend(ResourceDiscoveryBackend):
    """Local/S3 data sources have no live endpoint to query AWS through."""

    _MESSAGE = "Refreshing resources requires a REST backend. Configure one on the Settings page."

    def discover(self) -> dict:
        raise ResourceDiscoveryActionError(self._MESSAGE)


class RestResourceDiscoveryBackend(ResourceDiscoveryBackend):
    """Calls the real backend endpoint."""

    def __init__(self, base_url: str, api_key: str | None = None):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def _headers(self) -> dict:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def discover(self) -> dict:
        import requests

        url = f"{self._base_url}/investigation/resources"
        try:
            # Three lightweight, non-metric AWS describe calls (EC2/ELBv2/
            # AutoScaling) - fast, but generous enough of a timeout to
            # tolerate a slow region/account without a false failure.
            response = requests.get(url, headers=self._headers(), timeout=30)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001 - surface as ResourceDiscoveryActionError to callers
            raise ResourceDiscoveryActionError(f"Failed to GET {url}: {exc}") from exc


def get_resource_discovery_backend(config: AppConfig) -> ResourceDiscoveryBackend:
    """Factory: mirrors get_investigation_backend()/get_cost_explorer_backend() - the single switch point."""
    if config.data_source_type == "rest" and config.rest_base_url:
        return RestResourceDiscoveryBackend(base_url=config.rest_base_url, api_key=config.rest_api_key)
    return UnavailableResourceDiscoveryBackend()


class ResourceDiscoveryService:
    def __init__(self, backend: ResourceDiscoveryBackend):
        self._backend = backend

    @property
    def is_live(self) -> bool:
        return isinstance(self._backend, RestResourceDiscoveryBackend)

    def discover(self) -> dict:
        return self._backend.discover()

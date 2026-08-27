"""
FollowUpService (dashboard client) — asks evidence-grounded follow-up
questions about a completed investigation report and reads back its
conversation. Completely separate from Cost Explorer.

Like InvestigationService/CostRefreshService/ResourceDiscoveryService, this
is a *write-ish/live* action (it triggers a real Gemini call server-side),
so it goes through a Backend ABC (local/S3 data sources have nothing to
POST to), never a direct boto3/Gemini call from the dashboard itself. See
services/CONTRACT.md.

Contract:

    POST /investigation/{investigation_id}/follow-up
      request:  { "question": "..." }
      response: { "investigation_id", "question", "answer", "confidence",
                  "evidence_used": [...], "uncertainties": [...],
                  "follow_up_needed": bool }

    GET /investigation/{investigation_id}/follow-up
      response: { "investigation_id", "conversation": [ {role, content,
                  timestamp, ...}, ... ] } (conversation is [] if no
                  follow-up question has been asked yet - not an error)

investigation_id is built by the caller as f"{run_id}__{resource_id}" -
both values the Report Viewer already has in scope for a rendered report.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .config import AppConfig


class FollowUpActionError(Exception):
    """Raised when a follow-up question cannot reach a backend, or the
    backend rejects it (investigation not found/superseded, bad question,
    Gemini unavailable) - the HTTP status/detail is folded into the
    message so the UI can show it directly."""


class FollowUpBackend(ABC):
    @abstractmethod
    def ask(self, investigation_id: str, question: str) -> dict:
        """Ask a follow-up question. Returns the structured answer dict."""

    @abstractmethod
    def get_conversation(self, investigation_id: str) -> dict:
        """Returns the existing conversation for this investigation, or
        {"conversation": []} if none exists yet."""


class UnavailableFollowUpBackend(FollowUpBackend):
    """Local/S3 data sources have no live endpoint to ask a question through."""

    _MESSAGE = "Follow-up questions require a REST backend. Configure one on the Settings page."

    def ask(self, investigation_id: str, question: str) -> dict:
        raise FollowUpActionError(self._MESSAGE)

    def get_conversation(self, investigation_id: str) -> dict:
        # Reading conversation history is harmless to no-op when there's
        # no live backend - an empty conversation is the honest answer,
        # not an error, since local/S3 mode never had one to begin with.
        return {"investigation_id": investigation_id, "conversation": []}


class RestFollowUpBackend(FollowUpBackend):
    """Calls the real backend endpoints."""

    def __init__(self, base_url: str, api_key: str | None = None):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def _headers(self) -> dict:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def ask(self, investigation_id: str, question: str) -> dict:
        import requests

        url = f"{self._base_url}/investigation/{investigation_id}/follow-up"
        try:
            # A follow-up question makes one Gemini call server-side -
            # generous enough timeout to tolerate normal LLM latency
            # without a false failure, matching the same order of
            # magnitude as the Cost Explorer refresh call.
            response = requests.post(url, json={"question": question}, headers=self._headers(), timeout=60)
            if response.status_code >= 400:
                detail = response.json().get("detail") if response.headers.get("content-type", "").startswith("application/json") else response.text
                raise FollowUpActionError(str(detail) if detail else f"Follow-up request failed ({response.status_code}).")
            return response.json()
        except FollowUpActionError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface as FollowUpActionError to callers
            raise FollowUpActionError(f"Failed to POST {url}: {exc}") from exc

    def get_conversation(self, investigation_id: str) -> dict:
        import requests

        url = f"{self._base_url}/investigation/{investigation_id}/follow-up"
        try:
            response = requests.get(url, headers=self._headers(), timeout=15)
            if response.status_code == 404:
                return {"investigation_id": investigation_id, "conversation": []}
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            raise FollowUpActionError(f"Failed to GET {url}: {exc}") from exc


def get_follow_up_backend(config: AppConfig) -> FollowUpBackend:
    """Factory: mirrors get_investigation_backend()/get_cost_explorer_backend()/
    get_resource_discovery_backend() - the single switch point."""
    if config.data_source_type == "rest" and config.rest_base_url:
        return RestFollowUpBackend(base_url=config.rest_base_url, api_key=config.rest_api_key)
    return UnavailableFollowUpBackend()


class FollowUpService:
    def __init__(self, backend: FollowUpBackend):
        self._backend = backend

    @property
    def is_live(self) -> bool:
        return isinstance(self._backend, RestFollowUpBackend)

    def ask(self, investigation_id: str, question: str) -> dict:
        return self._backend.ask(investigation_id, question)

    def get_conversation(self, investigation_id: str) -> dict:
        return self._backend.get_conversation(investigation_id)

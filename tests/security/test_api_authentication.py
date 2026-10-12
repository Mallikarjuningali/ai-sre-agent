"""Regression tests for issue #2 - no authentication on any endpoint
while binding 0.0.0.0.

Covers:
  * api.auth.require_api_key in isolation (env unset -> fail-closed 503,
    missing/malformed/wrong key -> 401, correct Bearer key -> passes)
  * the app-wide wiring: api.app's FastAPI app has the dependency applied
    to every route, and the interactive docs/OpenAPI schema are disabled
    (they'd otherwise publish the route map unauthenticated)

These tests use FastAPI's TestClient and monkeypatch os.environ only -
they never touch AWS, Gemini, or the live output/ tree. The same
investigation-manager import shim as tests/cost_explorer/test_api.py is
applied so api.app imports under this sandbox's Python.
"""

import sys
import types

import pytest

from tests.cost_explorer import _helpers as h  # noqa: F401 - sys.path + datetime.UTC shim

# --- Sandbox compatibility shim (identical rationale to
# --- tests/cost_explorer/test_api.py's module docstring) ---
_stub = types.ModuleType("api.investigation_manager")


class _StubInvestigationManager:
    def __init__(self):
        pass


class _StubBusyError(Exception):
    pass


_stub.InvestigationManager = _StubInvestigationManager
_stub.InvestigationBusyError = _StubBusyError
sys.modules["api.investigation_manager"] = _stub

from fastapi.testclient import TestClient  # noqa: E402

from api.auth import API_KEY_ENV_VAR, require_api_key  # noqa: E402

TEST_KEY = "test-key-that-is-long-enough-to-be-realistic-0123456789"


@pytest.fixture()
def client(monkeypatch):
    """A TestClient against a FRESHLY IMPORTED api.app, with the key env
    var set BEFORE import (app construction binds the dependency)."""
    monkeypatch.setenv(API_KEY_ENV_VAR, TEST_KEY)
    sys.modules.pop("api.auth", None)
    sys.modules.pop("api.app", None)
    import api.app as app_mod  # noqa: E402
    yield TestClient(app_mod.app, raise_server_exceptions=True)
    sys.modules.pop("api.app", None)


class TestRequireApiKeyUnit:
    """The dependency function itself, driven through a throwaway app so
    no api.app import is needed for these."""

    def _make_client(self, monkeypatch, server_key):
        """server_key: the value to set API_KEY_ENV_VAR to, or None to
        leave the server unconfigured (fail-closed path)."""
        from fastapi import Depends, FastAPI

        if server_key is None:
            monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(API_KEY_ENV_VAR, server_key)
        app = FastAPI(dependencies=[Depends(require_api_key)])

        @app.get("/probe")
        def probe():
            return {"ok": True}

        return TestClient(app)

    def test_unset_env_var_fails_closed_with_503(self, monkeypatch):
        resp = self._make_client(monkeypatch, server_key=None).get("/probe")
        assert resp.status_code == 503
        assert API_KEY_ENV_VAR in resp.json()["detail"]

    def test_blank_env_var_is_treated_as_unset(self, monkeypatch):
        resp = self._make_client(monkeypatch, server_key="   ").get("/probe")
        assert resp.status_code == 503

    def test_missing_header_is_401(self, monkeypatch):
        resp = self._make_client(monkeypatch, server_key=TEST_KEY).get("/probe")
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == "Bearer"

    def test_wrong_scheme_is_401(self, monkeypatch):
        client = self._make_client(monkeypatch, server_key=TEST_KEY)
        assert client.get("/probe", headers={"Authorization": f"Basic {TEST_KEY}"}).status_code == 401
        assert client.get("/probe", headers={"Authorization": TEST_KEY}).status_code == 401

    def test_wrong_key_is_401(self, monkeypatch):
        client = self._make_client(monkeypatch, server_key=TEST_KEY)
        resp = client.get("/probe", headers={"Authorization": "Bearer wrong-key"})
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid API key."

    def test_correct_key_passes(self, monkeypatch):
        client = self._make_client(monkeypatch, server_key=TEST_KEY)
        resp = client.get("/probe", headers={"Authorization": f"Bearer {TEST_KEY}"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_scheme_is_case_insensitive(self, monkeypatch):
        client = self._make_client(monkeypatch, server_key=TEST_KEY)
        resp = client.get("/probe", headers={"Authorization": f"bearer {TEST_KEY}"})
        assert resp.status_code == 200


class TestAppWideWiring:
    """The real app: every route class must require the key."""

    def test_unauthenticated_get_is_401(self, client):
        assert client.get("/summary.json").status_code == 401

    def test_unauthenticated_head_is_401(self, client):
        assert client.head("/summary.json").status_code == 401

    def test_unauthenticated_post_is_401(self, client):
        assert client.post("/investigation/full").status_code == 401
        assert client.post("/cost-explorer/refresh", json={}).status_code == 401

    def test_unauthenticated_delete_is_401(self, client):
        assert client.request(
            "DELETE", "/executions", json={"run_ids": ["run_x"]}
        ).status_code == 401

    def test_authenticated_requests_reach_the_route(self, client):
        headers = {"Authorization": f"Bearer {TEST_KEY}"}
        # These hit real handlers - the point is they get PAST auth (any
        # status other than 401/503 means auth let them through; the exact
        # status depends on whatever feed files exist on disk or what the
        # upstream call does, which this test deliberately does not
        # control). /resources is used rather than a manager-backed route
        # because this sandbox's import shim replaces the investigation
        # manager with a stub (see module docstring).
        for resp in (
            client.get("/summary.json", headers=headers),
            client.head("/summary.json", headers=headers),
            client.get("/resources", headers=headers),
        ):
            assert resp.status_code in (200, 404), f"auth blocked a valid key: {resp.status_code}"

    def test_wrong_key_rejected_on_real_app(self, client):
        resp = client.get("/summary.json", headers={"Authorization": "Bearer nope"})
        assert resp.status_code == 401

    def test_docs_and_openapi_are_disabled(self, client):
        headers = {"Authorization": f"Bearer {TEST_KEY}"}
        assert client.get("/docs", headers=headers).status_code == 404
        assert client.get("/redoc", headers=headers).status_code == 404
        assert client.get("/openapi.json", headers=headers).status_code == 404

    def test_unset_env_var_refuses_everything_on_real_app(self, monkeypatch):
        # Import api.app fresh with NO key configured -> 503, not silently open.
        monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
        sys.modules.pop("api.auth", None)
        sys.modules.pop("api.app", None)
        import api.app as app_mod
        try:
            c = TestClient(app_mod.app)
            assert c.get("/summary.json").status_code == 503
            assert c.post("/investigation/full").status_code == 503
        finally:
            sys.modules.pop("api.app", None)

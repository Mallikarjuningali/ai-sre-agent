"""
Tests for the existing Cost Explorer API routes in api/app.py - covers
spec section 6. Stubs api.investigation_manager (Python 3.9 `dict | None`
syntax incompatibility in THIS sandbox only, not a real app bug - same
technique already used by this repo's other API test scripts) so
api.app can be imported at all; every other route/manager is exercised
for real, with CostExplorerManager's own AWS/Gemini calls mocked at the
collector/LLM boundary (never boto3/Gemini directly).
"""
import json
import shutil
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.cost_explorer import _helpers as h  # noqa: F401

# --- Python 3.9 sandbox compatibility shim (see module docstring) ---
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

import api.app as app_mod  # noqa: E402
import utils.cost_dashboard_export as export_mod  # noqa: E402


class TestCostExplorerFeedRoutes(unittest.TestCase):
    """GET/HEAD /cost-explorer/* - pure passthroughs of dashboard_feed/*.json."""

    def setUp(self):
        self.client = TestClient(app_mod.app)
        self._orig_feed_dir = app_mod.COST_FEED_DIR
        self.tmp_feed_dir = Path("output/cost/_test_api_feed")
        shutil.rmtree(self.tmp_feed_dir, ignore_errors=True)
        self.tmp_feed_dir.mkdir(parents=True, exist_ok=True)
        app_mod.COST_FEED_DIR = self.tmp_feed_dir

    def tearDown(self):
        app_mod.COST_FEED_DIR = self._orig_feed_dir
        shutil.rmtree(self.tmp_feed_dir, ignore_errors=True)

    def _write_feed(self, filename, content):
        with open(self.tmp_feed_dir / filename, "w") as f:
            json.dump(content, f)

    def test_get_summary_returns_404_when_not_published(self):
        resp = self.client.get("/cost-explorer/summary")
        self.assertEqual(resp.status_code, 404)

    def test_head_summary_reflects_existence(self):
        self.assertEqual(self.client.head("/cost-explorer/summary").status_code, 404)
        self._write_feed("summary.json", {"currency": "USD"})
        self.assertEqual(self.client.head("/cost-explorer/summary").status_code, 200)

    def test_get_summary_returns_published_content(self):
        self._write_feed("summary.json", {"currency": "USD", "current_period": {}})
        resp = self.client.get("/cost-explorer/summary")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["currency"], "USD")

    def test_all_seven_read_routes_present_and_working(self):
        routes = ["summary", "history", "credits", "services", "regions", "anomalies", "report"]
        for name in routes:
            self._write_feed(f"{name}.json", {"marker": name})
            resp = self.client.get(f"/cost-explorer/{name}")
            self.assertEqual(resp.status_code, 200, name)
            self.assertEqual(resp.json()["marker"], name)

    def test_comparison_returns_null_content_not_404_when_null(self):
        self._write_feed("comparison.json", None)
        resp = self.client.get("/cost-explorer/comparison")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json())


def _wait_for_terminal(client, run_id, timeout=5.0):
    import time as _time
    deadline = _time.time() + timeout
    last = None
    while _time.time() < deadline:
        resp = client.get(f"/cost-explorer/status/{run_id}")
        if resp.status_code == 200:
            last = resp.json()
            if last["status"] in ("COMPLETED", "FAILED"):
                return last
        _time.sleep(0.02)
    raise AssertionError(f"run {run_id} never reached a terminal state; last={last}")


class TestCostExplorerRefreshRoute(unittest.TestCase):
    """POST /cost-explorer/refresh (async: returns run_id immediately) +
    GET /cost-explorer/status/{run_id} - refresh behavior + error
    handling, with CostExplorerManager's real start_refresh()/background
    thread exercised but every AWS/Gemini boundary mocked."""

    def setUp(self):
        self.client = TestClient(app_mod.app)
        # Fresh manager per test so busy-state/run history from one test
        # never leaks into the next.
        self._orig_manager = app_mod.cost_manager
        from api.cost_explorer_manager import CostExplorerManager
        app_mod.cost_manager = CostExplorerManager()

    def tearDown(self):
        app_mod.cost_manager = self._orig_manager

    def test_post_refresh_returns_run_id_immediately(self):
        from llm.llm_engine import LLMEngine
        with patch("collector.cost_explorer.main"), \
             patch.object(LLMEngine, "__init__", lambda self: None), \
             patch.object(LLMEngine, "analyze", lambda self, prompt: json.dumps({"severity": "LOW"})), \
             patch("utils.cost_dashboard_export.export"):
            resp = self.client.post("/cost-explorer/refresh", json={})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("run_id", body)
        self.assertIn(body["status"], ("QUEUED", "RUNNING"))
        self.assertIn("started_at", body)
        # The synchronous response must NOT contain the final report - it
        # arrives asynchronously via the status poll below.
        self.assertNotIn("report", body)

    def test_refresh_completes_and_status_reports_completed_with_report(self):
        from llm.llm_engine import LLMEngine
        with patch("collector.cost_explorer.main") as mocked_main, \
             patch.object(LLMEngine, "__init__", lambda self: None), \
             patch.object(LLMEngine, "analyze", lambda self, prompt: json.dumps({"severity": "LOW"})), \
             patch("utils.cost_dashboard_export.export"):
            run_id = self.client.post("/cost-explorer/refresh", json={}).json()["run_id"]
            final = _wait_for_terminal(self.client, run_id)
        self.assertEqual(final["status"], "COMPLETED")
        self.assertEqual(final["report"]["severity"], "LOW")
        self.assertEqual(final["percent"], 100)
        mocked_main.assert_called_once()
        self.assertEqual(mocked_main.call_args.kwargs["from_date"], None)
        self.assertEqual(mocked_main.call_args.kwargs["to_date"], None)

    def test_refresh_with_comparison_dates_forwarded_to_collector(self):
        from llm.llm_engine import LLMEngine
        with patch("collector.cost_explorer.main") as mocked_main, \
             patch.object(LLMEngine, "__init__", lambda self: None), \
             patch.object(LLMEngine, "analyze", lambda self, prompt: json.dumps({})), \
             patch("utils.cost_dashboard_export.export"):
            run_id = self.client.post(
                "/cost-explorer/refresh", json={"from_date": "2026-01-01", "to_date": "2026-01-31"}
            ).json()["run_id"]
            _wait_for_terminal(self.client, run_id)
        self.assertEqual(mocked_main.call_args.kwargs["from_date"], "2026-01-01")
        self.assertEqual(mocked_main.call_args.kwargs["to_date"], "2026-01-31")

    def test_refresh_failure_surfaces_as_failed_status_not_http_error(self):
        with patch("collector.cost_explorer.main", side_effect=Exception("boto3 boom")):
            run_id = self.client.post("/cost-explorer/refresh", json={}).json()["run_id"]
            final = _wait_for_terminal(self.client, run_id)
        self.assertEqual(final["status"], "FAILED")
        self.assertIn("boto3 boom", final["error"])

    def test_concurrent_refresh_returns_409(self):
        app_mod.cost_manager._busy = True
        try:
            resp = self.client.post("/cost-explorer/refresh", json={})
            self.assertEqual(resp.status_code, 409)
        finally:
            app_mod.cost_manager._busy = False

    def test_status_for_unknown_run_id_returns_404(self):
        resp = self.client.get("/cost-explorer/status/no-such-run")
        self.assertEqual(resp.status_code, 404)

    def test_empty_body_behaves_same_as_no_body(self):
        from llm.llm_engine import LLMEngine
        with patch("collector.cost_explorer.main") as mocked_main, \
             patch.object(LLMEngine, "__init__", lambda self: None), \
             patch.object(LLMEngine, "analyze", lambda self, prompt: json.dumps({})), \
             patch("utils.cost_dashboard_export.export"):
            run_id = self.client.post("/cost-explorer/refresh").json()["run_id"]
            _wait_for_terminal(self.client, run_id)
        self.assertEqual(mocked_main.call_args.kwargs["from_date"], None)
        self.assertEqual(mocked_main.call_args.kwargs["to_date"], None)


if __name__ == "__main__":
    unittest.main()

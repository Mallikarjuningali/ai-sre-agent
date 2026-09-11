"""
Smoke tests for the Cost Explorer dashboard page (Phase 6) using
Streamlit's own AppTest harness - proves the new async-refresh progress
panel, forecast card, and tag allocation section all render without
raising, for a representative set of real-shaped feed data. Does not
assert exact visual output (that needs a browser); it asserts the page
executes cleanly end-to-end, which is what an import/syntax check alone
cannot prove for Streamlit render code.
"""
import unittest

from tests.cost_explorer import _helpers as h  # noqa: F401

try:
    from streamlit.testing.v1 import AppTest
    _APPTEST_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on installed streamlit version
    _APPTEST_AVAILABLE = False


_SCRIPT = '''
import sys
sys.path.insert(0, "/Users/mallikarjun.ingali/Desktop/personal/ai-sre-agent")
sys.path.insert(0, "/Users/mallikarjun.ingali/Desktop/personal/ai-sre-agent/dashboard")

from types import SimpleNamespace

from components.views import cost_explorer


class _StubCostExplorerService:
    def get_summary(self):
        return {"currency": "USD", "period": {}, "generated_at": "2026-01-15T10:00:00+05:30",
                "current_period": {
                    "from": "2026-01-01", "to": "2026-01-15", "currency": "USD",
                    "gross_cost": 120.0, "credits": {"total": -20.0, "currency": "USD", "history": []},
                    "net_cost": 100.0, "daily_history": [["2026-01-01", 50.0], ["2026-01-02", 50.0]],
                    "daily_breakdown": [["2026-01-01", 60, -10, 50], ["2026-01-02", 60, -10, 50]],
                    "service_breakdown": [{"service": "Amazon EC2", "gross_cost": 100.0, "credits": -20.0, "net_cost": 80.0, "currency": "USD"}],
                    "region_breakdown": [{"region": "us-east-1", "gross_cost": 120.0, "credits": -20.0, "net_cost": 100.0, "currency": "USD"}],
                },
                "previous_period": {
                    "from": "2025-12-17", "to": "2025-12-31", "currency": "USD",
                    "gross_cost": 90.0, "credits": {"total": 0, "currency": "USD", "history": []},
                    "net_cost": 90.0, "daily_history": [], "daily_breakdown": [],
                    "service_breakdown": [], "region_breakdown": [],
                },
                "change": {"gross_cost_change": 30.0, "gross_cost_change_percent": 33.3,
                           "credits_change": -20.0, "net_cost_change": 10.0, "net_cost_change_percent": 11.1}}

    def get_comparison(self):
        return {}

    def get_anomalies(self):
        return {"status": "none_found", "reason": None, "anomalies": [],
                "requested_start": None, "requested_end": None, "analyzed_start": None,
                "analyzed_end": None, "supported_from": None, "supported": True, "partial": False}

    def get_forecast(self):
        return {"status": "available", "reason": None, "forecast_amount": 245.67, "currency": "USD",
                "period": {"from": "2026-01-15", "to": "2026-01-31"},
                "prediction_interval_lower": 200.0, "prediction_interval_upper": 290.0}

    def get_tags(self):
        return {"tag_key": "Environment", "status": "available", "reason": None,
                "breakdown": [{"tag_value": "Production", "cost": 80.0, "currency": "USD"}],
                "untagged_cost": 15.0, "currency": "USD"}

    def get_report(self):
        return {}


class _StubCostRefreshService:
    is_live = True

    def start_refresh(self, from_date=None, to_date=None, tag_key=None):
        return {"run_id": "test-run", "status": "QUEUED", "started_at": "2026-01-15T10:00:00+05:30"}

    def get_run_status(self, run_id):
        return {"run_id": run_id, "status": "COMPLETED", "percent": 100, "phase_label": "Completed",
                "phases": [], "error": None, "report": {}, "elapsed_seconds": 2}


services = SimpleNamespace(cost_explorer=_StubCostExplorerService(), cost_refresh=_StubCostRefreshService())
config = SimpleNamespace()

cost_explorer.render(services, config)
'''


@unittest.skipUnless(_APPTEST_AVAILABLE, "streamlit.testing.v1.AppTest not available in this environment")
class TestCostExplorerDashboardSmoke(unittest.TestCase):

    def test_page_renders_without_exceptions_forecast_and_tags_available(self):
        at = AppTest.from_string(_SCRIPT, default_timeout=15)
        at.run()
        self.assertFalse(at.exception, f"Dashboard page raised: {[str(e) for e in at.exception]}")

    def test_forecast_card_and_tag_section_text_present(self):
        at = AppTest.from_string(_SCRIPT, default_timeout=15)
        at.run()
        self.assertFalse(at.exception)
        all_markdown_text = " ".join(m.value or "" for m in at.markdown)
        self.assertIn("Cost Forecast", all_markdown_text)
        self.assertIn("Environment", all_markdown_text)

    def test_unavailable_states_and_active_run_do_not_crash(self):
        script = _SCRIPT
        script = script.replace(
            '"status": "available", "reason": None, "forecast_amount": 245.67, "currency": "USD",\n'
            '                "period": {"from": "2026-01-15", "to": "2026-01-31"},\n'
            '                "prediction_interval_lower": 200.0, "prediction_interval_upper": 290.0}',
            '"status": "unavailable", "reason": "AccessDenied", "forecast_amount": None, "currency": None,\n'
            '                "period": {}, "prediction_interval_lower": None, "prediction_interval_upper": None}',
        )
        script = script.replace(
            '"tag_key": "Environment", "status": "available", "reason": None,\n'
            '                "breakdown": [{"tag_value": "Production", "cost": 80.0, "currency": "USD"}],\n'
            '                "untagged_cost": 15.0, "currency": "USD"}',
            '"tag_key": "Team", "status": "not_activated", '
            '"reason": "\\"Team\\" exists but is not activated for cost allocation.",\n'
            '                "breakdown": [], "untagged_cost": None, "currency": None}',
        )
        # Also exercise the active-refresh-in-progress polling panel.
        script = script.replace(
            'def get_run_status(self, run_id):\n'
            '        return {"run_id": run_id, "status": "COMPLETED"',
            'def get_run_status(self, run_id):\n'
            '        return {"run_id": run_id, "status": "RUNNING"',
        )
        script = script.replace(
            "cost_explorer.render(services, config)",
            'import streamlit as st\n'
            'st.session_state["cost_explorer_refresh_run"] = {"run_id": "test-run", "started_at_iso": None, "started_at_epoch": 0, "last_status": None}\n'
            "cost_explorer.render(services, config)",
        )
        at = AppTest.from_string(script, default_timeout=15)
        at.run()
        self.assertFalse(at.exception, f"Dashboard page raised: {[str(e) for e in at.exception]}")
        all_markdown_text = " ".join(m.value or "" for m in at.markdown)
        self.assertIn("not activated", all_markdown_text.lower())


if __name__ == "__main__":
    unittest.main()

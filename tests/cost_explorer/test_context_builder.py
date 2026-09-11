"""
Tests for context/cost_context_builder.py - expected context structure,
missing fields, empty service/region data, numeric consistency (gross =
net - credits at every level). Covers spec section 4.
"""
import unittest

from tests.cost_explorer import _helpers as h  # noqa: F401

from context.cost_context_builder import CostContextBuilder


def _period_raw(total_cost=100.0, credits_total=-10.0, service=None, region=None, daily=None):
    service_net = [{"service": "Amazon EC2", "cost": 90.0, "currency": "USD"}] if service is None else service
    region_net = [{"region": "us-east-1", "cost": 100.0, "currency": "USD"}] if region is None else region
    return {
        "from": "2026-01-01", "to": "2026-01-14", "currency": "USD",
        "total_cost": total_cost,
        "daily_history": daily or [["2026-01-01", 50.0], ["2026-01-02", 50.0]],
        "service_breakdown": service_net,
        "service_credit_breakdown": [{"service": "Amazon EC2", "cost": -10.0, "currency": "USD"}] if service is None else [],
        "region_breakdown": region_net,
        "region_credit_breakdown": [{"region": "us-east-1", "cost": -10.0, "currency": "USD"}] if region is None else [],
        "credits": {"total": credits_total, "currency": "USD", "history": [["2026-01-01", -10.0]]},
    }


class TestBuildContext(unittest.TestCase):

    def setUp(self):
        self.builder = CostContextBuilder()

    def test_expected_top_level_structure(self):
        raw = {"currency": "USD", "period": {"lookback_days": 14},
               "current_period": _period_raw(), "previous_period": _period_raw(total_cost=80.0),
               "anomalies": {"status": "not_configured"}, "comparison": None}
        context = self.builder.build_context(raw)
        for key in ("generated_by", "currency", "period", "current_period", "previous_period",
                    "change", "service_comparison", "region_comparison", "anomalies", "comparison"):
            self.assertIn(key, context)
        self.assertIsNone(context["comparison"])

    def test_gross_cost_equals_net_minus_credits_at_period_level(self):
        raw = {"current_period": _period_raw(total_cost=100.0, credits_total=-10.0),
               "previous_period": _period_raw(total_cost=80.0)}
        context = self.builder.build_context(raw)
        self.assertEqual(context["current_period"]["net_cost"], 100.0)
        self.assertEqual(context["current_period"]["gross_cost"], 110.0)  # 100 - (-10)

    def test_gross_cost_equals_net_minus_credits_per_service(self):
        raw = {"current_period": _period_raw(), "previous_period": _period_raw()}
        context = self.builder.build_context(raw)
        ec2 = context["current_period"]["service_breakdown"][0]
        self.assertEqual(ec2["net_cost"], 90.0)
        self.assertEqual(ec2["credits"], -10.0)
        self.assertEqual(ec2["gross_cost"], 100.0)  # 90 - (-10)

    def test_missing_fields_do_not_crash_and_produce_none(self):
        context = self.builder.build_context({})
        self.assertIsNone(context["current_period"]["gross_cost"])
        self.assertEqual(context["current_period"]["service_breakdown"], [])
        self.assertIsNone(context["change"]["net_cost_change"])

    def test_empty_service_and_region_data(self):
        raw = {"current_period": _period_raw(service=[], region=[]), "previous_period": _period_raw(service=[], region=[])}
        context = self.builder.build_context(raw)
        self.assertEqual(context["current_period"]["service_breakdown"], [])
        self.assertEqual(context["current_period"]["region_breakdown"], [])

    def test_service_present_only_in_one_period_gets_zero_for_the_other_side(self):
        service_only_current = [{"service": "Amazon S3", "cost": 5.0, "currency": "USD"}]
        raw = {
            "current_period": _period_raw(service=service_only_current),
            "previous_period": _period_raw(service=[]),
        }
        context = self.builder.build_context(raw)
        comparison = {c["service"]: c for c in context["service_comparison"]}
        self.assertEqual(comparison["Amazon S3"]["period_b_cost"], 0)

    def test_percent_change_none_when_old_value_is_zero(self):
        self.assertIsNone(CostContextBuilder._percent_change(10.0, 0))

    def test_percent_change_none_when_either_missing(self):
        self.assertIsNone(CostContextBuilder._percent_change(None, 10.0))
        self.assertIsNone(CostContextBuilder._percent_change(10.0, None))

    def test_zero_cost_case_produces_zero_not_none(self):
        raw = {"current_period": _period_raw(total_cost=0.0, credits_total=0.0),
               "previous_period": _period_raw(total_cost=0.0, credits_total=0.0)}
        context = self.builder.build_context(raw)
        self.assertEqual(context["current_period"]["net_cost"], 0.0)
        self.assertEqual(context["current_period"]["gross_cost"], 0.0)

    def test_comparison_block_built_when_present(self):
        raw = {
            "current_period": _period_raw(), "previous_period": _period_raw(),
            "comparison": {
                "period_a": _period_raw(total_cost=120.0),
                "period_b": _period_raw(total_cost=100.0),
                "period_a_anomalies": {"status": "none_found"},
                "period_b_anomalies": {"status": "not_configured"},
            },
        }
        context = self.builder.build_context(raw)
        self.assertIsNotNone(context["comparison"])
        self.assertEqual(context["comparison"]["difference"], 20.0)
        self.assertIn("service_comparison", context["comparison"])
        self.assertEqual(context["comparison"]["anomaly_comparison"]["selected_period"]["status"], "none_found")


if __name__ == "__main__":
    unittest.main()

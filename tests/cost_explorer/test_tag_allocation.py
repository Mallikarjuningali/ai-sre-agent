"""
Tests for tag-based cost allocation (Phase 3 / B8):
collector/cost_explorer.py::get_tag_activation_status/get_tag_breakdown,
context/cost_context_builder.py::_build_tag_allocation, and
utils/cost_dashboard_export.py::build_tags. Covers spec Phase 3's 6
required cases: active tag with data, not activated, tag doesn't exist,
zero cost, untagged resources, and AWS API failure.
"""
import unittest
from datetime import date
from unittest.mock import MagicMock

from tests.cost_explorer import _helpers as h  # noqa: F401

import collector.cost_explorer as ce_mod
from context.cost_context_builder import CostContextBuilder
import utils.cost_dashboard_export as export_mod


def _mock_ce():
    return MagicMock()


class TestGetTagActivationStatus(unittest.TestCase):

    def setUp(self):
        self._orig_ce = ce_mod.ce
        ce_mod.ce = _mock_ce()

    def tearDown(self):
        ce_mod.ce = self._orig_ce

    def test_active_tag(self):
        ce_mod.ce.list_cost_allocation_tags.return_value = {
            "CostAllocationTags": [{"TagKey": "Environment", "Status": "Active"}]
        }
        status, error = ce_mod.get_tag_activation_status("Environment")
        self.assertEqual(status, "active")
        self.assertIsNone(error)

    def test_inactive_tag(self):
        ce_mod.ce.list_cost_allocation_tags.return_value = {
            "CostAllocationTags": [{"TagKey": "Team", "Status": "Inactive"}]
        }
        status, error = ce_mod.get_tag_activation_status("Team")
        self.assertEqual(status, "inactive")

    def test_tag_not_found(self):
        ce_mod.ce.list_cost_allocation_tags.return_value = {"CostAllocationTags": []}
        status, error = ce_mod.get_tag_activation_status("NoSuchTag")
        self.assertEqual(status, "not_found")

    def test_api_failure(self):
        ce_mod.ce.list_cost_allocation_tags.side_effect = Exception("AccessDenied")
        status, error = ce_mod.get_tag_activation_status("Environment")
        self.assertIsNone(status)
        self.assertIn("AccessDenied", error)


class TestGetTagBreakdown(unittest.TestCase):

    def setUp(self):
        self._orig_ce = ce_mod.ce
        ce_mod.ce = _mock_ce()

    def tearDown(self):
        ce_mod.ce = self._orig_ce

    def _active(self):
        ce_mod.ce.list_cost_allocation_tags.return_value = {
            "CostAllocationTags": [{"TagKey": "Environment", "Status": "Active"}]
        }

    def test_case_1_active_tag_with_real_cost_data(self):
        self._active()
        ce_mod.ce.get_cost_and_usage.return_value = {
            "ResultsByTime": [{"Groups": [
                {"Keys": ["Environment$Production"], "Metrics": {"UnblendedCost": {"Amount": "80.00", "Unit": "USD"}}},
                {"Keys": ["Environment$Staging"], "Metrics": {"UnblendedCost": {"Amount": "20.00", "Unit": "USD"}}},
            ]}]
        }
        result = ce_mod.get_tag_breakdown(date(2026, 1, 1), date(2026, 1, 15), "Environment")
        self.assertEqual(result["status"], "available")
        values = {b["tag_value"]: b["cost"] for b in result["breakdown"]}
        self.assertEqual(values["Production"], 80.0)
        self.assertEqual(values["Staging"], 20.0)
        self.assertIsNone(result["untagged_cost"])

    def test_case_2_tag_not_activated_for_billing(self):
        ce_mod.ce.list_cost_allocation_tags.return_value = {
            "CostAllocationTags": [{"TagKey": "Team", "Status": "Inactive"}]
        }
        result = ce_mod.get_tag_breakdown(date(2026, 1, 1), date(2026, 1, 15), "Team")
        self.assertEqual(result["status"], "not_activated")
        self.assertIn("not activated", result["reason"])
        ce_mod.ce.get_cost_and_usage.assert_not_called()

    def test_case_3_tag_key_does_not_exist(self):
        ce_mod.ce.list_cost_allocation_tags.return_value = {"CostAllocationTags": []}
        result = ce_mod.get_tag_breakdown(date(2026, 1, 1), date(2026, 1, 15), "NotARealTag")
        self.assertEqual(result["status"], "unsupported")
        ce_mod.ce.get_cost_and_usage.assert_not_called()

    def test_case_4_no_costs_associated_with_tag_is_valid_empty_result(self):
        self._active()
        ce_mod.ce.get_cost_and_usage.return_value = {"ResultsByTime": [{"Groups": []}]}
        result = ce_mod.get_tag_breakdown(date(2026, 1, 1), date(2026, 1, 15), "Environment")
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["breakdown"], [])

    def test_case_5_untagged_resources_surfaced_separately(self):
        self._active()
        ce_mod.ce.get_cost_and_usage.return_value = {
            "ResultsByTime": [{"Groups": [
                {"Keys": ["Environment$Production"], "Metrics": {"UnblendedCost": {"Amount": "80.00", "Unit": "USD"}}},
                {"Keys": ["Environment$"], "Metrics": {"UnblendedCost": {"Amount": "15.00", "Unit": "USD"}}},
            ]}]
        }
        result = ce_mod.get_tag_breakdown(date(2026, 1, 1), date(2026, 1, 15), "Environment")
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["untagged_cost"], 15.0)
        # Untagged bucket must never be folded into the named breakdown.
        self.assertEqual(len(result["breakdown"]), 1)
        self.assertEqual(result["breakdown"][0]["tag_value"], "Production")

    def test_untagged_bucket_that_nets_to_exactly_zero_is_still_available_not_empty(self):
        # Regression: "available" must be decided by whether AWS returned
        # a real group at all, never by whether that group's cost happens
        # to be truthy - a genuine untagged bucket of exactly $0.00 is
        # still real AWS data, not the same as AWS returning zero groups.
        self._active()
        ce_mod.ce.get_cost_and_usage.return_value = {
            "ResultsByTime": [{"Groups": [
                {"Keys": ["Environment$"], "Metrics": {"UnblendedCost": {"Amount": "0.00", "Unit": "USD"}}},
            ]}]
        }
        result = ce_mod.get_tag_breakdown(date(2026, 1, 1), date(2026, 1, 15), "Environment")
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["untagged_cost"], 0.0)

    def test_case_6_aws_permission_failure_returns_structured_error(self):
        self._active()
        ce_mod.ce.get_cost_and_usage.side_effect = Exception("AccessDenied: not authorized for ce:GetCostAndUsage")
        result = ce_mod.get_tag_breakdown(date(2026, 1, 1), date(2026, 1, 15), "Environment")
        self.assertEqual(result["status"], "failed")
        self.assertIn("AccessDenied", result["reason"])

    def test_no_tag_key_requested_is_unsupported_not_a_crash(self):
        result = ce_mod.get_tag_breakdown(date(2026, 1, 1), date(2026, 1, 15), None)
        self.assertEqual(result["status"], "unsupported")

    def test_dynamic_tag_key_not_hardcoded(self):
        """The exact same function must work for ANY tag key name -
        Environment/Team/Project/Application, proving nothing is
        hardcoded to one business tag."""
        for tag_key in ("Environment", "Team", "Project", "Application"):
            ce_mod.ce.list_cost_allocation_tags.return_value = {
                "CostAllocationTags": [{"TagKey": tag_key, "Status": "Active"}]
            }
            ce_mod.ce.get_cost_and_usage.return_value = {
                "ResultsByTime": [{"Groups": [
                    {"Keys": [f"{tag_key}$X"], "Metrics": {"UnblendedCost": {"Amount": "1.00", "Unit": "USD"}}},
                ]}]
            }
            result = ce_mod.get_tag_breakdown(date(2026, 1, 1), date(2026, 1, 15), tag_key)
            self.assertEqual(result["tag_key"], tag_key)
            self.assertEqual(result["status"], "available")
            ce_mod.ce.list_cost_allocation_tags.assert_called_with(TagKeys=[tag_key])


class TestTagAllocationContextAndFeed(unittest.TestCase):

    def test_context_builder_passes_through_real_tag_data(self):
        raw_tag = {"tag_key": "Environment", "status": "available", "reason": None,
                   "breakdown": [{"tag_value": "Production", "cost": 80.0, "currency": "USD"}],
                   "untagged_cost": 15.0, "currency": "USD"}
        context = CostContextBuilder().build_context({"tag_breakdown": raw_tag})
        self.assertEqual(context["tag_allocation"]["tag_key"], "Environment")
        self.assertEqual(context["tag_allocation"]["untagged_cost"], 15.0)

    def test_context_builder_none_when_no_tag_requested(self):
        context = CostContextBuilder().build_context({})
        self.assertIsNone(context["tag_allocation"])

    def test_dashboard_feed_build_tags_none_when_not_requested(self):
        self.assertIsNone(export_mod.build_tags({}))

    def test_dashboard_feed_build_tags_passthrough(self):
        context = {"tag_allocation": {"tag_key": "Team", "status": "empty", "reason": "x",
                                       "breakdown": [], "untagged_cost": None, "currency": None}}
        self.assertEqual(export_mod.build_tags(context)["tag_key"], "Team")


if __name__ == "__main__":
    unittest.main()

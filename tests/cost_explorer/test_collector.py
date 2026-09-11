"""
Tests for collector/cost_explorer.py - GetCostAndUsage / GetAnomalies /
GetAnomalyMonitors, all mocked. Covers spec section 1 (collector),
section 2 (cost calculations), and section 3 (anomaly detection).
"""
import unittest
from datetime import date
from unittest.mock import MagicMock

from tests.cost_explorer import _helpers as h  # noqa: F401 - sets sys.path/cwd

import collector.cost_explorer as ce_mod


def _mock_ce():
    return MagicMock()


class TestGetTotalCost(unittest.TestCase):

    def setUp(self):
        self._orig_ce = ce_mod.ce
        ce_mod.ce = _mock_ce()

    def tearDown(self):
        ce_mod.ce = self._orig_ce

    def test_success_single_result(self):
        ce_mod.ce.get_cost_and_usage.return_value = h.cost_and_usage_response(amount="123.456")
        amount, currency = ce_mod.get_total_cost(date(2026, 1, 1), date(2026, 1, 15))
        self.assertEqual(amount, 123.46)
        self.assertEqual(currency, "USD")

    def test_success_multiple_results_summed(self):
        ce_mod.ce.get_cost_and_usage.return_value = {
            "ResultsByTime": [
                {"Total": {"UnblendedCost": {"Amount": "10.00", "Unit": "USD"}}},
                {"Total": {"UnblendedCost": {"Amount": "5.50", "Unit": "USD"}}},
            ]
        }
        amount, currency = ce_mod.get_total_cost(date(2026, 1, 1), date(2026, 2, 15))
        self.assertEqual(amount, 15.50)
        self.assertEqual(currency, "USD")

    def test_empty_response_returns_none(self):
        ce_mod.ce.get_cost_and_usage.return_value = {"ResultsByTime": []}
        amount, currency = ce_mod.get_total_cost(date(2026, 1, 1), date(2026, 1, 15))
        self.assertIsNone(amount)
        self.assertIsNone(currency)

    def test_malformed_missing_amount_treated_as_no_data(self):
        ce_mod.ce.get_cost_and_usage.return_value = {
            "ResultsByTime": [{"Total": {"UnblendedCost": {"Unit": "USD"}}}]
        }
        amount, currency = ce_mod.get_total_cost(date(2026, 1, 1), date(2026, 1, 15))
        self.assertIsNone(amount)
        self.assertIsNone(currency)

    def test_partial_malformed_result_still_sums_valid_ones(self):
        ce_mod.ce.get_cost_and_usage.return_value = {
            "ResultsByTime": [
                {"Total": {"UnblendedCost": {"Unit": "USD"}}},  # missing Amount - skipped
                {"Total": {"UnblendedCost": {"Amount": "20.00", "Unit": "USD"}}},
            ]
        }
        amount, currency = ce_mod.get_total_cost(date(2026, 1, 1), date(2026, 2, 15))
        self.assertEqual(amount, 20.00)

    def test_api_failure_returns_none_none(self):
        ce_mod.ce.get_cost_and_usage.side_effect = Exception("boto3 boom")
        amount, currency = ce_mod.get_total_cost(date(2026, 1, 1), date(2026, 1, 15))
        self.assertIsNone(amount)
        self.assertIsNone(currency)

    def test_negligible_negative_rounds_to_zero(self):
        ce_mod.ce.get_cost_and_usage.return_value = h.cost_and_usage_response(amount="-1.08e-19")
        amount, currency = ce_mod.get_total_cost(date(2026, 1, 1), date(2026, 1, 15))
        self.assertEqual(amount, 0.0)

    def test_date_range_passed_through_to_aws(self):
        ce_mod.ce.get_cost_and_usage.return_value = h.cost_and_usage_response()
        ce_mod.get_total_cost(date(2026, 3, 1), date(2026, 3, 10))
        kwargs = ce_mod.ce.get_cost_and_usage.call_args.kwargs
        self.assertEqual(kwargs["TimePeriod"], {"Start": "2026-03-01", "End": "2026-03-10"})
        self.assertEqual(kwargs["Granularity"], "MONTHLY")


class TestGetDailyHistory(unittest.TestCase):

    def setUp(self):
        self._orig_ce = ce_mod.ce
        ce_mod.ce = _mock_ce()

    def tearDown(self):
        ce_mod.ce = self._orig_ce

    def test_orders_oldest_to_newest_and_rounds(self):
        ce_mod.ce.get_cost_and_usage.return_value = {
            "ResultsByTime": [
                {"TimePeriod": {"Start": "2026-01-03"}, "Total": {"UnblendedCost": {"Amount": "3.001", "Unit": "USD"}}},
                {"TimePeriod": {"Start": "2026-01-01"}, "Total": {"UnblendedCost": {"Amount": "1.004", "Unit": "USD"}}},
            ]
        }
        history = ce_mod.get_daily_history(date(2026, 1, 1), date(2026, 1, 4))
        self.assertEqual(history, [["2026-01-01", 1.0], ["2026-01-03", 3.0]])

    def test_empty_response(self):
        ce_mod.ce.get_cost_and_usage.return_value = {"ResultsByTime": []}
        self.assertEqual(ce_mod.get_daily_history(date(2026, 1, 1), date(2026, 1, 4)), [])

    def test_api_failure_returns_empty_list(self):
        ce_mod.ce.get_cost_and_usage.side_effect = Exception("boom")
        self.assertEqual(ce_mod.get_daily_history(date(2026, 1, 1), date(2026, 1, 4)), [])


class TestGetCreditHistory(unittest.TestCase):

    def setUp(self):
        self._orig_ce = ce_mod.ce
        ce_mod.ce = _mock_ce()

    def tearDown(self):
        ce_mod.ce = self._orig_ce

    def test_zero_cost_account_returns_zero_total_empty_history(self):
        ce_mod.ce.get_cost_and_usage.return_value = {"ResultsByTime": []}
        history, total, currency = ce_mod.get_credit_history(date(2026, 1, 1), date(2026, 1, 4))
        self.assertEqual(history, [])
        self.assertEqual(total, 0.0)
        self.assertIsNone(currency)

    def test_negative_credit_amounts_preserved_as_aws_sign(self):
        ce_mod.ce.get_cost_and_usage.return_value = {
            "ResultsByTime": [
                {"TimePeriod": {"Start": "2026-01-01"}, "Total": {"UnblendedCost": {"Amount": "-10.00", "Unit": "USD"}}},
            ]
        }
        history, total, currency = ce_mod.get_credit_history(date(2026, 1, 1), date(2026, 1, 4))
        self.assertEqual(history, [["2026-01-01", -10.0]])
        self.assertEqual(total, -10.0)

    def test_zero_day_omitted_from_history(self):
        ce_mod.ce.get_cost_and_usage.return_value = {
            "ResultsByTime": [
                {"TimePeriod": {"Start": "2026-01-01"}, "Total": {"UnblendedCost": {"Amount": "0", "Unit": "USD"}}},
            ]
        }
        history, total, currency = ce_mod.get_credit_history(date(2026, 1, 1), date(2026, 1, 4))
        self.assertEqual(history, [])

    def test_api_failure_returns_none_total(self):
        ce_mod.ce.get_cost_and_usage.side_effect = Exception("boom")
        history, total, currency = ce_mod.get_credit_history(date(2026, 1, 1), date(2026, 1, 4))
        self.assertEqual(history, [])
        self.assertIsNone(total)
        self.assertIsNone(currency)


class TestGroupedCostBreakdown(unittest.TestCase):

    def setUp(self):
        self._orig_ce = ce_mod.ce
        ce_mod.ce = _mock_ce()

    def tearDown(self):
        ce_mod.ce = self._orig_ce

    def test_service_breakdown_basic(self):
        ce_mod.ce.get_cost_and_usage.return_value = h.cost_and_usage_response(
            groups=[("Amazon EC2", "50.00"), ("Amazon S3", "10.00")]
        )
        breakdown = ce_mod.get_service_breakdown(date(2026, 1, 1), date(2026, 1, 15))
        self.assertEqual(breakdown[0]["service"], "Amazon EC2")
        self.assertEqual(breakdown[0]["cost"], 50.0)

    def test_paginated_results_are_merged_across_pages(self):
        page1 = h.cost_and_usage_response(groups=[("Amazon EC2", "10.00")], next_token="page2")
        page2 = h.cost_and_usage_response(groups=[("Amazon EC2", "5.00"), ("Amazon S3", "1.00")])
        ce_mod.ce.get_cost_and_usage.side_effect = [page1, page2]
        breakdown = ce_mod.get_service_breakdown(date(2026, 1, 1), date(2026, 1, 15))
        by_name = {b["service"]: b["cost"] for b in breakdown}
        self.assertEqual(by_name["Amazon EC2"], 15.0)  # summed across both pages
        self.assertEqual(by_name["Amazon S3"], 1.0)
        self.assertEqual(ce_mod.ce.get_cost_and_usage.call_count, 2)

    def test_zero_and_negative_totals_are_kept_not_dropped(self):
        ce_mod.ce.get_cost_and_usage.return_value = h.cost_and_usage_response(
            groups=[("Amazon EC2", "0.00"), ("Amazon S3", "-3.00")]
        )
        breakdown = ce_mod.get_service_breakdown(date(2026, 1, 1), date(2026, 1, 15))
        names = {b["service"] for b in breakdown}
        self.assertIn("Amazon EC2", names)
        self.assertIn("Amazon S3", names)

    def test_region_breakdown_uses_region_dimension(self):
        ce_mod.ce.get_cost_and_usage.return_value = h.cost_and_usage_response(groups=[("us-east-1", "5.00")])
        ce_mod.get_region_breakdown(date(2026, 1, 1), date(2026, 1, 15))
        kwargs = ce_mod.ce.get_cost_and_usage.call_args.kwargs
        self.assertEqual(kwargs["GroupBy"], [{"Type": "DIMENSION", "Key": "REGION"}])

    def test_credit_breakdown_filters_record_type(self):
        ce_mod.ce.get_cost_and_usage.return_value = h.cost_and_usage_response(groups=[("Amazon EC2", "-2.00")])
        ce_mod.get_service_credit_breakdown(date(2026, 1, 1), date(2026, 1, 15))
        kwargs = ce_mod.ce.get_cost_and_usage.call_args.kwargs
        self.assertEqual(kwargs["Filter"], {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit"]}})

    def test_api_failure_returns_empty_list(self):
        ce_mod.ce.get_cost_and_usage.side_effect = Exception("boom")
        self.assertEqual(ce_mod.get_service_breakdown(date(2026, 1, 1), date(2026, 1, 15)), [])


class TestComparisonPeriod(unittest.TestCase):
    """Date-range handling - _comparison_period()'s complete-calendar-month
    vs equal-length-window rules, and _is_complete_calendar_month."""

    def test_full_calendar_month_compares_to_previous_month(self):
        start, end = ce_mod._comparison_period(date(2026, 2, 1), date(2026, 3, 1))
        self.assertEqual((start, end), (date(2026, 1, 1), date(2026, 2, 1)))

    def test_january_wraps_to_previous_december(self):
        start, end = ce_mod._comparison_period(date(2026, 1, 1), date(2026, 2, 1))
        self.assertEqual((start, end), (date(2025, 12, 1), date(2026, 1, 1)))

    def test_non_month_window_compares_to_equal_length_preceding_window(self):
        start, end = ce_mod._comparison_period(date(2026, 3, 5), date(2026, 3, 15))
        self.assertEqual((start, end), (date(2026, 2, 23), date(2026, 3, 5)))

    def test_zero_or_negative_duration_raises(self):
        with self.assertRaises(ValueError):
            ce_mod._comparison_period(date(2026, 3, 5), date(2026, 3, 5))


class TestGetAnomalies(unittest.TestCase):
    """Uses dates RELATIVE to the real "today" (never a fixed past date)
    so these tests stay correct regardless of when the suite runs -
    AWS Cost Anomaly Detection's supported window is itself a rolling
    window ending "today" (see _cost_anomaly_earliest_supported_date)."""

    def setUp(self):
        self._orig_ce = ce_mod.ce
        ce_mod.ce = _mock_ce()
        self.today = ce_mod._cost_anomaly_earliest_supported_date() + __import__("datetime").timedelta(days=30)
        self.recent_start = self.today - __import__("datetime").timedelta(days=10)
        self.recent_end = self.today

    def tearDown(self):
        ce_mod.ce = self._orig_ce

    def test_not_configured_when_no_monitors(self):
        ce_mod.ce.get_anomaly_monitors.return_value = {"AnomalyMonitors": []}
        result = ce_mod.get_anomalies(self.recent_start, self.recent_end)
        self.assertEqual(result["status"], "not_configured")
        self.assertEqual(result["anomalies"], [])

    def test_none_found_when_monitors_exist_but_no_anomalies(self):
        ce_mod.ce.get_anomaly_monitors.return_value = {"AnomalyMonitors": [h.anomaly_monitor()]}
        ce_mod.ce.get_anomalies.return_value = {"Anomalies": []}
        result = ce_mod.get_anomalies(self.recent_start, self.recent_end)
        self.assertEqual(result["status"], "none_found")
        self.assertTrue(result["supported"])

    def test_found_maps_all_real_fields(self):
        ce_mod.ce.get_anomaly_monitors.return_value = {"AnomalyMonitors": [h.anomaly_monitor()]}
        ce_mod.ce.get_anomalies.return_value = {"Anomalies": [h.anomaly_record()]}
        result = ce_mod.get_anomalies(self.recent_start, self.recent_end)
        self.assertEqual(result["status"], "found")
        anomaly = result["anomalies"][0]
        self.assertEqual(anomaly["service"], "Amazon EC2")
        self.assertEqual(anomaly["region"], "us-east-1")
        self.assertEqual(anomaly["total_impact"], 42.5)
        self.assertEqual(anomaly["monitor_name"], "Default Monitor")

    def test_unsupported_range_when_zero_overlap_with_rolling_window(self):
        ce_mod.ce.get_anomaly_monitors.return_value = {"AnomalyMonitors": [h.anomaly_monitor()]}
        far_past_start = date(2020, 1, 1)
        far_past_end = date(2020, 1, 5)
        result = ce_mod.get_anomalies(far_past_start, far_past_end)
        self.assertEqual(result["status"], "unsupported_range")
        self.assertFalse(result["supported"])
        ce_mod.ce.get_anomalies.assert_not_called()

    def test_partial_window_flagged_when_start_before_supported_floor(self):
        ce_mod.ce.get_anomaly_monitors.return_value = {"AnomalyMonitors": [h.anomaly_monitor()]}
        ce_mod.ce.get_anomalies.return_value = {"Anomalies": []}
        earliest_supported = ce_mod._cost_anomaly_earliest_supported_date()
        requested_start = earliest_supported - __import__("datetime").timedelta(days=30)
        result = ce_mod.get_anomalies(requested_start, earliest_supported + __import__("datetime").timedelta(days=5))
        self.assertTrue(result["partial"])

    def test_monitor_list_api_failure_returns_unavailable(self):
        ce_mod.ce.get_anomaly_monitors.side_effect = Exception("AccessDenied")
        result = ce_mod.get_anomalies(self.recent_start, self.recent_end)
        self.assertEqual(result["status"], "unavailable")

    def test_get_anomalies_api_failure_returns_unavailable(self):
        ce_mod.ce.get_anomaly_monitors.return_value = {"AnomalyMonitors": [h.anomaly_monitor()]}
        ce_mod.ce.get_anomalies.side_effect = Exception("Throttling")
        result = ce_mod.get_anomalies(self.recent_start, self.recent_end)
        self.assertEqual(result["status"], "unavailable")

    def test_validation_exception_retries_once_with_aws_corrected_boundary(self):
        import botocore.exceptions as bce
        ce_mod.ce.get_anomaly_monitors.return_value = {"AnomalyMonitors": [h.anomaly_monitor()]}

        corrected_boundary = self.recent_start + __import__("datetime").timedelta(days=2)
        error_response = {"Error": {"Code": "ValidationException", "Message": (
            f"Earliest supported detectionDate for GetRecentAnomalies is {corrected_boundary.isoformat()}."
        )}}
        validation_exc = bce.ClientError(error_response, "GetAnomalies")

        ce_mod.ce.get_anomalies.side_effect = [validation_exc, {"Anomalies": []}]

        result = ce_mod.get_anomalies(self.recent_start, self.recent_end)
        self.assertEqual(result["status"], "none_found")
        self.assertEqual(ce_mod.ce.get_anomalies.call_count, 2)

    def test_validation_exception_without_retry_success_is_unsupported_range(self):
        import botocore.exceptions as bce
        ce_mod.ce.get_anomaly_monitors.return_value = {"AnomalyMonitors": [h.anomaly_monitor()]}
        error_response = {"Error": {"Code": "ValidationException", "Message": "no date in this message"}}
        ce_mod.ce.get_anomalies.side_effect = bce.ClientError(error_response, "GetAnomalies")
        result = ce_mod.get_anomalies(self.recent_start, self.recent_end)
        self.assertEqual(result["status"], "unsupported_range")
        ce_mod.ce.get_anomalies.assert_called_once()


if __name__ == "__main__":
    unittest.main()

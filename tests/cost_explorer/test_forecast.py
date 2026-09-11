"""
Tests for cost forecasting (Phase 4 / B7):
collector/cost_explorer.py::get_cost_forecast/_forecast_period_bounds,
context/cost_context_builder.py::_build_forecast, and
utils/cost_dashboard_export.py::build_forecast. Covers spec Phase 4's
required cases: forecast available, insufficient historical data,
unsupported period, AWS API error, empty response, and the explicit
"never represent unavailable as zero" requirement.
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


class TestForecastPeriodBounds(unittest.TestCase):

    def test_forecast_period_is_today_through_start_of_next_month(self):
        start, end = ce_mod._forecast_period_bounds()
        today = start
        self.assertGreaterEqual(end, start)
        # end must be the 1st of a month, strictly after start
        self.assertEqual(end.day, 1)
        self.assertGreater(end, today)


class TestGetCostForecast(unittest.TestCase):

    def setUp(self):
        self._orig_ce = ce_mod.ce
        ce_mod.ce = _mock_ce()

    def tearDown(self):
        ce_mod.ce = self._orig_ce

    def test_forecast_available_with_prediction_interval(self):
        ce_mod.ce.get_cost_forecast.return_value = {
            "Total": {"Amount": "245.67", "Unit": "USD"},
            "ForecastResultsByTime": [{
                "TimePeriod": {"Start": "2026-01-15", "End": "2026-02-01"},
                "MeanValue": "245.67",
                "PredictionIntervalLowerBound": "200.00",
                "PredictionIntervalUpperBound": "290.00",
            }],
        }
        result = ce_mod.get_cost_forecast(date(2026, 1, 15), date(2026, 2, 1))
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["forecast_amount"], 245.67)
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["prediction_interval_lower"], 200.0)
        self.assertEqual(result["prediction_interval_upper"], 290.0)

    def test_period_uses_from_to_keys_matching_every_other_period_in_this_module(self):
        # Regression: the dashboard's shared _format_period_range() helper
        # reads period["from"]/period["to"] for every other period dict in
        # this codebase (current_period/previous_period/selected/
        # comparison_period) - if get_cost_forecast() ever used
        # "start"/"end" instead, the forecast card would silently render
        # a blank period range.
        ce_mod.ce.get_cost_forecast.return_value = {"Total": {"Amount": "1.00", "Unit": "USD"}, "ForecastResultsByTime": []}
        result = ce_mod.get_cost_forecast(date(2026, 1, 15), date(2026, 2, 1))
        self.assertEqual(set(result["period"].keys()), {"from", "to"})
        self.assertEqual(result["period"]["from"], "2026-01-15")
        self.assertEqual(result["period"]["to"], "2026-01-31")

    def test_forecast_uses_real_aws_metric_and_granularity(self):
        ce_mod.ce.get_cost_forecast.return_value = {"Total": {"Amount": "1.00", "Unit": "USD"}, "ForecastResultsByTime": []}
        ce_mod.get_cost_forecast(date(2026, 1, 15), date(2026, 2, 1))
        kwargs = ce_mod.ce.get_cost_forecast.call_args.kwargs
        self.assertEqual(kwargs["Metric"], "UNBLENDED_COST")
        self.assertEqual(kwargs["Granularity"], "MONTHLY")
        self.assertEqual(kwargs["TimePeriod"], {"Start": "2026-01-15", "End": "2026-02-01"})

    def test_insufficient_historical_data(self):
        import botocore.exceptions as bce
        error_response = {"Error": {"Code": "DataUnavailableException", "Message": "Not enough historical data."}}
        ce_mod.ce.get_cost_forecast.side_effect = bce.ClientError(error_response, "GetCostForecast")
        result = ce_mod.get_cost_forecast(date(2026, 1, 15), date(2026, 2, 1))
        self.assertEqual(result["status"], "insufficient_data")
        self.assertIsNone(result["forecast_amount"])

    def test_unsupported_period_validation_exception(self):
        import botocore.exceptions as bce
        error_response = {"Error": {"Code": "ValidationException", "Message": "Start date must be today or later."}}
        ce_mod.ce.get_cost_forecast.side_effect = bce.ClientError(error_response, "GetCostForecast")
        result = ce_mod.get_cost_forecast(date(2020, 1, 1), date(2020, 2, 1))
        self.assertEqual(result["status"], "unsupported_period")
        self.assertIsNone(result["forecast_amount"])

    def test_generic_api_error_returns_unavailable_never_zero(self):
        ce_mod.ce.get_cost_forecast.side_effect = Exception("AccessDenied: not authorized for ce:GetCostForecast")
        result = ce_mod.get_cost_forecast(date(2026, 1, 15), date(2026, 2, 1))
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["forecast_amount"])  # never 0 - genuinely unknown

    def test_empty_response_returns_unavailable_never_zero(self):
        ce_mod.ce.get_cost_forecast.return_value = {"ForecastResultsByTime": []}
        result = ce_mod.get_cost_forecast(date(2026, 1, 15), date(2026, 2, 1))
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["forecast_amount"])

    def test_multi_period_result_has_no_single_prediction_interval(self):
        ce_mod.ce.get_cost_forecast.return_value = {
            "Total": {"Amount": "10.00", "Unit": "USD"},
            "ForecastResultsByTime": [
                {"TimePeriod": {"Start": "2026-01-15", "End": "2026-01-16"}, "MeanValue": "5.00"},
                {"TimePeriod": {"Start": "2026-01-16", "End": "2026-01-17"}, "MeanValue": "5.00"},
            ],
        }
        result = ce_mod.get_cost_forecast(date(2026, 1, 15), date(2026, 1, 17), granularity="DAILY")
        self.assertEqual(result["status"], "available")
        self.assertIsNone(result["prediction_interval_lower"])
        self.assertIsNone(result["prediction_interval_upper"])


class TestForecastContextAndFeed(unittest.TestCase):

    def test_context_builder_passes_through_real_forecast(self):
        raw_forecast = {"status": "available", "reason": None, "forecast_amount": 245.67,
                         "currency": "USD", "period": {"from": "2026-01-15", "to": "2026-01-31"},
                         "prediction_interval_lower": 200.0, "prediction_interval_upper": 290.0}
        context = CostContextBuilder().build_context({"forecast": raw_forecast})
        self.assertEqual(context["forecast"]["forecast_amount"], 245.67)
        self.assertEqual(context["forecast"]["status"], "available")

    def test_context_builder_none_only_when_key_absent(self):
        context = CostContextBuilder().build_context({})
        self.assertIsNone(context["forecast"])

    def test_context_builder_preserves_unavailable_status_not_zero(self):
        raw_forecast = {"status": "unavailable", "reason": "boom", "forecast_amount": None,
                         "currency": None, "period": {}, "prediction_interval_lower": None,
                         "prediction_interval_upper": None}
        context = CostContextBuilder().build_context({"forecast": raw_forecast})
        self.assertEqual(context["forecast"]["status"], "unavailable")
        self.assertIsNone(context["forecast"]["forecast_amount"])

    def test_dashboard_feed_build_forecast_passthrough(self):
        context = {"forecast": {"status": "available", "forecast_amount": 10.0}}
        self.assertEqual(export_mod.build_forecast(context)["forecast_amount"], 10.0)

    def test_dashboard_feed_build_forecast_none_when_absent(self):
        self.assertIsNone(export_mod.build_forecast({}))


if __name__ == "__main__":
    unittest.main()

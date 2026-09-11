"""
Tests for Phase 5 (Gemini prompt integration): llm/cost_prompt_builder.py
must embed the ACTUAL collected tag/forecast data (never fabricate it),
correctly distinguish every real status, never invent a budget, and keep
sanitization in place. Covers spec CASE 7/8 (Gemini receives actual
forecast/tag data, and receives unavailable states correctly).
"""
import unittest

from tests.cost_explorer import _helpers as h  # noqa: F401

from llm.cost_prompt_builder import CostPromptBuilder


class TestPromptReceivesRealTagData(unittest.TestCase):

    def test_available_tag_data_embedded_verbatim(self):
        context = {
            "current_period": {}, "previous_period": {},
            "tag_allocation": {
                "tag_key": "Environment", "status": "available", "reason": None,
                "breakdown": [{"tag_value": "Production", "cost": 80.0, "currency": "USD"}],
                "untagged_cost": 15.0, "currency": "USD",
            },
        }
        prompt = CostPromptBuilder().build_prompt(context)
        self.assertIn("Environment", prompt)
        self.assertIn("Production", prompt)
        self.assertIn("80.0", prompt)
        self.assertIn("15.0", prompt)

    def test_not_activated_status_reaches_prompt_distinctly(self):
        context = {"current_period": {}, "tag_allocation": {
            "tag_key": "Team", "status": "not_activated", "reason": "not activated",
            "breakdown": [], "untagged_cost": None, "currency": None,
        }}
        prompt = CostPromptBuilder().build_prompt(context)
        self.assertIn("not_activated", prompt)

    def test_null_tag_allocation_when_not_requested(self):
        context = {"current_period": {}, "tag_allocation": None}
        prompt = CostPromptBuilder().build_prompt(context)
        self.assertIn('"tag_allocation":null', prompt.replace(" ", ""))

class TestPromptReceivesRealForecastData(unittest.TestCase):

    def test_available_forecast_embedded_verbatim(self):
        context = {"current_period": {}, "forecast": {
            "status": "available", "reason": None, "forecast_amount": 245.67,
            "currency": "USD", "period": {"from": "2026-01-15", "to": "2026-01-31"},
            "prediction_interval_lower": 200.0, "prediction_interval_upper": 290.0,
        }}
        prompt = CostPromptBuilder().build_prompt(context)
        self.assertIn("245.67", prompt)
        self.assertIn("2026-01-15", prompt)

    def test_unavailable_forecast_status_reaches_prompt_distinctly(self):
        context = {"current_period": {}, "forecast": {
            "status": "unavailable", "reason": "AccessDenied", "forecast_amount": None,
            "currency": None, "period": {}, "prediction_interval_lower": None,
            "prediction_interval_upper": None,
        }}
        prompt = CostPromptBuilder().build_prompt(context)
        self.assertIn("unavailable", prompt)
        # Must never silently render the missing amount as a literal 0.
        self.assertNotIn('"forecast_amount":0', prompt.replace(" ", ""))

    def test_insufficient_data_status_reaches_prompt_distinctly(self):
        context = {"current_period": {}, "forecast": {
            "status": "insufficient_data", "reason": "not enough history", "forecast_amount": None,
            "currency": None, "period": {}, "prediction_interval_lower": None,
            "prediction_interval_upper": None,
        }}
        prompt = CostPromptBuilder().build_prompt(context)
        self.assertIn("insufficient_data", prompt)

    def test_no_budget_instruction_present(self):
        prompt = CostPromptBuilder().build_prompt({"current_period": {}})
        self.assertIn("budget", prompt.lower())
        self.assertIn("no such data exists", prompt.lower())

    def test_prompt_instructs_never_to_calculate_forecast(self):
        prompt = CostPromptBuilder().build_prompt({"current_period": {}})
        self.assertIn("never calculate", prompt.lower())


class TestSanitizationStillAppliesToTagForecastSections(unittest.TestCase):

    def test_account_id_key_still_dropped_even_alongside_tag_forecast(self):
        context = {
            "current_period": {}, "account_id": "111122223333",
            "tag_allocation": {"tag_key": "Environment", "status": "available",
                                "breakdown": [], "untagged_cost": None, "currency": "USD", "reason": None},
            "forecast": {"status": "available", "forecast_amount": 10.0, "currency": "USD",
                         "period": {}, "prediction_interval_lower": None, "prediction_interval_upper": None,
                         "reason": None},
        }
        prompt = CostPromptBuilder().build_prompt(context)
        self.assertNotIn("111122223333", prompt)


if __name__ == "__main__":
    unittest.main()

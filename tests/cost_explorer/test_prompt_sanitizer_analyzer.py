"""
Tests for llm/cost_prompt_builder.py, llm/cost_sanitizer.py, and
analyzer/cost_analyzer.py - covers spec section 5 (Gemini analysis):
valid response, malformed JSON, missing fields, sanitization behavior,
Gemini failure. LLMEngine.analyze is always stubbed - no real Gemini call.
"""
import json
import unittest
from unittest.mock import MagicMock, patch

from tests.cost_explorer import _helpers as h  # noqa: F401

from llm.cost_sanitizer import CostSanitizer
from llm.cost_prompt_builder import CostPromptBuilder


class TestCostSanitizer(unittest.TestCase):

    def setUp(self):
        self.sanitizer = CostSanitizer()

    def test_drops_sensitive_keys_entirely(self):
        out = self.sanitizer.sanitize({"account_id": "111122223333", "service": "Amazon EC2"})
        self.assertNotIn("account_id", out)
        self.assertEqual(out["service"], "Amazon EC2")

    def test_drops_monitor_arn_key_entirely(self):
        out = self.sanitizer.sanitize({"monitor_arn": "arn:aws:ce::111122223333:anomalymonitor/abc", "service": "x"})
        self.assertNotIn("monitor_arn", out)
        self.assertEqual(out["service"], "x")

    def test_leaves_dollar_amounts_and_dates_untouched(self):
        out = self.sanitizer.sanitize({"net_cost": 123.45, "from": "2026-01-01"})
        self.assertEqual(out["net_cost"], 123.45)
        self.assertEqual(out["from"], "2026-01-01")

    def test_nested_structures_scrubbed_recursively(self):
        out = self.sanitizer.sanitize({"anomalies": [{"root_causes": [{"linked_account": "111122223333", "service": "Amazon EC2"}]}]})
        self.assertNotIn("linked_account", out["anomalies"][0]["root_causes"][0])
        self.assertEqual(out["anomalies"][0]["root_causes"][0]["service"], "Amazon EC2")

    def test_does_not_mutate_input(self):
        original = {"account_id": "111122223333"}
        self.sanitizer.sanitize(original)
        self.assertEqual(original["account_id"], "111122223333")

    def test_twelve_digit_string_value_not_under_sensitive_key_still_redacted(self):
        # Defense-in-depth: value-level regex catches a bare account ID
        # even under an unexpected key name.
        out = self.sanitizer.sanitize({"some_other_field": "111122223333"})
        self.assertEqual(out["some_other_field"], "[redacted]")


class TestCostPromptBuilder(unittest.TestCase):

    def test_prompt_includes_required_schema_keys(self):
        prompt = CostPromptBuilder().build_prompt({"currency": "USD", "current_period": {}, "previous_period": {}})
        for key in ("severity", "summary", "total_cost", "top_cost_drivers", "anomaly_findings",
                    "root_cause", "evidence", "recommendations", "comparison_analysis"):
            self.assertIn(f'"{key}"', prompt)

    def test_prompt_sanitizes_context_before_embedding(self):
        prompt = CostPromptBuilder().build_prompt({"account_id": "111122223333", "current_period": {}})
        self.assertNotIn("111122223333", prompt)
        self.assertNotIn('"account_id"', prompt)

    def test_prompt_redacts_bare_account_id_value_under_any_key(self):
        prompt = CostPromptBuilder().build_prompt({"some_field": "111122223333", "current_period": {}})
        self.assertNotIn("111122223333", prompt)
        self.assertIn("[redacted]", prompt)

    def test_prompt_embeds_real_context_values(self):
        prompt = CostPromptBuilder().build_prompt({"currency": "USD", "current_period": {"net_cost": 42.5}})
        self.assertIn("42.5", prompt)


class TestCostAnalyzer(unittest.TestCase):
    """LLMEngine.__init__ requires GEMINI_API_KEY - patched out at the
    class level (same convention used throughout this repo's other test
    suites) so constructing CostAnalyzer never needs a real API key or
    makes any real Gemini call."""

    def _build_analyzer_with_stubbed_deps(self, gemini_response, gemini_side_effect=None):
        from analyzer.cost_analyzer import CostAnalyzer
        from llm.llm_engine import LLMEngine
        with patch.object(LLMEngine, "__init__", lambda self: None):
            analyzer = CostAnalyzer()
        analyzer.builder = MagicMock()
        analyzer.builder.run.return_value = {"currency": "USD", "current_period": {}, "previous_period": {}}
        analyzer.llm = MagicMock()
        if gemini_side_effect is not None:
            analyzer.llm.analyze.side_effect = gemini_side_effect
        else:
            analyzer.llm.analyze.return_value = gemini_response
        analyzer.report = MagicMock()
        return analyzer

    def test_valid_json_response_parsed_and_saved(self):
        valid = json.dumps({"severity": "LOW", "summary": "stable", "total_cost": 10.0})
        analyzer = self._build_analyzer_with_stubbed_deps(valid)
        report = analyzer.run()
        self.assertEqual(report["severity"], "LOW")
        analyzer.report.save.assert_called_once_with(report)

    def test_malformed_json_falls_back_to_raw_response(self):
        analyzer = self._build_analyzer_with_stubbed_deps("not valid json {{{")
        report = analyzer.run()
        self.assertIn("raw_response", report)
        self.assertEqual(report["raw_response"], "not valid json {{{")

    def test_missing_fields_in_valid_json_still_saved_as_is(self):
        partial = json.dumps({"severity": "LOW"})
        analyzer = self._build_analyzer_with_stubbed_deps(partial)
        report = analyzer.run()
        self.assertEqual(report, {"severity": "LOW"})

    def test_gemini_failure_propagates_not_swallowed(self):
        analyzer = self._build_analyzer_with_stubbed_deps(None, gemini_side_effect=TimeoutError("gemini timed out"))
        with self.assertRaises(TimeoutError):
            analyzer.run()
        analyzer.report.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()

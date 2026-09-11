"""
Tests for the async refresh machinery itself (Phase 2 / A4):
api/cost_explorer_manager.py's CostExplorerManager (run_id/thread/status),
and the additive on_progress hooks threaded into
collector/cost_explorer.py::main() and analyzer/cost_analyzer.py::
CostAnalyzer.run(). Covers end-to-end CASE 2 from the spec: start_refresh
-> run_id -> polling -> real progress stages -> COMPLETED with report.
"""
import json
import time
import unittest
from unittest.mock import MagicMock, patch

from tests.cost_explorer import _helpers as h  # noqa: F401

import collector.cost_explorer as ce_mod
from api.cost_explorer_manager import CostExplorerBusyError, CostExplorerManager, PHASES
from llm.llm_engine import LLMEngine


def _wait_for_terminal(manager, run_id, timeout=5.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = manager.get_status(run_id)
        if last and last["status"] in ("COMPLETED", "FAILED"):
            return last
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} never reached a terminal state; last={last}")


class TestCostExplorerManagerAsync(unittest.TestCase):

    def setUp(self):
        self.manager = CostExplorerManager()
        self._orig_ce = ce_mod.ce
        ce_mod.ce = MagicMock()
        ce_mod.ce.get_cost_and_usage.return_value = {"ResultsByTime": []}
        ce_mod.ce.get_anomaly_monitors.return_value = {"AnomalyMonitors": []}
        self._llm_init_patch = patch.object(LLMEngine, "__init__", lambda self: None)
        self._llm_analyze_patch = patch.object(LLMEngine, "analyze", lambda self, prompt: json.dumps({"severity": "LOW"}))
        self._llm_init_patch.start()
        self._llm_analyze_patch.start()
        self._export_patch = patch("api.cost_explorer_manager.export_cost_feed")
        self._export_patch.start()

    def tearDown(self):
        ce_mod.ce = self._orig_ce
        self._llm_init_patch.stop()
        self._llm_analyze_patch.stop()
        self._export_patch.stop()

    def test_start_refresh_returns_immediately_with_run_id(self):
        started = time.time()
        result = self.manager.start_refresh()
        elapsed = time.time() - started
        self.assertIn("run_id", result)
        self.assertIn(result["status"], ("QUEUED", "RUNNING"))
        self.assertLess(elapsed, 0.5, "start_refresh must return immediately, not block on the pipeline")

    def test_full_lifecycle_reaches_completed_with_report(self):
        result = self.manager.start_refresh()
        final = _wait_for_terminal(self.manager, result["run_id"])
        self.assertEqual(final["status"], "COMPLETED")
        self.assertEqual(final["percent"], 100)
        self.assertEqual(final["report"]["severity"], "LOW")
        self.assertTrue(all(p["state"] == "done" for p in final["phases"]))

    def test_progress_passes_through_real_phase_keys_in_order(self):
        seen_phases = []
        orig_callback_factory = self.manager._make_progress_callback

        def spying_factory(run_id):
            cb = orig_callback_factory(run_id)

            def spy(phase_key):
                seen_phases.append(phase_key)
                cb(phase_key)

            return spy

        self.manager._make_progress_callback = spying_factory
        result = self.manager.start_refresh()
        _wait_for_terminal(self.manager, result["run_id"])

        expected_order = [key for key, _label in PHASES]
        self.assertEqual(seen_phases, expected_order)

    def test_get_status_unknown_run_id_returns_none(self):
        self.assertIsNone(self.manager.get_status("does-not-exist"))

    def test_concurrent_refresh_raises_busy_error(self):
        self.manager._busy = True
        try:
            with self.assertRaises(CostExplorerBusyError):
                self.manager.start_refresh()
        finally:
            self.manager._busy = False

    def test_busy_flag_cleared_after_completion_allows_next_refresh(self):
        first = self.manager.start_refresh()
        _wait_for_terminal(self.manager, first["run_id"])
        # Must not raise CostExplorerBusyError - proves _busy was cleared
        # by _finish() after the first run reached a terminal state.
        second = self.manager.start_refresh()
        _wait_for_terminal(self.manager, second["run_id"])

    def test_single_metric_aws_failure_degrades_gracefully_to_completed(self):
        # Every individual collector fetch function catches its own AWS
        # exceptions and returns None/[] (see collector/cost_explorer.py) -
        # so a single metric failing never crashes the whole async run;
        # the refresh still completes honestly with that one figure
        # missing, exactly like the pipeline's own documented behavior.
        ce_mod.ce.get_cost_and_usage.side_effect = Exception("cost data unavailable")
        result = self.manager.start_refresh()
        final = _wait_for_terminal(self.manager, result["run_id"])
        self.assertEqual(final["status"], "COMPLETED")
        self.assertFalse(self.manager._busy)

    def test_elapsed_seconds_present_in_status(self):
        result = self.manager.start_refresh()
        status = self.manager.get_status(result["run_id"])
        self.assertIn("elapsed_seconds", status)
        self.assertGreaterEqual(status["elapsed_seconds"], 0)


class TestCollectorOnProgressHook(unittest.TestCase):
    """Additive on_progress param on collector/cost_explorer.py::main()."""

    def setUp(self):
        self._orig_ce = ce_mod.ce
        ce_mod.ce = MagicMock()
        ce_mod.ce.get_cost_and_usage.return_value = {"ResultsByTime": []}
        ce_mod.ce.get_anomaly_monitors.return_value = {"AnomalyMonitors": []}

    def tearDown(self):
        ce_mod.ce = self._orig_ce

    def test_on_progress_called_with_real_phase_keys_in_order(self):
        calls = []
        ce_mod.main(on_progress=lambda phase: calls.append(phase))
        self.assertEqual(calls, ["COLLECTING_COST_DATA", "COLLECTING_ANOMALY_DATA"])

    def test_omitted_on_progress_behaves_exactly_as_before(self):
        # Must not raise when on_progress is not supplied at all -
        # backward compatible with every existing caller.
        ce_mod.main()


class TestCostAnalyzerOnProgressHook(unittest.TestCase):
    """Additive on_progress param on analyzer/cost_analyzer.py::CostAnalyzer.run()."""

    def test_on_progress_called_with_real_phase_keys_in_order(self):
        from analyzer.cost_analyzer import CostAnalyzer
        with patch.object(LLMEngine, "__init__", lambda self: None):
            analyzer = CostAnalyzer()
        analyzer.builder = MagicMock()
        analyzer.builder.run.return_value = {}
        analyzer.llm = MagicMock()
        analyzer.llm.analyze.return_value = json.dumps({})
        analyzer.report = MagicMock()

        calls = []
        analyzer.run(on_progress=lambda phase: calls.append(phase))
        self.assertEqual(calls, ["BUILDING_CONTEXT", "RUNNING_AI_ANALYSIS", "PERSISTING_REPORT"])

    def test_omitted_on_progress_behaves_exactly_as_before(self):
        from analyzer.cost_analyzer import CostAnalyzer
        with patch.object(LLMEngine, "__init__", lambda self: None):
            analyzer = CostAnalyzer()
        analyzer.builder = MagicMock()
        analyzer.builder.run.return_value = {}
        analyzer.llm = MagicMock()
        analyzer.llm.analyze.return_value = json.dumps({"severity": "LOW"})
        analyzer.report = MagicMock()

        report = analyzer.run()
        self.assertEqual(report["severity"], "LOW")


if __name__ == "__main__":
    unittest.main()

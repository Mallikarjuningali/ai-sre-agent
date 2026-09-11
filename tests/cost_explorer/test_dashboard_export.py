"""
Tests for utils/cost_dashboard_export.py - report persistence, dashboard
export, missing report handling. Covers spec section 7. Uses real
temp-dir-backed file I/O (atomic_write_json) rather than mocking the
filesystem, since this module's whole job IS file I/O.
"""
import json
import shutil
import unittest
from pathlib import Path

from tests.cost_explorer import _helpers as h  # noqa: F401

import utils.cost_dashboard_export as export_mod


class TestCostDashboardExport(unittest.TestCase):

    def setUp(self):
        self._orig_output = export_mod.OUTPUT_DIR
        self._orig_context = export_mod.CONTEXT_DIR
        self._orig_reports = export_mod.REPORTS_DIR
        self._orig_feed = export_mod.FEED_DIR

        self.tmp_dir = Path("output/cost/_test_scratch")
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        export_mod.OUTPUT_DIR = self.tmp_dir
        export_mod.CONTEXT_DIR = self.tmp_dir / "context"
        export_mod.REPORTS_DIR = self.tmp_dir / "reports"
        export_mod.FEED_DIR = self.tmp_dir / "dashboard_feed"

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        export_mod.OUTPUT_DIR = self._orig_output
        export_mod.CONTEXT_DIR = self._orig_context
        export_mod.REPORTS_DIR = self._orig_reports
        export_mod.FEED_DIR = self._orig_feed

    def _write_context(self, context):
        export_mod.CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
        with open(export_mod.CONTEXT_DIR / "cost_context.json", "w") as f:
            json.dump(context, f)

    def _write_report(self, report):
        export_mod.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        with open(export_mod.REPORTS_DIR / "cost_report.json", "w") as f:
            json.dump(report, f)

    def test_missing_context_and_report_produce_empty_but_valid_feeds(self):
        export_mod.export()
        summary = json.loads((export_mod.FEED_DIR / "summary.json").read_text())
        self.assertIsNone(summary["currency"])
        report = json.loads((export_mod.FEED_DIR / "report.json").read_text())
        self.assertEqual(report, {})

    def test_report_persistence_round_trips_through_feed(self):
        self._write_context({"currency": "USD"})
        self._write_report({"severity": "HIGH", "summary": "cost spike"})
        export_mod.export()
        report = json.loads((export_mod.FEED_DIR / "report.json").read_text())
        self.assertEqual(report["severity"], "HIGH")

    def test_all_eight_feed_files_written(self):
        self._write_context({"currency": "USD"})
        export_mod.export()
        for filename in ("summary.json", "history.json", "credits.json", "services.json",
                          "regions.json", "anomalies.json", "comparison.json", "report.json"):
            self.assertTrue((export_mod.FEED_DIR / filename).exists(), f"{filename} missing")

    def test_comparison_null_when_not_requested(self):
        self._write_context({"currency": "USD", "comparison": None})
        export_mod.export()
        content = (export_mod.FEED_DIR / "comparison.json").read_text()
        self.assertEqual(json.loads(content), None)

    def test_credits_by_service_filters_zero_credit_entries(self):
        context = {
            "currency": "USD",
            "current_period": {
                "credits": {"total": -5.0, "currency": "USD", "history": []},
                "service_breakdown": [
                    {"service": "Amazon EC2", "credits": -5.0, "gross_cost": 10, "net_cost": 5, "currency": "USD"},
                    {"service": "Amazon S3", "credits": 0, "gross_cost": 2, "net_cost": 2, "currency": "USD"},
                ],
                "region_breakdown": [],
            },
        }
        self._write_context(context)
        export_mod.export()
        credits = json.loads((export_mod.FEED_DIR / "credits.json").read_text())
        names = [c["service"] for c in credits["by_service"]]
        self.assertEqual(names, ["Amazon EC2"])
        self.assertFalse(credits["resource_level_attribution_available"])

    def test_atomic_write_leaves_no_tmp_file_behind(self):
        export_mod.atomic_write_json(export_mod.FEED_DIR / "x.json", {"a": 1})
        self.assertTrue((export_mod.FEED_DIR / "x.json").exists())
        self.assertFalse((export_mod.FEED_DIR / "x.json.tmp").exists())

    def test_load_context_malformed_json_returns_empty_dict(self):
        export_mod.CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
        (export_mod.CONTEXT_DIR / "cost_context.json").write_text("{not valid json")
        self.assertEqual(export_mod.load_context(), {})


if __name__ == "__main__":
    unittest.main()

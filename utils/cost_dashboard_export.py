"""
=========================================================
AI SRE AGENT
Module : Cost Dashboard Export
Purpose:
    Bridge between the Cost Explorer pipeline's output/cost/ tree and the
    AegisOps AI dashboard's Cost Explorer page. Completely separate from
    utils/dashboard_export.py (not imported, not modified) - reads only
    output/cost/context/ and output/cost/reports/, writes only
    output/cost/dashboard_feed/.

    Eight feed files, one per dashboard concern, mirroring the existing
    infra dashboard's "one JSON file per concern" convention:

        summary.json    - current/previous cost, change, credits_total,
                           gross_cost, currency, period (all additive -
                           the original fields are unchanged)
        history.json    - daily cost datapoints
        credits.json    - AWS credit total/history (RECORD_TYPE=Credit)
        services.json   - cost grouped by AWS service
        regions.json    - cost grouped by AWS region
        anomalies.json  - AWS Cost Anomaly Detection findings/status
        comparison.json - the user-selected Month/Period Comparison
                           (period_a/period_b/service_comparison/
                           region_comparison/absolute_difference/
                           change_percent), or null when no comparison
                           has been requested yet
        report.json     - the Gemini cost-analysis report (includes an
                           additive comparison_analysis field when a
                           comparison was part of this run)

    Safe to run standalone with no AWS/Gemini calls and no side effects:

        python -m utils.cost_dashboard_export
=========================================================
"""

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

OUTPUT_DIR = Path("output/cost")
CONTEXT_DIR = OUTPUT_DIR / "context"
REPORTS_DIR = OUTPUT_DIR / "reports"
FEED_DIR = OUTPUT_DIR / "dashboard_feed"


def read_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def atomic_write_json(path: Path, obj):
    """Write-to-temp + os.replace() so the dashboard's poller never
    observes a half-written file - same technique used by
    utils/dashboard_export.py, duplicated here rather than imported to
    keep this module fully self-contained."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp_path, path)


def load_context() -> dict:
    return read_json(CONTEXT_DIR / "cost_context.json") or {}


def load_report() -> dict:
    return read_json(REPORTS_DIR / "cost_report.json") or {}


def build_summary(context: dict, generated_at: str) -> dict:
    credits = context.get("credits") or {}
    return {
        "total_cost": context.get("total_cost"),
        "previous_cost": context.get("previous_cost"),
        "change_percent": context.get("change_percent"),
        "currency": context.get("currency"),
        "period": context.get("period") or {},
        "generated_at": generated_at,
        # Additive - existing fields above are byte-for-byte unchanged.
        "credits_total": credits.get("total"),
        "gross_cost": context.get("gross_cost"),
    }


def build_history(context: dict) -> dict:
    return {
        "daily_history": context.get("daily_history") or [],
        "currency": context.get("currency"),
    }


def build_credits(context: dict) -> dict:
    credits = context.get("credits") or {}
    return {
        "total": credits.get("total"),
        "currency": credits.get("currency"),
        "history": credits.get("history") or [],
    }


def build_services(context: dict) -> dict:
    return {
        "service_breakdown": context.get("service_breakdown") or [],
        "currency": context.get("currency"),
    }


def build_regions(context: dict) -> dict:
    return {
        "region_breakdown": context.get("region_breakdown") or [],
        "currency": context.get("currency"),
    }


def build_anomalies(context: dict) -> dict:
    return context.get("anomalies") or {"status": "unavailable", "reason": "No cost context available", "anomalies": []}


def build_comparison(context: dict):
    """None when no Month/Period Comparison has been requested yet - a
    valid, honest "not run" state (see the dashboard's empty_state for
    this), never a fabricated comparison."""
    return context.get("comparison")


def build_report(report: dict) -> dict:
    return report


def export() -> None:
    FEED_DIR.mkdir(parents=True, exist_ok=True)

    context = load_context()
    report = load_report()
    generated_at = datetime.now(IST).isoformat()

    atomic_write_json(FEED_DIR / "summary.json", build_summary(context, generated_at))
    atomic_write_json(FEED_DIR / "history.json", build_history(context))
    atomic_write_json(FEED_DIR / "credits.json", build_credits(context))
    atomic_write_json(FEED_DIR / "services.json", build_services(context))
    atomic_write_json(FEED_DIR / "regions.json", build_regions(context))
    atomic_write_json(FEED_DIR / "anomalies.json", build_anomalies(context))
    atomic_write_json(FEED_DIR / "comparison.json", build_comparison(context))
    atomic_write_json(FEED_DIR / "report.json", build_report(report))


if __name__ == "__main__":
    export()

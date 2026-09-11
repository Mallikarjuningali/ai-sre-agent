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

    Ten feed files, one per dashboard concern, mirroring the existing
    infra dashboard's "one JSON file per concern" convention. All built
    from context/cost_context_builder.py's current_period/previous_period/
    change/service_comparison/region_comparison/anomalies/tag_allocation/
    forecast/comparison shape - see that module's docstring for the full
    data model.

        summary.json    - current_period (gross/credits/net), previous_period,
                           change, currency, period, generated_at
        history.json    - current period's daily cost datapoints
        credits.json    - current period's credit total/history, PLUS
                           service/region-level credit attribution
                           (only entries where a real credit exists) and
                           an explicit note that resource-level
                           attribution is not available from AWS
        services.json   - current period's service breakdown
                           (gross/credits/net per service) plus
                           service_comparison (vs previous_period, for a
                           "Change" column)
        regions.json    - same as services.json, grouped by region
        anomalies.json  - AWS Cost Anomaly Detection findings/status for
                           the current period
        tags.json       - the user-requested tag-based cost allocation
                           (tag_key/status/reason/breakdown/untagged_cost),
                           or null when no tag key has been requested yet
        forecast.json   - AWS's own GetCostForecast projection for the
                           remainder of the current calendar month
                           (status/reason/forecast_amount/period/
                           prediction interval), always present once a
                           refresh has run under this feature
        comparison.json - the user-selected Month/Period Comparison
                           (selected_period/comparison_period/difference/
                           percentage_change/service_comparison/
                           region_comparison/credit_comparison/
                           anomaly_comparison), or null when no
                           comparison has been requested yet
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

# AWS Cost Explorer's own, real limitation - stated once here, echoed
# verbatim into every feed file where resource-level attribution might
# otherwise be implied, rather than fabricated or silently omitted. See
# collector/cost_explorer.py's get_service_credit_breakdown() docstring
# for the full technical reasoning.
RESOURCE_LEVEL_CREDIT_NOTE = (
    "AWS Cost Explorer reports these credits at the service/region level. "
    "Individual resource attribution is not available from the current billing data."
)

_EMPTY_PERIOD = {
    "from": None, "to": None, "currency": None, "gross_cost": None,
    "credits": {"total": None, "currency": None, "history": []},
    "net_cost": None, "daily_history": [], "service_breakdown": [], "region_breakdown": [],
}


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
    current_period = context.get("current_period") or dict(_EMPTY_PERIOD)
    previous_period = context.get("previous_period") or dict(_EMPTY_PERIOD)
    change = context.get("change") or {}

    return {
        "currency": context.get("currency"),
        "period": context.get("period") or {},
        "generated_at": generated_at,
        "current_period": current_period,
        "previous_period": previous_period,
        "change": change,
    }


def build_history(context: dict) -> dict:
    current_period = context.get("current_period") or {}
    return {
        "daily_history": current_period.get("daily_history") or [],
        "currency": current_period.get("currency") or context.get("currency"),
    }


def _credit_entries_only(breakdown: list, label_key: str) -> list:
    """Filters a merged gross/credits/net breakdown down to entries that
    actually had a nonzero credit - the Credits section shows "which
    services/regions had credits applied," not the entire cost
    breakdown (that's what services.json/regions.json are for)."""
    return [item for item in (breakdown or []) if item.get(label_key) and item.get("credits")]


def build_credits(context: dict) -> dict:
    current_period = context.get("current_period") or {}
    credits = current_period.get("credits") or {"total": None, "currency": None, "history": []}

    return {
        "total": credits.get("total"),
        "currency": credits.get("currency") or current_period.get("currency"),
        "history": credits.get("history") or [],
        "by_service": _credit_entries_only(current_period.get("service_breakdown"), "service"),
        "by_region": _credit_entries_only(current_period.get("region_breakdown"), "region"),
        "resource_level_attribution_available": False,
        "resource_level_attribution_note": RESOURCE_LEVEL_CREDIT_NOTE,
    }


def build_services(context: dict) -> dict:
    current_period = context.get("current_period") or {}
    return {
        "service_breakdown": current_period.get("service_breakdown") or [],
        "service_comparison": context.get("service_comparison") or [],
        "currency": current_period.get("currency") or context.get("currency"),
    }


def build_regions(context: dict) -> dict:
    current_period = context.get("current_period") or {}
    return {
        "region_breakdown": current_period.get("region_breakdown") or [],
        "region_comparison": context.get("region_comparison") or [],
        "currency": current_period.get("currency") or context.get("currency"),
        "region_credit_note": RESOURCE_LEVEL_CREDIT_NOTE,
    }


def build_anomalies(context: dict) -> dict:
    return context.get("anomalies") or {
        "status": "unavailable", "reason": "No cost context available", "anomalies": [],
        "requested_start": None, "requested_end": None,
        "analyzed_start": None, "analyzed_end": None,
        "supported_from": None, "supported": None, "partial": False,
    }


def build_forecast(context: dict):
    """None only when the published context predates this feature - a
    genuine forecast attempt (even a failed one) always carries a real
    status once this feature is live, never silently omitted."""
    return context.get("forecast")


def build_tags(context: dict):
    """None when no tag key has been requested yet - a valid, honest
    "not run" state (mirrors build_comparison's own None convention),
    never a fabricated allocation. See
    context/cost_context_builder.py::_build_tag_allocation for the real
    AWS-derived shape when a tag key WAS requested."""
    return context.get("tag_allocation")


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
    atomic_write_json(FEED_DIR / "tags.json", build_tags(context))
    atomic_write_json(FEED_DIR / "forecast.json", build_forecast(context))
    atomic_write_json(FEED_DIR / "comparison.json", build_comparison(context))
    atomic_write_json(FEED_DIR / "report.json", build_report(report))


if __name__ == "__main__":
    export()

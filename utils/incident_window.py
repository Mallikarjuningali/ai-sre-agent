"""
=========================================================
AI SRE AGENT
Module : Incident Window
Purpose:
    Deterministic (non-interpretive) incident-time/window derivation for
    the optional Log Investigation feature (see
    api/log_investigation_manager.py). Used only to decide WHICH bounded
    time range to fetch logs for - it never decides or influences a root
    cause. Every value here is read directly off already-collected
    MetricTrends {U, TH, H} facts (min()/comparison over real data), the
    same "mechanical extraction only" style
    llm/follow_up_prompt_builder.py::_metric_extremes already established
    for a different feature - no "if metric > X then <RCA conclusion>"
    branch exists anywhere in this file.

    incident_time is a SINGLE timestamp, never a range: the EARLIEST
    point, across every metric with a configured CloudWatch Alarm
    threshold, where that metric's own operator first breaches it - i.e.
    the moment the existing metric investigation's own data shows the
    incident beginning. This is the one deterministic, documented method
    used to pick a single point out of however many breaching samples
    exist; see derive_incident_time() below. The bounded log-investigation
    window is then exactly incident_time +/- LOG_INCIDENT_WINDOW_BEFORE/
    AFTER_MINUTES - never "earliest breach - buffer" through "latest
    breach + buffer" (a previous, incorrect implementation of this same
    idea, which could balloon the window far past any real buffer).

    Operates on the RAW (pre-sanitize) per-resource context
    (output/context/<resource_id>.json) - this runs before any AWS log
    call is made, purely to size that call's time window, so it needs the
    real MetricTrends shapes exactly as context/context_builder.py wrote
    them (EC2's nested context["context"]["cloudwatch"]["MetricTrends"],
    or a first-class Load Balancer/Auto Scaling Group's own top-level
    context["context"]["MetricTrends"]) - the same two shapes
    llm/sanitizer.py's own sanitize_cloudwatch/sanitize_load_balancer/
    sanitize_auto_scaling_group already read from, by the same key names.
=========================================================
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Dict, Optional

from config.settings import (
    LOG_INCIDENT_WINDOW_BEFORE_MINUTES,
    LOG_INCIDENT_WINDOW_AFTER_MINUTES,
    LOG_FALLBACK_WINDOW_MINUTES,
)

IST = ZoneInfo("Asia/Kolkata")

_OPERATORS = {
    "GT": lambda value, threshold: value > threshold,
    "GTE": lambda value, threshold: value >= threshold,
    "LT": lambda value, threshold: value < threshold,
    "LTE": lambda value, threshold: value <= threshold,
    "EQ": lambda value, threshold: value == threshold,
}


def _candidate_trends(raw_context: Dict[str, Any]) -> Dict[str, Any]:
    """Every trend-shaped ({"H": [...]} present) metric in the raw
    context, regardless of resource type - EC2's nested
    cloudwatch.MetricTrends, or a first-class Load Balancer/Auto Scaling
    Group's own top-level MetricTrends. Same generic-scan philosophy as
    llm/follow_up_prompt_builder.py::_metric_timeline, adapted to the raw
    (not sanitized) key names since this runs before sanitization."""

    data = raw_context.get("context") or {}
    candidates: Dict[str, Any] = {}

    cloudwatch = data.get("cloudwatch")
    if isinstance(cloudwatch, dict):
        trends = cloudwatch.get("MetricTrends") or {}
        candidates.update({k: v for k, v in trends.items() if isinstance(v, dict) and "H" in v})

    top_level_trends = data.get("MetricTrends")
    if isinstance(top_level_trends, dict):
        candidates.update({k: v for k, v in top_level_trends.items() if isinstance(v, dict) and "H" in v})

    return candidates


def _parse_trend_timestamp(hhmm: str, reference_now: datetime) -> Optional[datetime]:
    """MetricTrends' H points are "HH:MM" strings in IST with no date
    component (see utils/metric_stats.py) - the trend window is only the
    last METRIC_TREND_LOOKBACK_MINUTES (60), so combining the time-of-day
    with "today" (IST) is safe except right at a midnight boundary; if the
    combined timestamp would land in the future relative to now, it must
    actually belong to yesterday, so one day is subtracted. This is a
    mechanical date-recovery step, not an inference about the incident."""

    try:
        hour, minute = (int(part) for part in hhmm.split(":"))
    except (ValueError, AttributeError):
        return None

    candidate = reference_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate > reference_now:
        candidate -= timedelta(days=1)

    return candidate


def derive_incident_time(raw_context: Dict[str, Any], reference_now: Optional[datetime] = None) -> Optional[datetime]:
    """Returns a SINGLE datetime - the EARLIEST point, across every trend-
    shaped metric with a configured threshold (TH), where that metric's
    own operator (GT/GTE/LT/LTE/EQ) against its own threshold value is
    first satisfied - i.e. when the incident, per the existing metric
    investigation's own data, began. Returns None if no metric in this
    context has both a threshold and a breaching point, meaning there is
    no confident incident_time to derive.

    Deterministic method, documented: when multiple metrics/points breach
    at different times, the EARLIEST one wins - this is "when the
    incident started," the standard SRE definition of incident time, and
    it is picked the same way every time this function runs (never a
    tie-break that could vary between calls). Purely min() over real,
    already-collected facts - never a metric recollection, never an
    invented timestamp."""

    reference_now = reference_now or datetime.now(IST)
    breach_timestamps = []

    for trend in _candidate_trends(raw_context).values():

        threshold = trend.get("TH")
        if not threshold:
            continue

        operator_fn = _OPERATORS.get(threshold.get("OP"))
        threshold_value = threshold.get("V")
        if operator_fn is None or threshold_value is None:
            continue

        for point in trend.get("H") or []:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue
            timestamp_label, value = point
            if value is None or not operator_fn(value, threshold_value):
                continue
            parsed = _parse_trend_timestamp(timestamp_label, reference_now)
            if parsed is not None:
                breach_timestamps.append(parsed)

    if not breach_timestamps:
        return None

    return min(breach_timestamps)


def resolve_analysis_window(raw_context: Dict[str, Any], reference_now: Optional[datetime] = None) -> Dict[str, Any]:
    """The final bounded window handed to collector/logs.py:
    incident_time +/- LOG_INCIDENT_WINDOW_BEFORE/AFTER_MINUTES (a single
    anchor point expanded symmetrically - NEVER "earliest breach - buffer"
    through "latest breach + buffer", which could balloon far past the
    configured buffer when breaches are spread across time), or - when no
    metric confidently establishes an incident_time - a small, clearly-
    labeled fallback window centered on "now". confidence is always
    carried through so neither the prompt nor the dashboard ever mistakes
    an inferred window for a confidently derived one.

    Returns {"incident_time": datetime|None, "start": datetime,
    "end": datetime, "confidence": "derived"|"inferred"}."""

    reference_now = reference_now or datetime.now(IST)

    incident_time = derive_incident_time(raw_context, reference_now=reference_now)

    if incident_time is not None:
        return {
            "incident_time": incident_time,
            "start": incident_time - timedelta(minutes=LOG_INCIDENT_WINDOW_BEFORE_MINUTES),
            "end": incident_time + timedelta(minutes=LOG_INCIDENT_WINDOW_AFTER_MINUTES),
            "confidence": "derived",
        }

    half_window = timedelta(minutes=LOG_FALLBACK_WINDOW_MINUTES / 2)
    return {
        "incident_time": None,
        "start": reference_now - half_window,
        "end": reference_now + half_window,
        "confidence": "inferred",
    }

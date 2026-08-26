"""
=========================================================
AI SRE AGENT
Module : Cost Explorer Collector
Purpose:
    Collect real AWS Cost Explorer data (total cost, daily history,
    service/region breakdown, cost anomaly findings) for a single AWS
    account. Fully independent from the infra collectors
    (cloudwatch/alb/autoscaling/cloudtrail) - separate AWS API surface
    (Cost Explorer's `ce` client), separate output directory
    (output/cost/raw/), separate everything downstream.

    No value here is ever hardcoded or invented - every cost, service
    name, region, and anomaly value is whatever AWS Cost Explorer
    actually returned. Missing/unavailable data is represented as such
    (None / empty list / explicit status), never guessed.
=========================================================
"""

from datetime import date, datetime, timedelta, UTC

from utils.cost_writer import write_json
from utils.aws_clients import get_ce_client
from utils.logger import get_logger
from config.settings import COST_LOOKBACK_DAYS

logger = get_logger(__name__)

ce = get_ce_client()


# =========================================================
# Period bounds
# =========================================================

def _iso_date(d):
    return d.strftime("%Y-%m-%d")


def _error_code(exc):
    """The AWS error code (e.g. "ValidationException", "AccessDeniedException")
    from a botocore ClientError, or None for anything else (a plain
    exception, a network error, ...). Used to distinguish a legitimate,
    expected AWS rejection from a real system failure - never guessed
    when the exception doesn't actually carry this information."""

    response = getattr(exc, "response", None)

    if not isinstance(response, dict):
        return None

    return (response.get("Error") or {}).get("Code")


def _is_complete_calendar_month(period_start, period_end):
    """True when [period_start, period_end) spans exactly one full
    calendar month: period_start is the 1st of a month, and period_end
    (exclusive) is the 1st of the following month. Pure date arithmetic
    via Python's date type - no hardcoded month names or day counts, so
    it's automatically correct for 28/29/30/31-day months and leap
    years."""

    if period_start.day != 1:
        return False

    if period_start.month == 12:
        next_month_first = date(period_start.year + 1, 1, 1)
    else:
        next_month_first = date(period_start.year, period_start.month + 1, 1)

    return period_end == next_month_first


def _comparison_period(period_start, period_end):
    """Given any period [period_start, period_end) (end-exclusive),
    returns the comparison period to use - the single shared calculation
    used both by the default current-vs-previous comparison below and by
    the user-selected Month/Period Comparison feature. No duplicate
    date-calculation logic exists anywhere else in this module.

    - If the selected period is a COMPLETE calendar month, the
      comparison is the immediately preceding calendar month, whatever
      its actual length is (28/29/30/31 days) - determined purely from
      the real calendar, never a hardcoded month length or name.
    - Otherwise, the comparison is the immediately preceding period of
      the exact same duration (in days) as the selection - the original
      rule, unchanged for every non-full-month case."""

    duration = (period_end - period_start).days

    if duration <= 0:
        raise ValueError("period_end must be after period_start")

    if _is_complete_calendar_month(period_start, period_end):

        if period_start.month == 1:
            comparison_start = date(period_start.year - 1, 12, 1)
        else:
            comparison_start = date(period_start.year, period_start.month - 1, 1)

        return comparison_start, period_start

    comparison_end = period_start
    comparison_start = period_start - timedelta(days=duration)

    return comparison_start, comparison_end


def _period_bounds():
    """Current period: the last COST_LOOKBACK_DAYS days, ending today.
    Previous period: the equal-length window immediately before it, via
    the shared _comparison_period() helper - both windows entirely
    driven by the one COST_LOOKBACK_DAYS constant, no calendar-month
    assumptions. Cost Explorer's End date is exclusive, so current_end
    is "tomorrow" to include all of "today"."""

    today = datetime.now(UTC).date()

    current_end = today + timedelta(days=1)
    current_start = today - timedelta(days=COST_LOOKBACK_DAYS - 1)

    previous_start, previous_end = _comparison_period(current_start, current_end)

    return {
        "current_start": current_start,
        "current_end": current_end,
        "previous_start": previous_start,
        "previous_end": previous_end,
    }


# =========================================================
# Total cost
# =========================================================

def get_total_cost(start_date, end_date):
    """Real AWS total cost for [start_date, end_date) via
    GetCostAndUsage. Sums across every ResultsByTime entry (MONTHLY
    granularity can still return more than one row when the window
    crosses a calendar month boundary) rather than assuming a single
    result. Returns (amount: float | None, currency: str | None) -
    None/None when there's no data, never a fabricated 0."""

    try:
        response = ce.get_cost_and_usage(
            TimePeriod={"Start": _iso_date(start_date), "End": _iso_date(end_date)},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
        )

        results = response.get("ResultsByTime") or []

        total_amount = 0.0
        currency = None
        found_any = False

        for result in results:

            total = result.get("Total") or {}
            cost = total.get("UnblendedCost") or {}
            amount = cost.get("Amount")

            if amount is None:
                continue

            found_any = True
            total_amount += float(amount)
            currency = cost.get("Unit") or currency

        if not found_any:
            return None, None

        rounded = round(total_amount, 2)

        # AWS can legitimately return a net cost that rounds to a tiny
        # negative float (e.g. credits/discounts nearly offsetting usage,
        # or floating-point residue like "-1.08e-19" in the raw Amount).
        # -0.0 == 0.0 in Python, so this only ever normalizes a
        # genuinely-zero-or-negligible result - it never changes a real
        # non-zero total, positive or negative.
        if rounded == 0:
            rounded = 0.0

        return rounded, currency

    except Exception as exc:

        logger.error(f"get_total_cost failed: {exc}")

        return None, None


# =========================================================
# Daily cost history
# =========================================================

def get_daily_history(start_date, end_date):
    """Real AWS daily cost datapoints for [start_date, end_date) via
    GetCostAndUsage, DAILY granularity. Returns [date_label, value] pairs,
    oldest -> newest - the raw sequence only, no trend/anomaly
    classification happens here.

    date_label is the full "YYYY-MM-DD" AWS gave us in TimePeriod.Start -
    not a "DD-MM"-style abbreviation. A year-less, ambiguous label like
    "18-08" is exactly the kind of string a chart library's date-axis
    auto-detection can silently misparse; an unambiguous ISO date can't
    be. Friendly display formatting (e.g. "Aug 18") happens only at
    chart-render time in the dashboard, never here - this stays the
    machine-safe, sortable value."""

    try:
        response = ce.get_cost_and_usage(
            TimePeriod={"Start": _iso_date(start_date), "End": _iso_date(end_date)},
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
        )

        results = response.get("ResultsByTime") or []

        history = []

        for result in results:

            period_start = (result.get("TimePeriod") or {}).get("Start")

            if not period_start:
                continue

            total = result.get("Total") or {}
            cost = total.get("UnblendedCost") or {}
            amount = cost.get("Amount")

            if amount is None:
                continue

            history.append([period_start, round(float(amount), 2)])

        # AWS's own documented ordering for ResultsByTime is already
        # chronological, but an explicit sort on the (now unambiguous,
        # lexicographically-sortable) ISO date guarantees oldest -> newest
        # regardless, at negligible cost for a 14-point list.
        history.sort(key=lambda point: point[0])

        return history

    except Exception as exc:

        logger.error(f"get_daily_history failed: {exc}")

        return []


# =========================================================
# Credits (AWS Cost Explorer has no "Credits" metric - the only real
# way to isolate credit records is GetCostAndUsage's existing
# UnblendedCost metric filtered to RECORD_TYPE=Credit)
# =========================================================

def get_credit_history(start_date, end_date):
    """Real AWS credit amounts for [start_date, end_date) via
    GetCostAndUsage, DAILY granularity, filtered to the RECORD_TYPE
    dimension = "Credit" - the AWS-documented way to isolate credit
    records; there is no separate "Credits" metric to request. One
    query yields both the daily history and its total (summed here),
    so no second API call is needed for the total - see requirement to
    avoid unnecessary calls.

    AWS reports Credit records as negative UnblendedCost amounts (a
    credit reduces the bill) - that sign is preserved exactly as AWS
    returns it, never flipped. Any positive "credits applied" framing
    belongs to the presentation layer, not here.

    Returns (history: list[[date, amount]], total: float | None,
    currency: str | None). total/currency are None only when the API
    call itself failed. An account with genuinely zero credit records
    (the common case) returns total=0.0 with an empty history - summing
    zero real records is legitimately zero, not "unknown", so this is
    not treated the same as a failed call."""

    try:
        response = ce.get_cost_and_usage(
            TimePeriod={"Start": _iso_date(start_date), "End": _iso_date(end_date)},
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
            Filter={"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit"]}},
        )

        results = response.get("ResultsByTime") or []

        history = []
        currency = None

        for result in results:

            period_start = (result.get("TimePeriod") or {}).get("Start")

            if not period_start:
                continue

            total_block = result.get("Total") or {}
            cost = total_block.get("UnblendedCost") or {}
            amount = cost.get("Amount")

            if amount is None:
                continue

            amount = round(float(amount), 2)

            # A day with no credit activity reports as 0 (or is simply
            # absent from Total) - omitted from the history, same as
            # "no data that day", not padded with a no-op zero row.
            if amount == 0:
                continue

            currency = cost.get("Unit") or currency

            history.append([period_start, amount])

        history.sort(key=lambda point: point[0])

        total = round(sum(point[1] for point in history), 2) if history else 0.0

        return history, total, currency

    except Exception as exc:

        logger.error(f"get_credit_history failed: {exc}")

        return [], None, None


# =========================================================
# Service / region breakdown
# =========================================================

def get_service_breakdown(start_date, end_date):
    """Real AWS cost grouped by Cost Explorer's own SERVICE dimension -
    never a hardcoded service list, only whatever AWS actually billed.
    This is the NET figure (every record type, including Credit records
    tagged to that service) - see get_service_credit_breakdown() for the
    credit-only isolation needed to recover gross cost per service."""

    return _grouped_cost(start_date, end_date, "SERVICE", "service")


def get_region_breakdown(start_date, end_date):
    """Same as get_service_breakdown but grouped by the REGION dimension."""

    return _grouped_cost(start_date, end_date, "REGION", "region")


def get_service_credit_breakdown(start_date, end_date):
    """Real AWS credit amounts grouped by SERVICE - the same mechanism
    get_credit_history() uses at the account level (RECORD_TYPE=Credit
    filter on GetCostAndUsage), just also grouped by dimension. This is a
    genuinely supported Cost Explorer capability (same API call, no new
    AWS permission) - AWS's billing data attaches a SERVICE value to
    Credit-type records the same way it does to Usage-type records, so
    this reliably answers "which service's credits were these" at the
    account's own billing granularity. It does NOT and cannot answer
    "which EC2 instance" - see get_region_credit_breakdown()'s docstring
    for why resource-level attribution is a separate, unsupported
    question this function makes no attempt to answer."""

    return _grouped_cost(start_date, end_date, "SERVICE", "service", record_type="Credit")


def get_region_credit_breakdown(start_date, end_date):
    """Same mechanism as get_service_credit_breakdown() but grouped by
    REGION. Some credit types (e.g. enterprise/support credits) may not
    carry a specific region in AWS's own billing data - if so, AWS simply
    won't return a group for them here (or will group them under
    whatever generic value AWS itself recorded, e.g. "NoRegion"); nothing
    is inferred or redistributed by this code."""

    return _grouped_cost(start_date, end_date, "REGION", "region", record_type="Credit")


def _grouped_cost(start_date, end_date, dimension_key, label_key, record_type=None):
    """Paginated via NextPageToken - GetCostAndUsage's actual request/
    response field name (confirmed against the boto3 `ce` API, not
    guessed). Keeps requesting the next page until AWS stops returning a
    NextPageToken; a single-page response never enters the loop a second
    time, so single-page behavior is unchanged. Every page's Groups are
    accumulated into the same running `totals` dict, so results from
    every page are summed - never dropped, never double counted even if
    a group were ever split across pages.

    record_type: None fetches every record type (the net-of-credits
    figure per dimension value - AWS has no way to exclude a record type
    from an unfiltered query); "Credit" isolates just RECORD_TYPE=Credit
    records, the same documented mechanism get_credit_history() already
    uses at the account level, just additionally grouped by dimension_key.

    Keeps every group AWS actually returned - including one whose total
    rounds to exactly 0 (a service/region fully offset by a credit in the
    same period) or negative (credits exceeding usage for that dimension
    value). AWS does not return a group at all for a dimension value with
    zero billing activity of any kind, so nothing here is fabricated by
    not filtering; the previous behavior (dropping any non-positive
    total) is what caused fully-credited services/regions to silently
    disappear instead of showing their real gross/credit/net story."""

    try:
        totals = {}
        currency = None
        next_page_token = None

        while True:

            request_kwargs = {
                "TimePeriod": {"Start": _iso_date(start_date), "End": _iso_date(end_date)},
                "Granularity": "MONTHLY",
                "Metrics": ["UnblendedCost"],
                "GroupBy": [{"Type": "DIMENSION", "Key": dimension_key}],
            }

            if record_type:
                request_kwargs["Filter"] = {"Dimensions": {"Key": "RECORD_TYPE", "Values": [record_type]}}

            if next_page_token:
                request_kwargs["NextPageToken"] = next_page_token

            response = ce.get_cost_and_usage(**request_kwargs)

            results = response.get("ResultsByTime") or []

            for result in results:

                for group in result.get("Groups") or []:

                    keys = group.get("Keys") or []

                    if not keys:
                        continue

                    name = keys[0]

                    metrics = group.get("Metrics") or {}
                    cost = metrics.get("UnblendedCost") or {}
                    amount = cost.get("Amount")

                    if amount is None:
                        continue

                    currency = cost.get("Unit") or currency

                    totals[name] = totals.get(name, 0.0) + float(amount)

            next_page_token = response.get("NextPageToken")

            if not next_page_token:
                break

        breakdown = [
            {label_key: name, "cost": round(amount, 2), "currency": currency}
            for name, amount in totals.items()
        ]

        breakdown.sort(key=lambda item: abs(item["cost"]), reverse=True)

        return breakdown

    except Exception as exc:

        logger.error(f"_grouped_cost({dimension_key}, record_type={record_type}) failed: {exc}")

        return []


# =========================================================
# Full period data bundle - the single per-period fetch used for the
# always-computed current/previous periods AND the optional
# user-selected Month/Period Comparison. Reuses every per-metric
# function above rather than reimplementing any of them - this is the
# ONLY place they're called together for an arbitrary caller-supplied
# period.
# =========================================================

def get_period_data(start_date, end_date):
    """Fetches the complete Cost Explorer bundle (total cost, currency,
    daily history, service/region net AND credit breakdown, credits) for
    one [start_date, end_date) window, by calling the existing functions
    - no new AWS query shape is introduced here beyond what
    get_service_credit_breakdown/get_region_credit_breakdown already add.
    Raw facts only; gross_cost per service/region is derived downstream
    in the context builder (net - credits), same convention the
    account-level gross_cost already used - exactly one gross_cost
    formula in the whole pipeline, applied at every level."""

    total_cost, currency = get_total_cost(start_date, end_date)
    daily_history = get_daily_history(start_date, end_date)
    service_breakdown = get_service_breakdown(start_date, end_date)
    service_credit_breakdown = get_service_credit_breakdown(start_date, end_date)
    region_breakdown = get_region_breakdown(start_date, end_date)
    region_credit_breakdown = get_region_credit_breakdown(start_date, end_date)
    credit_history, credit_total, credit_currency = get_credit_history(start_date, end_date)

    return {
        "from": _iso_date(start_date),
        "to": _iso_date(end_date - timedelta(days=1)),
        "total_cost": total_cost,
        "currency": currency or credit_currency,
        "daily_history": daily_history,
        "service_breakdown": service_breakdown,
        "service_credit_breakdown": service_credit_breakdown,
        "region_breakdown": region_breakdown,
        "region_credit_breakdown": region_credit_breakdown,
        "credits": {
            "total": credit_total,
            "currency": credit_currency,
            "history": credit_history,
        },
    }


# =========================================================
# Cost anomalies - three honest states, never a fabricated threshold
# =========================================================

def get_anomalies(start_date, end_date):
    """AWS Cost Anomaly Detection findings, if available.

    - "not_configured": account has zero AnomalyMonitors - AWS isn't
      watching for anomalies at all right now.
    - "none_found": monitors exist, GetAnomalies returned nothing for
      this window - i.e. AWS actually checked and found nothing.
    - "found": real AWS-reported anomalies, their actual
      impact/score/service/region/dates/monitor preserved as-is.
    - "unsupported_range": AWS rejected the requested date window as
      invalid for anomaly detection (e.g. end_date beyond AWS's latest
      supported detection date) - a legitimate condition, distinct from
      a real failure, so it's never confused with "unavailable".
    - "unavailable": an actual unexpected API/system error (permissions,
      throttling, network, ...) - never silently folded into
      "none_found" or "unsupported_range".

    end_date must not exceed AWS's latest supported detection date
    (effectively "today") - callers are responsible for passing a date
    that respects that constraint; see main()'s anomaly_end_date.

    Both get_anomaly_monitors() and get_anomalies() are paginated via
    NextPageToken (their actual boto3 request/response field, same as
    GetCostAndUsage) until AWS stops returning one - so an account with
    more than one page of monitors or anomalies is never silently
    truncated to the first page.

    AWS does not provide a severity classification for an anomaly - only
    anomaly_score/max_anomaly_score and impact figures are real fields;
    no severity bucket is invented anywhere in this pipeline."""

    try:
        monitors = []
        next_page_token = None

        while True:

            request_kwargs = {}

            if next_page_token:
                request_kwargs["NextPageToken"] = next_page_token

            monitors_response = ce.get_anomaly_monitors(**request_kwargs)

            monitors.extend(monitors_response.get("AnomalyMonitors") or [])

            next_page_token = monitors_response.get("NextPageToken")

            if not next_page_token:
                break

    except Exception as exc:

        logger.error(f"get_anomaly_monitors failed: {exc}")

        return {"status": "unavailable", "reason": str(exc), "anomalies": []}

    if not monitors:
        return {
            "status": "not_configured",
            "reason": "No AWS Cost Anomaly monitors configured",
            "anomalies": [],
        }

    # ARN -> Name, so each anomaly below can carry its monitor's real
    # name alongside its ARN without a second AWS call - monitors was
    # already fetched above for the not_configured check.
    monitor_names = {
        monitor.get("MonitorArn"): monitor.get("MonitorName")
        for monitor in monitors
        if monitor.get("MonitorArn")
    }

    try:
        raw_anomalies = []
        next_page_token = None

        while True:

            request_kwargs = {
                "DateInterval": {"StartDate": _iso_date(start_date), "EndDate": _iso_date(end_date)},
            }

            if next_page_token:
                request_kwargs["NextPageToken"] = next_page_token

            anomalies_response = ce.get_anomalies(**request_kwargs)

            raw_anomalies.extend(anomalies_response.get("Anomalies") or [])

            next_page_token = anomalies_response.get("NextPageToken")

            if not next_page_token:
                break

    except Exception as exc:

        if _error_code(exc) == "ValidationException":

            # A legitimate, expected AWS constraint (e.g. the requested
            # date window falls outside what GetAnomalies currently
            # supports) - not a system failure, so it gets its own status
            # rather than being folded into "unavailable".
            logger.warning(f"get_anomalies rejected the requested date range: {exc}")

            return {"status": "unsupported_range", "reason": str(exc), "anomalies": []}

        logger.error(f"get_anomalies failed: {exc}")

        return {"status": "unavailable", "reason": str(exc), "anomalies": []}

    if not raw_anomalies:
        return {"status": "none_found", "reason": "No AWS cost anomaly detected", "anomalies": []}

    anomalies = []

    for anomaly in raw_anomalies:

        impact = anomaly.get("Impact") or {}
        score = anomaly.get("AnomalyScore") or {}
        monitor_arn = anomaly.get("MonitorArn")

        # RootCauses is where AWS actually attaches region/linked-account
        # detail for an anomaly - DimensionValue above is just the
        # monitor's own grouping key (often the service name), not a
        # region. A single anomaly can have more than one root cause
        # (e.g. it spans regions or linked accounts) - every one AWS
        # returned is kept in root_causes; "region" below is only a
        # convenience read of the first entry for simple display and
        # must not be read as "the only region involved".
        root_causes = [
            {
                "service": cause.get("Service"),
                "region": cause.get("Region"),
                "linked_account": cause.get("LinkedAccount"),
                "linked_account_name": cause.get("LinkedAccountName"),
                "usage_type": cause.get("UsageType"),
                "contribution": (cause.get("Impact") or {}).get("Contribution"),
            }
            for cause in (anomaly.get("RootCauses") or [])
        ]

        anomalies.append({
            "anomaly_id": anomaly.get("AnomalyId"),
            "service": anomaly.get("DimensionValue"),
            "region": root_causes[0]["region"] if root_causes else None,
            "start_date": anomaly.get("AnomalyStartDate"),
            "end_date": anomaly.get("AnomalyEndDate"),
            "total_impact": impact.get("TotalImpact"),
            "max_impact": impact.get("MaxImpact"),
            "impact_percentage": impact.get("TotalImpactPercentage"),
            "total_actual_spend": impact.get("TotalActualSpend"),
            "total_expected_spend": impact.get("TotalExpectedSpend"),
            "anomaly_score": score.get("CurrentScore"),
            "max_anomaly_score": score.get("MaxScore"),
            "monitor_arn": monitor_arn,
            "monitor_name": monitor_names.get(monitor_arn),
            "root_causes": root_causes,
            "feedback": anomaly.get("Feedback"),
        })

    return {"status": "found", "reason": None, "anomalies": anomalies}


# =========================================================
# Main
# =========================================================

def main(from_date=None, to_date=None):
    """from_date/to_date (optional "YYYY-MM-DD" strings, both required
    together) are the user-selected Month/Period Comparison range from
    the dashboard's date pickers - entirely dynamic, never a hardcoded
    month. When supplied, a "comparison" block is added to the output
    with two full period bundles (the selected period and, via the same
    _comparison_period() helper the default current/previous comparison
    already uses, the immediately preceding period of equal length).
    When omitted (the default, unchanged path), no comparison is
    fetched and no extra AWS calls are made.

    current/previous are both full get_period_data() bundles (gross-
    capable: net + credit breakdown per service/region, daily history,
    credits) - not just a single total each, as before - so the context
    builder can derive gross/credits/net at the service and region level
    for both periods, and so "Change" columns in the redesigned Cost
    Explorer page have real previous-period data to compare against."""

    logger.info("Starting Cost Explorer Collector...")

    bounds = _period_bounds()

    current = get_period_data(bounds["current_start"], bounds["current_end"])
    previous = get_period_data(bounds["previous_start"], bounds["previous_end"])

    # GetAnomalies' DateInterval.EndDate has a different constraint than
    # GetCostAndUsage's exclusive End: AWS caps it at the "latest
    # supported detection date," which is today (inclusive) - not
    # tomorrow. bounds["current_end"] is deliberately tomorrow (for the
    # cost-usage calls above), so it must NOT be reused here as-is;
    # subtracting one day recovers "today" from the same value already
    # computed, without introducing a second date calculation.
    anomaly_end_date = bounds["current_end"] - timedelta(days=1)

    anomalies = get_anomalies(bounds["current_start"], anomaly_end_date)

    comparison = None

    if from_date and to_date:

        # User-selected "From Date" is inclusive; GetCostAndUsage's End
        # is exclusive, so period_a_end = to_date + 1 day - exactly the
        # rule the dashboard's date pickers document, dynamically
        # computed from whatever the user actually picked, never today
        # or a specific month.
        period_a_start = datetime.strptime(from_date, "%Y-%m-%d").date()
        period_a_end = datetime.strptime(to_date, "%Y-%m-%d").date() + timedelta(days=1)

        period_b_start, period_b_end = _comparison_period(period_a_start, period_a_end)

        period_a = get_period_data(period_a_start, period_a_end)
        period_b = get_period_data(period_b_start, period_b_end)

        # GetAnomalies has its own supported-range constraint (see
        # anomaly_end_date above) - a comparison period far in the past
        # will legitimately come back "unsupported_range"; that is a
        # real, honest AWS answer, not a bug, and is surfaced as-is.
        period_a_anomaly_end = min(period_a_end, bounds["current_end"]) - timedelta(days=1)
        period_b_anomaly_end = min(period_b_end, bounds["current_end"]) - timedelta(days=1)

        comparison = {
            "period_a": period_a,
            "period_b": period_b,
            "period_a_anomalies": get_anomalies(period_a_start, period_a_anomaly_end),
            "period_b_anomalies": get_anomalies(period_b_start, period_b_anomaly_end),
        }

    data = {

        "collector": "cost_explorer",

        "timestamp": datetime.now(UTC).isoformat(),

        "currency": current["currency"] or previous["currency"],

        "period": {

            "current_start": _iso_date(bounds["current_start"]),
            "current_end": _iso_date(bounds["current_end"] - timedelta(days=1)),
            "previous_start": _iso_date(bounds["previous_start"]),
            "previous_end": _iso_date(bounds["previous_end"] - timedelta(days=1)),
            "lookback_days": COST_LOOKBACK_DAYS,

        },

        "current_period": current,

        "previous_period": previous,

        "anomalies": anomalies,

        "comparison": comparison,

    }

    write_json("cost_explorer.json", data)

    print("\nCost Explorer JSON report generated successfully.")


# =========================================================
# Entry Point
# =========================================================

if __name__ == "__main__":
    main()

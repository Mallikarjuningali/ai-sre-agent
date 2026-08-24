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

from datetime import datetime, timedelta, UTC

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


def _period_bounds():
    """Current period: the last COST_LOOKBACK_DAYS days, ending today.
    Previous period: the equal-length window immediately before it - both
    windows entirely driven by the one COST_LOOKBACK_DAYS constant, no
    calendar-month assumptions. Cost Explorer's End date is exclusive, so
    current_end is "tomorrow" to include all of "today"."""

    today = datetime.now(UTC).date()

    current_end = today + timedelta(days=1)
    current_start = today - timedelta(days=COST_LOOKBACK_DAYS - 1)

    previous_end = current_start
    previous_start = current_start - timedelta(days=COST_LOOKBACK_DAYS)

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

        return round(total_amount, 2), currency

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
    classification happens here."""

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

            date_obj = datetime.strptime(period_start, "%Y-%m-%d")
            date_label = date_obj.strftime("%d-%m")

            history.append([date_label, round(float(amount), 2)])

        return history

    except Exception as exc:

        logger.error(f"get_daily_history failed: {exc}")

        return []


# =========================================================
# Service / region breakdown
# =========================================================

def get_service_breakdown(start_date, end_date):
    """Real AWS cost grouped by Cost Explorer's own SERVICE dimension -
    never a hardcoded service list, only whatever AWS actually billed."""

    return _grouped_cost(start_date, end_date, "SERVICE", "service")


def get_region_breakdown(start_date, end_date):
    """Same as get_service_breakdown but grouped by the REGION dimension."""

    return _grouped_cost(start_date, end_date, "REGION", "region")


def _grouped_cost(start_date, end_date, dimension_key, label_key):
    """Paginated via NextPageToken - GetCostAndUsage's actual request/
    response field name (confirmed against the boto3 `ce` API, not
    guessed). Keeps requesting the next page until AWS stops returning a
    NextPageToken; a single-page response never enters the loop a second
    time, so single-page behavior is unchanged. Every page's Groups are
    accumulated into the same running `totals` dict, so results from
    every page are summed - never dropped, never double counted even if
    a group were ever split across pages."""

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
            if amount > 0
        ]

        breakdown.sort(key=lambda item: item["cost"], reverse=True)

        return breakdown

    except Exception as exc:

        logger.error(f"_grouped_cost({dimension_key}) failed: {exc}")

        return []


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
      impact/score/service/dates preserved as-is.
    - "unavailable": an API call failed (e.g. permissions) - never
      silently folded into "none_found".

    Both get_anomaly_monitors() and get_anomalies() are paginated via
    NextPageToken (their actual boto3 request/response field, same as
    GetCostAndUsage) until AWS stops returning one - so an account with
    more than one page of monitors or anomalies is never silently
    truncated to the first page."""

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

        logger.error(f"get_anomalies failed: {exc}")

        return {"status": "unavailable", "reason": str(exc), "anomalies": []}

    if not raw_anomalies:
        return {"status": "none_found", "reason": "No AWS cost anomaly detected", "anomalies": []}

    anomalies = []

    for anomaly in raw_anomalies:

        impact = anomaly.get("Impact") or {}
        score = anomaly.get("AnomalyScore") or {}

        anomalies.append({
            "anomaly_id": anomaly.get("AnomalyId"),
            "service": anomaly.get("DimensionValue"),
            "start_date": anomaly.get("AnomalyStartDate"),
            "end_date": anomaly.get("AnomalyEndDate"),
            "total_impact": impact.get("TotalImpact"),
            "max_impact": impact.get("MaxImpact"),
            "impact_percentage": impact.get("TotalImpactPercentage"),
            "anomaly_score": score.get("CurrentScore"),
            "feedback": anomaly.get("Feedback"),
        })

    return {"status": "found", "reason": None, "anomalies": anomalies}


# =========================================================
# Main
# =========================================================

def main():

    logger.info("Starting Cost Explorer Collector...")

    bounds = _period_bounds()

    current_total, currency = get_total_cost(bounds["current_start"], bounds["current_end"])
    previous_total, previous_currency = get_total_cost(bounds["previous_start"], bounds["previous_end"])

    daily_history = get_daily_history(bounds["current_start"], bounds["current_end"])

    service_breakdown = get_service_breakdown(bounds["current_start"], bounds["current_end"])
    region_breakdown = get_region_breakdown(bounds["current_start"], bounds["current_end"])

    anomalies = get_anomalies(bounds["current_start"], bounds["current_end"])

    data = {

        "collector": "cost_explorer",

        "timestamp": datetime.now(UTC).isoformat(),

        "currency": currency or previous_currency,

        "period": {

            "current_start": _iso_date(bounds["current_start"]),
            "current_end": _iso_date(bounds["current_end"] - timedelta(days=1)),
            "previous_start": _iso_date(bounds["previous_start"]),
            "previous_end": _iso_date(bounds["previous_end"] - timedelta(days=1)),
            "lookback_days": COST_LOOKBACK_DAYS,

        },

        "total_cost": current_total,

        "previous_cost": previous_total,

        "daily_history": daily_history,

        "service_breakdown": service_breakdown,

        "region_breakdown": region_breakdown,

        "anomalies": anomalies,

    }

    write_json("cost_explorer.json", data)

    print("\nCost Explorer JSON report generated successfully.")


# =========================================================
# Entry Point
# =========================================================

if __name__ == "__main__":
    main()

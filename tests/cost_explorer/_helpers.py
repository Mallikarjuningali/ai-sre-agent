"""
Shared test fixtures for the Cost Explorer test suite. No real AWS or
Gemini calls are ever made from any test in this package - collector/
cost_explorer.py's module-level `ce` client and llm/llm_engine.py's
LLMEngine.analyze are always replaced with mocks/stubs before use.
"""
import datetime as _dt
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

# Test-environment-only compatibility shim: `datetime.UTC` is a Python 3.11+
# name, but this sandbox runs Python 3.9. Production targets 3.11+ (see
# collector/cost_explorer.py's `from datetime import ... UTC`) - this shim
# never ships, it only lets the existing 3.11+ source import cleanly under
# the 3.9 interpreter this suite happens to run under here.
if not hasattr(_dt, "UTC"):
    _dt.UTC = _dt.timezone.utc


def cost_and_usage_response(groups=None, amount="100.00", currency="USD", period_start="2026-01-01", next_token=None):
    """A single ResultsByTime entry shaped like a real GetCostAndUsage
    response - either a plain Total (no GroupBy) or Groups (GroupBy)."""
    result = {"TimePeriod": {"Start": period_start, "End": period_start}}
    if groups is not None:
        result["Groups"] = [
            {"Keys": [name], "Metrics": {"UnblendedCost": {"Amount": str(cost), "Unit": currency}}}
            for name, cost in groups
        ]
    else:
        result["Total"] = {"UnblendedCost": {"Amount": str(amount), "Unit": currency}}
    response = {"ResultsByTime": [result]}
    if next_token:
        response["NextPageToken"] = next_token
    return response


def anomaly_monitor(arn="arn:aws:ce::111122223333:anomalymonitor/abc", name="Default Monitor"):
    return {"MonitorArn": arn, "MonitorName": name}


def anomaly_record(anomaly_id="anomaly-1", service="Amazon EC2", region="us-east-1",
                    start="2026-01-05", end="2026-01-06", total_impact=42.5,
                    monitor_arn="arn:aws:ce::111122223333:anomalymonitor/abc"):
    return {
        "AnomalyId": anomaly_id,
        "DimensionValue": service,
        "AnomalyStartDate": start,
        "AnomalyEndDate": end,
        "Impact": {"TotalImpact": total_impact, "MaxImpact": total_impact, "TotalImpactPercentage": 25.0,
                    "TotalActualSpend": 100.0, "TotalExpectedSpend": 57.5},
        "AnomalyScore": {"CurrentScore": 80.0, "MaxScore": 80.0},
        "MonitorArn": monitor_arn,
        "RootCauses": [{"Service": service, "Region": region, "LinkedAccount": "111122223333",
                          "LinkedAccountName": "prod", "UsageType": "BoxUsage", "Impact": {"Contribution": 42.5}}],
        "Feedback": None,
    }

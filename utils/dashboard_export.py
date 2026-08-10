"""
=========================================================
AI SRE AGENT
Module : Dashboard Export
Purpose:
    Bridge between ai-sre-agent's real output/ tree and the
    AegisOps AI dashboard's data contract (see
    dashboard/services/CONTRACT.md in the sibling project).

    Reads output/{summary,reports,context,archive} (read-only)
    and writes summary.json, collectors.json, executions.json,
    reports.json, analytics.json into output/dashboard_feed/.

    Safe to run standalone with no AWS/Gemini calls and no
    side effects on the collection pipeline:

        python -m utils.dashboard_export
=========================================================
"""

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

OUTPUT_DIR = Path("output")
SUMMARY_DIR = OUTPUT_DIR / "summary"
REPORTS_DIR = OUTPUT_DIR / "reports"
CONTEXT_DIR = OUTPUT_DIR / "context"
ARCHIVE_DIR = OUTPUT_DIR / "archive"
FEED_DIR = OUTPUT_DIR / "dashboard_feed"
STATE_FILE = FEED_DIR / ".export_state.json"

RESOURCE_TYPE_LABELS = {"EC2": "EC2 Instance"}

COLLECTOR_LABELS = {
    "cloudwatch": "CloudWatch",
    "alb": "ALB",
    "autoscaling": "AutoScaling",
    "cloudtrail": "CloudTrail",
}

STATUS_MAP = {
    "SUCCESS": "COMPLETED",
    "PARTIAL_SUCCESS": "COMPLETED_WITH_ERRORS",
    "FAILED": "FAILED",
}

SEVERITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


# =========================================================
# Timestamp helpers
# =========================================================

def parse_ist_clock_string(text):
    """'%d-%m-%Y %H:%M:%S IST' -> IST-aware datetime, or None."""
    if not text:
        return None
    cleaned = str(text).replace("IST", "").strip()
    try:
        return datetime.strptime(cleaned, "%d-%m-%Y %H:%M:%S").replace(tzinfo=IST)
    except ValueError:
        return None


def parse_run_id(run_id):
    """'%d-%m-%Y_%H-%M-%S' -> IST-aware datetime, or None."""
    if not run_id:
        return None
    try:
        return datetime.strptime(str(run_id), "%d-%m-%Y_%H-%M-%S").replace(tzinfo=IST)
    except ValueError:
        return None


def mtime_dt(path: Path):
    return datetime.fromtimestamp(path.stat().st_mtime, tz=IST)


# =========================================================
# Value normalization
# =========================================================

def normalize_confidence(raw):
    """Gemini's confidence scale isn't contractually fixed - accept 0-1 or 0-100."""
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return 0.0
    if value <= 1:
        return value
    if value <= 100:
        return value / 100.0
    return 1.0


def label_resource_type(raw):
    if not raw:
        return None
    return RESOURCE_TYPE_LABELS.get(raw, raw)


def round_int(value):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def read_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# =========================================================
# Source loaders (read-only)
# =========================================================

def load_run_summaries():
    """Every output/summary/*.json - the full execution history, oldest first."""
    if not SUMMARY_DIR.exists():
        return []

    runs = []
    for path in sorted(SUMMARY_DIR.glob("*.json")):
        data = read_json(path)
        if data is None:
            continue
        run_id = data.get("run_id") or path.stem
        start_dt = parse_run_id(run_id) or parse_ist_clock_string(data.get("start_time"))
        data["_run_id"] = run_id
        data["_start_dt"] = start_dt
        runs.append(data)

    runs.sort(key=lambda r: r["_start_dt"] or datetime.min.replace(tzinfo=IST))
    return runs


def load_current_reports():
    """output/reports/<instance_id>.json - latest known report per instance."""
    if not REPORTS_DIR.exists():
        return {}

    reports = {}
    for path in sorted(REPORTS_DIR.glob("*.json")):
        data = read_json(path)
        if data is None:
            continue
        reports[path.stem] = {"data": data, "path": path}
    return reports


def load_current_contexts():
    """output/context/<instance_id>.json - resource_type per instance."""
    if not CONTEXT_DIR.exists():
        return {}

    contexts = {}
    for path in sorted(CONTEXT_DIR.glob("*.json")):
        data = read_json(path)
        if data is None:
            continue
        contexts[path.stem] = data
    return contexts


def load_archive_severity_snapshots():
    """
    One (archive_dt, {instance_id: SEVERITY}) per output/archive/<ts>/reports/,
    oldest first. This is the only real source of per-run historical severity
    data (capped at MAX_RUN_HISTORY archive folders).
    """
    if not ARCHIVE_DIR.exists():
        return []

    snapshots = []
    for run_dir in ARCHIVE_DIR.iterdir():
        if not run_dir.is_dir():
            continue
        archive_dt = parse_run_id(run_dir.name)
        if archive_dt is None:
            continue
        reports_dir = run_dir / "reports"
        if not reports_dir.exists():
            continue

        severities = {}
        for report_path in reports_dir.glob("*.json"):
            data = read_json(report_path)
            if data is None:
                continue
            severity = str(data.get("severity") or "").upper()
            if severity:
                severities[report_path.stem] = severity

        snapshots.append((archive_dt, severities))

    snapshots.sort(key=lambda s: s[0])
    return snapshots


# =========================================================
# Change-detection state (persisted across exports)
# =========================================================

def load_prev_state():
    if not STATE_FILE.exists():
        return {}
    return read_json(STATE_FILE) or {}


def atomic_write_json(path: Path, obj):
    """Write-to-temp + os.replace() so the dashboard's poller never observes
    a half-written file (a mid-write JSONDecodeError on collectors.json alone
    is enough to blank the entire dashboard app - see app.py)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp_path, path)


def find_run_id_for(runs, at_dt):
    """Latest run (ascending list) whose start_time <= at_dt. Advisory only."""
    candidate = None
    for run in runs:
        start_dt = run.get("_start_dt")
        if start_dt and start_dt <= at_dt:
            candidate = run.get("_run_id")
        else:
            break
    return candidate


# =========================================================
# Builders
# =========================================================

def build_collectors_json(runs):
    if not runs:
        return {}
    collectors = runs[-1].get("collectors") or {}
    result = {}
    for key, label in COLLECTOR_LABELS.items():
        value = str(collectors.get(key, "")).upper()
        result[label] = "Healthy" if value == "SUCCESS" else "Degraded"
    return result


def build_executions_json(runs):
    executions = []
    for run in runs:
        start_dt = run.get("_start_dt")
        end_dt = parse_ist_clock_string(run.get("end_time"))
        executions.append(
            {
                "run_id": run.get("_run_id"),
                "status": STATUS_MAP.get(str(run.get("status", "")).upper(), run.get("status")),
                "execution_time": round_int(run.get("execution_time_seconds")),
                "successful": run.get("successful", 0),
                "failed": run.get("failed", 0),
                "resources": run.get("instances_discovered", 0),
                "resources_analyzed": run.get("instances_analyzed", 0),
                "resources_skipped": run.get("resources_skipped", 0),
                "reports_generated": run.get("reports_generated", 0),
                "start_time": start_dt.isoformat() if start_dt else None,
                "finished_at": end_dt.isoformat() if end_dt else None,
            }
        )
    executions.sort(key=lambda e: e["start_time"] or "", reverse=True)
    return executions


def build_reports_json(reports, contexts, runs, prev_state):
    """Returns (reports_json_array, new_state_snapshot)."""
    result = []
    new_state = {}

    for instance_id, entry in sorted(reports.items()):
        data = entry["data"]
        path = entry["path"]
        severity = str(data.get("severity") or "UNKNOWN").upper()
        detected_dt = mtime_dt(path)

        report = {
            "instance_id": instance_id,
            "report_id": instance_id,
            "severity": severity,
            "summary": data.get("summary", ""),
            "root_cause": data.get("root_cause", ""),
            "evidence": data.get("evidence") or [],
            "recommendations": data.get("recommendations") or [],
            "detected_at": detected_dt.isoformat(),
        }

        confidence = normalize_confidence(data.get("confidence"))
        if confidence is not None:
            report["ai_confidence"] = confidence

        context = contexts.get(instance_id)
        if context:
            resource_type = label_resource_type(context.get("resource_type"))
            if resource_type:
                report["resource_type"] = resource_type

            cloudwatch_ctx = (context.get("context") or {}).get("cloudwatch")
            if cloudwatch_ctx:
                report["telemetry"] = {
                    "state": cloudwatch_ctx.get("State"),
                    "cpu": cloudwatch_ctx.get("CPU"),
                    "memory": cloudwatch_ctx.get("Memory"),
                    "disk": cloudwatch_ctx.get("Disk"),
                    "network_in": cloudwatch_ctx.get("NetworkIn"),
                    "network_out": cloudwatch_ctx.get("NetworkOut"),
                    "status_check": cloudwatch_ctx.get("StatusCheck"),
                }

        run_id = find_run_id_for(runs, detected_dt)
        if run_id:
            report["run_id"] = run_id

        prev_severity = prev_state.get(instance_id)
        if prev_severity is None:
            report["is_new"] = True
        elif prev_severity != severity:
            if SEVERITY_RANK.get(severity, 0) > SEVERITY_RANK.get(prev_severity, 0):
                report["severity_changed_from"] = prev_severity

        new_state[instance_id] = severity
        result.append(report)

    result.sort(key=lambda r: r["detected_at"], reverse=True)
    return result, new_state


def build_severity_trend(archives):
    trend = []
    for archive_dt, severities in archives:
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for severity in severities.values():
            key = severity.lower()
            if key in counts:
                counts[key] += 1
        trend.append({"date": archive_dt.strftime("%d %b, %H:%M"), **counts})
    return trend


def build_top_affected_resources(archives, contexts, limit=5):
    counts = {}
    for _, severities in archives:
        for instance_id in severities:
            counts[instance_id] = counts.get(instance_id, 0) + 1

    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]

    result = []
    for instance_id, count in ranked:
        context = contexts.get(instance_id)
        resource_type = label_resource_type(context.get("resource_type")) if context else None
        result.append({"resource_id": instance_id, "type": resource_type or "—", "incidents": count})
    return result


def build_resource_distribution(contexts):
    counts = {}
    for context in contexts.values():
        label = label_resource_type(context.get("resource_type")) or "Unknown"
        counts[label] = counts.get(label, 0) + 1
    return [{"type": t, "count": c} for t, c in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)]


def build_resources_json(contexts):
    """Discovered resource inventory grouped by type (dashboard's resources.json
    contract - backs the Investigation page's Single Resource picker). Every
    instance_id/resource_type pair here is a real output/context/*.json file
    the collectors already wrote; nothing is invented."""
    grouped = {}
    for instance_id, context in contexts.items():
        resource_type = label_resource_type(context.get("resource_type")) or "Unknown"
        grouped.setdefault(resource_type, []).append({"id": instance_id, "label": instance_id})
    for entries in grouped.values():
        entries.sort(key=lambda e: e["id"])
    return grouped


def build_investigation_trend(runs):
    return [
        {
            "date": (run.get("_start_dt").strftime("%d %b, %H:%M") if run.get("_start_dt") else run.get("_run_id", "")),
            "count": run.get("reports_generated", 0),
        }
        for run in runs
    ]


def build_execution_time_trend(runs):
    return [
        {
            "date": (run.get("_start_dt").strftime("%d %b, %H:%M") if run.get("_start_dt") else run.get("_run_id", "")),
            "avg_seconds": run.get("execution_time_seconds", 0),
        }
        for run in runs
    ]


def build_summary_json(runs, reports_json, severity_trend, top_affected, investigation_trend):
    generated_at = datetime.now(IST).isoformat()

    kpis = {}
    infra_health = {}
    latest_execution = {}

    if runs:
        latest = runs[-1]
        instances_discovered = latest.get("instances_discovered", 0)
        instances_analyzed = latest.get("instances_analyzed", 0)
        successful = latest.get("successful", 0)
        reports_generated = latest.get("reports_generated", 0)

        critical_count = sum(1 for r in reports_json if r["severity"] == "CRITICAL")
        high_count = sum(1 for r in reports_json if r["severity"] == "HIGH")

        success_rate = None
        if instances_analyzed:
            success_rate = round((successful / instances_analyzed) * 100, 1)

        kpis = {
            "total_resources": {"value": instances_discovered},
            "critical_issues": {"value": critical_count},
            "high_severity": {"value": high_count},
            "investigations": {"value": reports_generated},
            "success_rate": {"value": success_rate},
        }

        reported_ids = {r["instance_id"] for r in reports_json}
        critical = critical_count
        warning = sum(1 for r in reports_json if r["severity"] in ("HIGH", "MEDIUM"))
        healthy = sum(1 for r in reports_json if r["severity"] == "LOW")
        no_report = max(instances_discovered - len(reported_ids), 0)
        unrecognized = sum(1 for r in reports_json if r["severity"] not in SEVERITY_RANK)
        infra_health = {
            "total": instances_discovered,
            "healthy": healthy,
            "warning": warning,
            "critical": critical,
            "unknown": no_report + unrecognized,
        }

        start_dt = latest.get("_start_dt")
        latest_execution = {
            "run_id": latest.get("_run_id"),
            "status": STATUS_MAP.get(str(latest.get("status", "")).upper(), latest.get("status")),
            "resources": instances_discovered,
            "successful": successful,
            "failed": latest.get("failed", 0),
            "duration_seconds": round_int(latest.get("execution_time_seconds")),
            "start_time": start_dt.isoformat() if start_dt else None,
        }

    recent_investigations = []
    for r in reports_json[:6]:
        item = {
            "report_id": r["report_id"],
            "instance_id": r["instance_id"],
            "severity": r["severity"],
            "summary": r.get("summary", ""),
            "detected_at": r.get("detected_at"),
        }
        if r.get("is_new"):
            item["is_new"] = True
        if r.get("severity_changed_from"):
            item["severity_changed_from"] = r["severity_changed_from"]
        recent_investigations.append(item)

    return {
        "generated_at": generated_at,
        "kpis": kpis,
        "infrastructure_health": infra_health,
        "latest_execution": latest_execution,
        "incidents_by_severity_trend": severity_trend,
        "top_affected_resources": top_affected,
        "investigation_trend": investigation_trend,
        "recent_investigations": recent_investigations,
    }


def build_analytics_json(incident_trend, severity_trend, resource_distribution, execution_time_trend):
    return {
        "incident_trend": incident_trend,
        "severity_trend": severity_trend,
        "resource_distribution": resource_distribution,
        # No event-count/latency instrumentation exists anywhere in collector/*.py
        # today - an empty array is the honest signal, synthesized zeros would
        # misleadingly imply this was measured.
        "collector_statistics": [],
        "execution_time_trend": execution_time_trend,
    }


# =========================================================
# Entry point
# =========================================================

def export():
    FEED_DIR.mkdir(parents=True, exist_ok=True)

    runs = load_run_summaries()
    reports = load_current_reports()
    contexts = load_current_contexts()
    archives = load_archive_severity_snapshots()
    prev_state = load_prev_state()

    reports_json, new_state = build_reports_json(reports, contexts, runs, prev_state)
    severity_trend = build_severity_trend(archives)
    top_affected = build_top_affected_resources(archives, contexts)
    investigation_trend = build_investigation_trend(runs)
    resource_distribution = build_resource_distribution(contexts)
    execution_time_trend = build_execution_time_trend(runs)
    resources_json = build_resources_json(contexts)

    collectors_json = build_collectors_json(runs)
    executions_json = build_executions_json(runs)
    summary_json = build_summary_json(runs, reports_json, severity_trend, top_affected, investigation_trend)
    analytics_json = build_analytics_json(investigation_trend, severity_trend, resource_distribution, execution_time_trend)

    atomic_write_json(FEED_DIR / "collectors.json", collectors_json)
    atomic_write_json(FEED_DIR / "executions.json", executions_json)
    atomic_write_json(FEED_DIR / "reports.json", reports_json)
    atomic_write_json(FEED_DIR / "summary.json", summary_json)
    atomic_write_json(FEED_DIR / "analytics.json", analytics_json)
    atomic_write_json(FEED_DIR / "resources.json", resources_json)
    atomic_write_json(STATE_FILE, new_state)


if __name__ == "__main__":
    export()

"""
=========================================================
AI SRE AGENT
Module : Log Investigation Manager
Purpose:
    Runs the OPTIONAL, explicitly user-triggered Log Investigation stage
    for an existing EC2/ALB/ASG infrastructure investigation. Completely
    separate from api/investigation_manager.py (which runs the normal
    collectors -> Gemini RCA pipeline) - this module never collects
    metrics, never runs during a normal Full/Single Resource Investigation,
    and is invoked only when a user explicitly clicks "Investigate Logs".

    Sequence: read the EXISTING report + context (read-only, same files
    api/follow_up_manager.py already reads) -> derive a bounded incident
    window (utils/incident_window.py) -> discover/fetch the resource
    type's real log source (collector/logs.py) -> reduce it locally
    (context/log_evidence_builder.py) -> sanitize it through the NEW,
    ISOLATED Log Sanitizer (llm/log_sanitizer.py) -> build a prompt
    (llm/log_prompt_builder.py) -> call the existing, unchanged
    llm/llm_engine.py::LLMEngine -> persist (utils/log_investigation_store.py)
    -> return a structured result.

    investigation_id is the same f"{run_id}__{resource_id}" scheme
    Follow-Up Q&A already established, and the same immutability/
    staleness cross-check (find_run_id_for against the report's own
    advisory run_id) is reused here unchanged - the original RCA report
    is never written to by this feature; a Log Investigation result is
    always a separate, sibling artifact.
=========================================================
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

import collector.logs as log_collector
from config.settings import REGION, LOG_MAX_ASG_MEMBER_INSTANCES_SCANNED
from context.log_evidence_builder import build_evidence_package, empty_evidence_package
from llm.log_prompt_builder import LogPromptBuilder
from llm.log_sanitizer import LogSanitizer
from llm.llm_engine import LLMEngine
from utils import log_investigation_store
from utils.dashboard_export import CONTEXT_DIR, REPORTS_DIR, find_run_id_for, load_run_summaries, mtime_dt
from utils.incident_window import resolve_analysis_window
from utils.logger import get_logger

logger = get_logger("LogInvestigationManager")

_SUPPORTED_RESOURCE_TYPES = ("EC2", "Load Balancer", "Auto Scaling Group")


class LogInvestigationNotFoundError(Exception):
    """No report/context exists for the requested resource, or
    investigation_id is malformed."""


class LogInvestigationSupersededError(Exception):
    """The resource has been reinvestigated since the run_id encoded in
    investigation_id - the original report is immutable, so a log
    investigation cannot be grounded in evidence that no longer matches
    what's on disk."""


class LogInvestigationUnsupportedResourceError(Exception):
    """This resource's type has no supported log source design (only
    EC2/Load Balancer/Auto Scaling Group are supported)."""


class LogInvestigationUnavailableError(Exception):
    """The Gemini call for this log analysis failed/timed out. Nothing is
    persisted for a request that never got an answer - the evidence
    package that was already gathered is simply discarded, matching
    Follow-Up Q&A's own "don't persist a half-finished result" behavior."""


def _split_investigation_id(investigation_id: str):
    if not investigation_id or "__" not in investigation_id:
        raise LogInvestigationNotFoundError(f"Malformed investigation_id: {investigation_id!r}")
    run_id, _, resource_id = investigation_id.rpartition("__")
    if not run_id or not resource_id:
        raise LogInvestigationNotFoundError(f"Malformed investigation_id: {investigation_id!r}")
    return run_id, resource_id


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _fetch_ec2_events(resource_id: str, window: Dict[str, Any]):
    """Returns (raw_events, log_source, limitation_or_none)."""
    log_source_ref = log_collector.discover_ec2_log_source(resource_id)
    if log_source_ref is None:
        return [], "unavailable", "Logs unavailable for this resource - no configured CloudWatch Logs stream was found for this instance."

    events = log_collector.fetch_ec2_log_events(
        log_source_ref["log_group"], log_source_ref["log_stream"], window["start"], window["end"]
    )
    return events, "cloudwatch_logs", None


def _fetch_alb_events(resource_id: str, window: Dict[str, Any]):
    lb_arn = log_collector.resolve_alb_arn(resource_id)
    if lb_arn is None:
        return [], "unavailable", "Logs unavailable for this resource - the load balancer could not be found."

    access_log_config = log_collector.discover_alb_access_log_config(lb_arn)
    if access_log_config is None:
        return [], "unavailable", "Logs unavailable for this resource - ALB access logging is not enabled for this load balancer."

    events = log_collector.fetch_alb_access_logs(
        access_log_config["bucket"], access_log_config["prefix"], REGION, window["start"], window["end"]
    )
    return events, "alb_access_logs", None


def _fetch_asg_events(resource_id: str, raw_context: Dict[str, Any], window: Dict[str, Any]):
    """ASG scaling activities are always a real, queryable source for any
    existing ASG (no "enabled" flag to check, unlike ALB access logs) -
    this is never reported "unavailable"; an ASG with no activity in the
    window legitimately returns zero events. Member-instance CloudWatch
    Logs are attempted additionally, bounded, and purely additive - their
    absence never downgrades the primary asg_scaling_activities source."""

    events = list(log_collector.discover_asg_scaling_activities(resource_id, window["start"], window["end"]))

    instances = ((raw_context.get("context") or {}).get("instances") or [])[:LOG_MAX_ASG_MEMBER_INSTANCES_SCANNED]
    for instance in instances:
        instance_id = instance.get("instance_id")
        if not instance_id:
            continue
        log_source_ref = log_collector.discover_ec2_log_source(instance_id)
        if log_source_ref is None:
            continue
        events.extend(
            log_collector.fetch_ec2_log_events(
                log_source_ref["log_group"], log_source_ref["log_stream"], window["start"], window["end"]
            )
        )

    return events, "asg_scaling_activities", None


def _parse_log_response(raw_response: str) -> Dict[str, Any]:
    """Parses Gemini's JSON response into the log-analysis schema. Never
    crashes on a malformed response - falls back to an honest message and
    marks parsed=False, mirroring api/follow_up_manager.py::_parse_response's
    same graceful-degradation style."""

    fallback_status = {"found": [], "not_found": [], "unavailable": [], "truncated": []}

    try:
        data = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError):
        return {
            "log_analysis_summary": (
                "The log evidence was gathered, but the AI could not produce a well-formed "
                "analysis for it. Please try again."
            ),
            "materially_changes_rca": False, "updated_root_cause": None, "updated_confidence": None,
            "evidence_citations": [], "uncertainty": ["Gemini's response could not be parsed."],
            "evidence_status": fallback_status, "parsed": False,
        }

    if not isinstance(data, dict) or not data.get("log_analysis_summary"):
        return {
            "log_analysis_summary": (
                "The log evidence was gathered, but the AI did not return a usable analysis. "
                "Please try again."
            ),
            "materially_changes_rca": False, "updated_root_cause": None, "updated_confidence": None,
            "evidence_citations": [], "uncertainty": ["Gemini's response was missing a summary."],
            "evidence_status": fallback_status, "parsed": False,
        }

    return {
        "log_analysis_summary": data["log_analysis_summary"],
        "materially_changes_rca": bool(data.get("materially_changes_rca", False)),
        "updated_root_cause": data.get("updated_root_cause"),
        "updated_confidence": data.get("updated_confidence"),
        "evidence_citations": data.get("evidence_citations") or [],
        "uncertainty": data.get("uncertainty") or [],
        "evidence_status": {**fallback_status, **(data.get("evidence_status") or {})},
        "parsed": True,
    }


class LogInvestigationManager:

    def investigate(self, investigation_id: str) -> Dict[str, Any]:

        run_id, resource_id = _split_investigation_id(investigation_id)

        report_path = REPORTS_DIR / f"{resource_id}.json"
        context_path = CONTEXT_DIR / f"{resource_id}.json"

        if not report_path.exists() or not context_path.exists():
            raise LogInvestigationNotFoundError(
                f"No investigation report found for resource '{resource_id}'. Run an "
                "investigation for this resource before investigating logs."
            )

        report = _read_json(report_path)
        raw_context = _read_json(context_path)

        if not report or not any(report.get(k) for k in ("summary", "root_cause", "severity")):
            raise LogInvestigationNotFoundError(
                f"Resource '{resource_id}' has no completed RCA yet - this investigation "
                "is not ready for log investigation."
            )

        # Same immutability/staleness cross-check as Follow-Up Q&A (see
        # api/follow_up_manager.py) - reused unchanged, not reimplemented.
        current_run_id = find_run_id_for(load_run_summaries(), mtime_dt(report_path))
        if current_run_id and current_run_id != run_id:
            raise LogInvestigationSupersededError(
                f"This report has been superseded by a newer investigation of "
                f"'{resource_id}' (run {current_run_id}). Reopen the current report to "
                "investigate logs."
            )

        resource_type = raw_context.get("resource_type")
        if resource_type not in _SUPPORTED_RESOURCE_TYPES:
            raise LogInvestigationUnsupportedResourceError(
                f"Log investigation is not available for resource type {resource_type!r}."
            )

        window = resolve_analysis_window(raw_context)

        if resource_type == "EC2":
            raw_events, log_source, unavailable_reason = _fetch_ec2_events(resource_id, window)
        elif resource_type == "Load Balancer":
            raw_events, log_source, unavailable_reason = _fetch_alb_events(resource_id, window)
        else:  # "Auto Scaling Group"
            raw_events, log_source, unavailable_reason = _fetch_asg_events(resource_id, raw_context, window)

        window_dict = {
            "start": window["start"].isoformat(),
            "end": window["end"].isoformat(),
            "confidence": window["confidence"],
        }

        if log_source == "unavailable":
            evidence_package = empty_evidence_package(resource_id, resource_type, "unavailable", unavailable_reason)
            evidence_package["requested_window"] = window_dict
        else:
            evidence_package = build_evidence_package(
                resource_id=resource_id, resource_type=resource_type, log_source=log_source,
                requested_window=window_dict, analyzed_window=window_dict,
                raw_events=raw_events, report=report,
            )

        sanitized_package = LogSanitizer().sanitize(evidence_package)

        prompt = LogPromptBuilder().build_prompt(
            report=report, sanitized_evidence_package=sanitized_package,
            resource_id=resource_id, resource_type=resource_type, run_id=run_id,
        )

        started = time.monotonic()
        try:
            raw_response = LLMEngine().analyze(prompt)
        except Exception as exc:
            logger.error(f"Log Investigation Gemini call failed for investigation_id={investigation_id}: {exc}")
            raise LogInvestigationUnavailableError(
                "The log evidence was gathered, but the AI log analysis is temporarily "
                "unavailable. Please try again."
            ) from exc
        gemini_latency_seconds = time.monotonic() - started

        analysis = _parse_log_response(raw_response)

        log_investigation_store.save_result(
            investigation_id=investigation_id, run_id=run_id, resource_id=resource_id,
            resource_type=resource_type, report_reference=str(report_path), context_reference=str(context_path),
            evidence_package=sanitized_package, analysis=analysis,
        )

        logger.info(
            f"log_investigation investigation_id={investigation_id} log_source={log_source} "
            f"total_events={evidence_package.get('total_events')} relevant_events={evidence_package.get('relevant_events')} "
            f"gemini_latency_seconds={gemini_latency_seconds:.2f} response_parsed={analysis['parsed']} "
            f"materially_changes_rca={analysis['materially_changes_rca']}"
        )

        return {
            "investigation_id": investigation_id,
            "resource_id": resource_id,
            "resource_type": resource_type,
            "evidence_package": sanitized_package,
            "analysis": analysis,
        }

    def get_results(self, investigation_id: str) -> Dict[str, Any]:
        _split_investigation_id(investigation_id)  # validates shape; raises if malformed
        result = log_investigation_store.load_result(investigation_id)
        if result is None:
            return {"investigation_id": investigation_id, "investigated": False}
        return {"investigation_id": investigation_id, "investigated": True, **result}

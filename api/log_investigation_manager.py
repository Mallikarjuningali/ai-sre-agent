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
from context.evidence_gap import assess_evidence_gap, build_log_investigation_plan
from context.log_evidence_builder import (
    build_evidence_package,
    empty_evidence_package,
    relevance_tokens_for_report,
    event_is_relevant,
)
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


def _relevant_count(events, tokens, tokens_by_source=None):
    """How many of these events match the SAME relevance tokens
    context/log_evidence_builder.py::build_evidence_package() will use for
    the merged reduction - reusing its own exported event_is_relevant(),
    never a second, parallel definition of relevance. Used only for
    per-source bookkeeping in `sources[]`, so this count stays consistent
    with the final evidence package's own relevant_events."""
    return sum(1 for e in events if event_is_relevant(e, tokens, tokens_by_source))


def _classify_source_type(source_name: Optional[str], candidate_keys) -> Optional[str]:
    """Matches a REAL discovered source identifier (e.g. an actual
    CloudWatch Logs group name) against the investigation plan's ABSTRACT
    source-type keys (e.g. "nginx_error", "nginx_access", "systemd_nginx")
    using real AWS log-group-naming conventions as a heuristic: splitting
    the abstract key into tokens ("nginx", "error") and checking whether
    ALL of those tokens appear (case-insensitive) in the real name.
    Returns the first matching key, or None if no key's tokens all appear
    - in which case the caller applies no investigation-specific filter
    for that source (generic relevance detection still applies), rather
    than guessing or applying every category's terms to every source."""

    if not source_name:
        return None
    haystack = source_name.lower()
    for key in candidate_keys:
        tokens = [t for t in key.lower().split("_") if t]
        if tokens and all(token in haystack for token in tokens):
            return key
    return None


def _merge_filters_by_source(plan: Dict[str, Any]) -> Dict[str, list]:
    """Unions every component's "filters_by_source" (see
    context/evidence_gap.py::build_log_investigation_plan) into one
    {source_type_name: [terms]} map - the actual investigation-specific
    filter set the plan hands to relevance filtering, keyed by the same
    abstract source-type names _classify_source_type() resolves discovered
    sources against."""

    merged: Dict[str, list] = {}
    for component in plan.get("components") or []:
        for source_type, terms in (component.get("filters_by_source") or {}).items():
            existing = merged.setdefault(source_type, [])
            for term in terms:
                if term not in existing:
                    existing.append(term)
    return merged


def _fetch_ec2_events(resource_id: str, window: Dict[str, Any], tokens, source_hints=None, filters_by_source=None):
    """Returns (raw_events, log_source, limitation_or_none, sources).
    source_hints (from context/evidence_gap.py) prioritizes which
    discovered log groups are investigated first - never changes WHETHER
    a source is found, only WHICH ones are preferred; up to
    LOG_MAX_EC2_SOURCES_PER_INVESTIGATION distinct sources may be
    investigated (see collector/logs.py::discover_ec2_log_sources).
    Each event's "source" is set to the investigation plan's own abstract
    source-type name (e.g. "nginx_error") when the discovered log group's
    real name can be confidently classified against it (see
    _classify_source_type) - this is what lets build_evidence_package()
    apply that source's own filters_by_source terms to exactly these
    events, never to unrelated sources."""

    discovered = log_collector.discover_ec2_log_sources(resource_id, source_hints=source_hints)
    filters_by_source = filters_by_source or {}

    if not discovered:
        reason = "Logs unavailable for this resource - no configured CloudWatch Logs stream was found for this instance."
        sources = [{"type": "cloudwatch_logs", "name": None, "status": "not_discoverable", "events_found": 0, "relevant_events": 0}]
        return [], "unavailable", reason, sources

    raw_events = []
    sources = []
    for candidate in discovered:
        events = log_collector.fetch_ec2_log_events(
            candidate["log_group"], candidate["log_stream"], window["start"], window["end"]
        )
        source_type = _classify_source_type(candidate["log_group"], filters_by_source.keys()) or candidate["log_group"]
        for event in events:
            event["source"] = source_type
        raw_events.extend(events)
        sources.append({
            "type": "cloudwatch_logs", "name": candidate["log_group"], "status": "available",
            "events_found": len(events), "relevant_events": _relevant_count(events, tokens, filters_by_source),
        })

    return raw_events, "cloudwatch_logs", None, sources


def _fetch_alb_events(resource_id: str, window: Dict[str, Any], tokens, filters_by_source=None):
    filters_by_source = filters_by_source or {}
    lb_arn = log_collector.resolve_alb_arn(resource_id)
    if lb_arn is None:
        reason = "Logs unavailable for this resource - the load balancer could not be found."
        return [], "unavailable", reason, [{"type": "alb_access_log", "name": resource_id, "status": "not_discoverable", "events_found": 0, "relevant_events": 0}]

    access_log_config = log_collector.discover_alb_access_log_config(lb_arn)
    if access_log_config is None:
        reason = "Logs unavailable for this resource - ALB access logging is not enabled for this load balancer."
        return [], "unavailable", reason, [{"type": "alb_access_log", "name": resource_id, "status": "not_configured", "events_found": 0, "relevant_events": 0}]

    events = log_collector.fetch_alb_access_logs(
        access_log_config["bucket"], access_log_config["prefix"], REGION, window["start"], window["end"]
    )
    for event in events:
        event["source"] = "alb_access_log"
    sources = [{
        "type": "alb_access_log", "name": access_log_config["bucket"], "status": "available",
        "events_found": len(events), "relevant_events": _relevant_count(events, tokens, filters_by_source),
    }]
    return events, "alb_access_logs", None, sources


def _fetch_asg_events(resource_id: str, raw_context: Dict[str, Any], window: Dict[str, Any], tokens, source_hints=None, filters_by_source=None):
    """ASG scaling activities are always a real, queryable source for any
    existing ASG (no "enabled" flag to check, unlike ALB access logs) -
    this is never reported "unavailable"; an ASG with no activity in the
    window legitimately returns zero events. Member-instance CloudWatch
    Logs are attempted additionally, bounded, and purely additive - their
    absence never downgrades the primary asg_scaling_activity source.
    source_hints prioritizes each member instance's log group the same
    way it does for a standalone EC2 investigation."""

    filters_by_source = filters_by_source or {}

    activity_events = list(log_collector.discover_asg_scaling_activities(resource_id, window["start"], window["end"]))
    for event in activity_events:
        event["source"] = "asg_scaling_activity"
    sources = [{
        "type": "asg_scaling_activity", "name": resource_id, "status": "available",
        "events_found": len(activity_events), "relevant_events": _relevant_count(activity_events, tokens, filters_by_source),
    }]

    events = list(activity_events)

    instances = ((raw_context.get("context") or {}).get("instances") or [])[:LOG_MAX_ASG_MEMBER_INSTANCES_SCANNED]
    for instance in instances:
        instance_id = instance.get("instance_id")
        if not instance_id:
            continue
        log_source_ref = log_collector.discover_ec2_log_source(instance_id, source_hints=source_hints)
        if log_source_ref is None:
            sources.append({
                "type": "cloudwatch_logs", "name": instance_id, "status": "not_discoverable",
                "events_found": 0, "relevant_events": 0,
            })
            continue
        member_events = log_collector.fetch_ec2_log_events(
            log_source_ref["log_group"], log_source_ref["log_stream"], window["start"], window["end"]
        )
        member_source_type = _classify_source_type(log_source_ref["log_group"], filters_by_source.keys()) or log_source_ref["log_group"]
        for event in member_events:
            event["source"] = member_source_type
        events.extend(member_events)
        sources.append({
            "type": "cloudwatch_logs", "name": log_source_ref["log_group"], "status": "available",
            "events_found": len(member_events), "relevant_events": _relevant_count(member_events, tokens, filters_by_source),
        })

    return events, "asg_scaling_activities", None, sources


def _parse_log_response(raw_response: str) -> Dict[str, Any]:
    """Parses Gemini's JSON response into the log-analysis schema. Never
    crashes on a malformed response - falls back to an honest message and
    marks parsed=False, mirroring api/follow_up_manager.py::_parse_response's
    same graceful-degradation style."""

    fallback_status = {"found": [], "not_found": [], "unavailable": [], "truncated": []}
    fallback_correlation = {"supports_existing_rca": False, "contradicts_existing_rca": False, "new_findings": [], "reasoning": ""}

    try:
        data = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError):
        return {
            "log_analysis_summary": (
                "The log evidence was gathered, but the AI could not produce a well-formed "
                "analysis for it. Please try again."
            ),
            "materially_changes_rca": False, "updated_root_cause": None, "updated_confidence": None,
            "established_by_logs": None,
            "evidence_citations": [], "uncertainty": ["Gemini's response could not be parsed."],
            "evidence_status": fallback_status, "correlation": fallback_correlation, "parsed": False,
        }

    if not isinstance(data, dict) or not data.get("log_analysis_summary"):
        return {
            "log_analysis_summary": (
                "The log evidence was gathered, but the AI did not return a usable analysis. "
                "Please try again."
            ),
            "materially_changes_rca": False, "updated_root_cause": None, "updated_confidence": None,
            "established_by_logs": None,
            "evidence_citations": [], "uncertainty": ["Gemini's response was missing a summary."],
            "evidence_status": fallback_status, "correlation": fallback_correlation, "parsed": False,
        }

    return {
        "log_analysis_summary": data["log_analysis_summary"],
        "materially_changes_rca": bool(data.get("materially_changes_rca", False)),
        "updated_root_cause": data.get("updated_root_cause"),
        "updated_confidence": data.get("updated_confidence"),
        "established_by_logs": data.get("established_by_logs"),
        "evidence_citations": data.get("evidence_citations") or [],
        "uncertainty": data.get("uncertainty") or [],
        "evidence_status": {**fallback_status, **(data.get("evidence_status") or {})},
        "correlation": {**fallback_correlation, **(data.get("correlation") or {})},
        "parsed": True,
    }


def _established_by_investigation(report: Dict[str, Any]) -> str:
    """Deterministic, code-computed echo of what the ORIGINAL metric-
    based investigation already established - assembled verbatim from the
    existing report (never regenerated, reinterpreted, or handed to
    Gemini to restate) - see llm/log_prompt_builder.py's own
    "EXISTING RCA" section, which is the prompt-side counterpart of this
    same fact."""
    if report.get("root_cause"):
        return str(report["root_cause"])
    if report.get("summary"):
        return str(report["summary"])
    return "The existing investigation did not record a root cause or summary."


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

        # Evidence-gap assessment: purely mechanical text matching against
        # the EXISTING RCA's own wording (see context/evidence_gap.py) -
        # never a metric threshold, never a root-cause conclusion. Used
        # only to (a) prioritize which discovered log source(s) to
        # investigate when a resource has more than one, and (b) tell
        # Gemini WHY this log evidence was fetched. "Investigate Logs"
        # remains a fully optional, user-triggered action regardless of
        # gap_detected - this assessment never blocks or skips the action
        # the user asked for, per the feature's own design.
        gap = assess_evidence_gap(report)
        plan = build_log_investigation_plan(report, resource_type)
        tokens, _categories = relevance_tokens_for_report(report)
        filters_by_source = _merge_filters_by_source(plan)

        # Fix 1: incident_time is a SINGLE timestamp derived from the
        # existing metric investigation's own data (see
        # utils/incident_window.py::derive_incident_time) - the bounded
        # window below is exactly incident_time +/- LOG_INCIDENT_WINDOW_
        # BEFORE/AFTER_MINUTES, never "earliest breach - buffer" through
        # "latest breach + buffer".
        window = resolve_analysis_window(raw_context)

        if resource_type == "EC2":
            raw_events, log_source, unavailable_reason, sources = _fetch_ec2_events(
                resource_id, window, tokens, source_hints=gap["source_hints"], filters_by_source=filters_by_source
            )
        elif resource_type == "Load Balancer":
            raw_events, log_source, unavailable_reason, sources = _fetch_alb_events(
                resource_id, window, tokens, filters_by_source=filters_by_source
            )
        else:  # "Auto Scaling Group"
            raw_events, log_source, unavailable_reason, sources = _fetch_asg_events(
                resource_id, raw_context, window, tokens, source_hints=gap["source_hints"], filters_by_source=filters_by_source
            )

        window_dict = {
            "incident_time": window["incident_time"].isoformat() if window["incident_time"] else None,
            "start": window["start"].isoformat(),
            "end": window["end"].isoformat(),
            "confidence": window["confidence"],
        }

        if log_source == "unavailable":
            evidence_package = empty_evidence_package(resource_id, resource_type, "unavailable", unavailable_reason)
            evidence_package["requested_window"] = window_dict
        else:
            # Fix 2: the investigation plan's own filters_by_source terms
            # (evidence_to_check, per source type) are unioned with the
            # existing generic relevance detection here - never replacing
            # it, and never applied to a source they don't belong to
            # (each event was tagged above only with the source type
            # _classify_source_type could confidently resolve it to).
            evidence_package = build_evidence_package(
                resource_id=resource_id, resource_type=resource_type, log_source=log_source,
                requested_window=window_dict, analyzed_window=window_dict,
                raw_events=raw_events, report=report,
                additional_tokens_by_source=filters_by_source,
            )

        sanitized_package = LogSanitizer().sanitize(evidence_package)

        prompt = LogPromptBuilder().build_prompt(
            report=report, sanitized_evidence_package=sanitized_package,
            resource_id=resource_id, resource_type=resource_type, run_id=run_id,
            evidence_gap=gap, investigation_plan=plan, sources=sources,
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
        # Additive only - utils/log_investigation_store.py needs no schema
        # change since analysis is already a free-form persisted dict.
        # established_by_investigation is deterministic (code-computed,
        # never Gemini-authored); evidence_gap is the same mechanical
        # assessment already threaded into the prompt above, surfaced here
        # too so the dashboard can show it without a second computation.
        analysis["established_by_investigation"] = _established_by_investigation(report)
        analysis["evidence_gap"] = gap

        log_investigation_store.save_result(
            investigation_id=investigation_id, run_id=run_id, resource_id=resource_id,
            resource_type=resource_type, report_reference=str(report_path), context_reference=str(context_path),
            evidence_package=sanitized_package, analysis=analysis,
            investigation_plan=plan, sources=sources, incident_window=window_dict,
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
            "investigation_plan": plan,
            "sources": sources,
            "incident_window": window_dict,
            "evidence_package": sanitized_package,
            "analysis": analysis,
        }

    def get_results(self, investigation_id: str) -> Dict[str, Any]:
        _split_investigation_id(investigation_id)  # validates shape; raises if malformed
        result = log_investigation_store.load_result(investigation_id)
        if result is None:
            return {"investigation_id": investigation_id, "investigated": False}
        return {"investigation_id": investigation_id, "investigated": True, **result}

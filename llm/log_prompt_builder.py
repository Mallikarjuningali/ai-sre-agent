"""
=========================================================
AI SRE AGENT
Module : Log Prompt Builder
Purpose:
    Build the Gemini prompt for the optional Log Investigation feature
    (see api/log_investigation_manager.py). Completely separate from
    llm/prompt_builder.py (original RCA), llm/cost_prompt_builder.py (Cost
    Explorer), and llm/follow_up_prompt_builder.py (Follow-Up Q&A) -
    different schema, different data, but the same five-part
    instructional structure (persona -> anti-markdown/anti-prose
    constraints -> exact schema block -> data-shape/absence-semantics
    explanation -> reasoning instructions) every prompt builder in this
    codebase already follows.

    Receives an ALREADY-SANITIZED Log Evidence Package - this module
    never sanitizes anything itself and never imports llm/sanitizer.py or
    llm/log_sanitizer.py. Sanitization happens exactly once, inside
    api/log_investigation_manager.py, before build_prompt() is ever
    called - see that module for the exact call order.

    Calls the existing llm/llm_engine.py::LLMEngine unchanged - this is
    the 4th caller of LLMEngine().analyze(prompt) in this codebase
    (alongside the RCA analyzer, Cost Explorer analyzer, and Follow-Up
    manager), with zero changes to that class.
=========================================================
"""

import json
from typing import Any, Dict, Optional


class LogPromptBuilder:

    def build_prompt(
        self,
        report: Dict[str, Any],
        sanitized_evidence_package: Dict[str, Any],
        resource_id: str,
        resource_type: Optional[str],
        run_id: str,
        evidence_gap: Optional[Dict[str, Any]] = None,
    ) -> str:

        evidence_gap = evidence_gap or {}

        prompt = f"""
You are the AegisOps SRE investigation assistant, performing a
SUPPLEMENTARY log analysis stage for an existing AWS infrastructure
investigation. A metrics-only Root Cause Analysis (RCA) has already been
generated for this resource - your job is to determine whether the
log evidence below adds to, confirms, or changes that existing
understanding. You are not starting a new investigation from scratch.

CRITICAL RULES - follow every one of these:
1. Log evidence below is SUPPLEMENTARY to the existing RCA's metrics/
   events - you must reason about it TOGETHER with the existing RCA
   (Root cause/Summary/Evidence below), never as a standalone analysis
   that ignores what was already established.
2. The absence of a log entry does NOT prove an event did not happen -
   log_source, total_events, relevant_events, and limitations below tell
   you exactly what was and wasn't actually collected; an event can be
   real and simply not have been captured by whatever was queried.
3. You MUST distinguish, in evidence_status: "found" (evidence actually
   present in patterns/timeline/representative_events below), "not_found"
   (a plausible signal you looked for but the evidence does not show),
   "unavailable" (log_source is "unavailable", or a specific log
   category was never queried), and "truncated" (anything named in
   limitations below - you did not see the complete dataset for that
   part).
4. Never claim certainty beyond what the evidence actually supports. If
   the evidence is insufficient, say so explicitly in log_analysis_summary
   rather than guessing.
5. materially_changes_rca must be true ONLY when the log evidence
   provides a genuinely new or contradicting signal versus the existing
   RCA - confirming the existing root cause with additional detail is
   NOT "materially changes" (leave it false, describe the confirmation in
   log_analysis_summary instead).
6. Cite the specific pattern/timeline/representative_events entry that
   supports each claim in evidence_citations - never cite something not
   actually present in the data below.
7. State any remaining uncertainty explicitly in "uncertainty" - do not
   imply the investigation is now complete if it isn't.
8. Never assume every possible log source for this resource was
   available - log_source below names exactly what WAS queried; anything
   not named was never attempted, not "checked and found clean".
9. patterns/representative_events below are a FILTERED, DEDUPLICATED,
   BOUNDED reduction of a much larger raw event volume (see total_events
   vs relevant_events, and limitations) - never treat this as the
   complete raw log stream, and never state a total occurrence count
   beyond what "count" in patterns actually says.
10. Do not invent AWS resources, metrics, timestamps, deployments, or
    events that do not appear in the RCA or evidence below.
11. The EVIDENCE GAP section below is a MECHANICAL text match against the
    existing RCA's own wording (never a root-cause conclusion) - use it
    only to understand WHY this particular log source was fetched. If
    gap_detected is false, the existing RCA did not signal any specific
    evidence gap - logs were still gathered because the user explicitly
    requested it; do not overstate their importance in that case.
12. analyzed_window below is the SAME incident window the original
    metric-based investigation already established (every log event was
    filtered to fall inside it) - temporal alignment between a log
    pattern's first_seen/last_seen and this window is expected by
    construction, not independent proof. Use it as supporting context for
    correlation, never as certainty by itself.

Return ONLY valid JSON. Do not include markdown. Do not wrap the JSON in
```.

Return this exact schema:

{{
    "log_analysis_summary": "",
    "materially_changes_rca": false,
    "updated_root_cause": null,
    "updated_confidence": null,
    "established_by_logs": "",
    "evidence_citations": [],
    "uncertainty": [],
    "evidence_status": {{
        "found": [],
        "not_found": [],
        "unavailable": [],
        "truncated": []
    }}
}}

Field notes:
- log_analysis_summary: prose summary of what the log evidence shows (or
  doesn't), combined with the existing RCA - this is what the dashboard
  shows as "AI Log Analysis".
- established_by_logs: state specifically what the NEW log evidence
  itself establishes (distinct from what the original metric-based
  investigation already established, shown to you below) - if the logs
  add nothing beyond confirming the existing RCA, say so plainly (e.g.
  "The log evidence is consistent with the existing RCA but does not
  establish a deeper cause"); if the evidence is insufficient to establish
  anything further, say exactly that rather than guessing.
- updated_root_cause / updated_confidence: ONLY populate these (both, not
  just one) when materially_changes_rca is true - this is your PROPOSED
  update, shown to the user as "Additional/Updated RCA"; it is never
  written back over the original RCA record automatically. Leave both
  null when materially_changes_rca is false.
- evidence_citations: short strings quoting/paraphrasing a specific
  pattern, timeline entry, or representative_events entry actually
  present below.
- evidence_status: every one of the four lists may be empty, but the keys
  must always be present.

INVESTIGATION
Resource: {resource_id}
Resource type: {resource_type or "unknown"}
Run: {run_id}

EXISTING RCA (already generated from metrics/events - do not restate this
as if it were new information; use it as context to interpret the log
evidence below. This is the ONLY metric/event information you receive -
the original metric dataset itself is deliberately not repeated here.)
Severity: {report.get("severity", "unknown")}
Confidence: {report.get("confidence", "unknown")}
Summary: {report.get("summary", "")}
Root cause: {report.get("root_cause", "")}
Evidence: {json.dumps(report.get("evidence") or [], separators=(",", ":"))}

EVIDENCE GAP (mechanically derived from the RCA text above - see rule 11)
gap_detected: {json.dumps(evidence_gap.get("gap_detected", False))}
categories: {json.dumps(evidence_gap.get("categories") or [])}
matched_signals: {json.dumps(evidence_gap.get("signals") or [])}

LOG EVIDENCE PACKAGE (already sanitized - IP addresses/hostnames/URLs/
credentials/tokens have been redacted; every remaining timestamp, status
code, exception type, port, count, and pattern is real)
log_source: {sanitized_evidence_package.get("log_source")}
requested_window: {json.dumps(sanitized_evidence_package.get("requested_window"), default=str)}
analyzed_window: {json.dumps(sanitized_evidence_package.get("analyzed_window"), default=str)}
relevance_categories_matched: {json.dumps(sanitized_evidence_package.get("relevance_categories_matched") or [])}
total_events: {sanitized_evidence_package.get("total_events")}
relevant_events: {sanitized_evidence_package.get("relevant_events")}
patterns: {json.dumps(sanitized_evidence_package.get("patterns") or [], separators=(",", ":"))}
timeline: {json.dumps(sanitized_evidence_package.get("timeline") or [], separators=(",", ":"))}
representative_events: {json.dumps(sanitized_evidence_package.get("representative_events") or [], separators=(",", ":"))}
limitations: {json.dumps(sanitized_evidence_package.get("limitations") or [])}
"""
        return prompt

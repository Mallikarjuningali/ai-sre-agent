"""
=========================================================
AI SRE AGENT
Module : Follow-Up Prompt Builder
Purpose:
    Build the evidence-grounded prompt for a Follow-Up Question about an
    existing infrastructure investigation. Completely separate from
    llm/prompt_builder.py (the original RCA prompt) and llm/cost_prompt_builder.py
    (Cost Explorer) - different schema, different data, different
    instructions - but reuses llm/sanitizer.py's existing Sanitizer
    unchanged, exactly as llm/prompt_builder.py already does, so no
    sensitive field (DNS names, VPC IDs, private IPs) ever reaches Gemini
    just because the question arrived through a different code path.

    Every fact placed in the prompt is either:
      - read verbatim from the original RCA report (severity/confidence/
        summary/root_cause/evidence/recommendations), or
      - read verbatim from the sanitized investigation context (the same
        MetricTrends {U,TH,H} / target group / scaling-activity shapes
        llm/prompt_builder.py already sends Gemini for the original RCA), or
      - a purely mechanical extraction over that same data (sort by
        timestamp, take first/last/min/max, truncate to a bounded count).

    Nothing here performs RCA-style interpretation (no "if cpu > 90:
    ..."-shaped logic anywhere) - every conclusion is left to Gemini,
    grounded in the facts assembled below.
=========================================================
"""

import json
from typing import Any, Dict, List, Optional

from llm.sanitizer import Sanitizer
from config.settings import FOLLOW_UP_TIMELINE_MAX_EVENTS

# Same field-minimization philosophy llm/sanitizer.py::sanitize_cloudtrail
# already applies (event_name/service/error_code, dropping username/
# source_ip/region/user_agent) - this module additionally keeps
# event_time, which no sanitizer in this codebase treats as sensitive
# (MetricTrends' own H arrays already carry timestamps straight through
# every existing sanitizer unmodified). This is a parallel, narrower
# extraction for timeline purposes only - llm/sanitizer.py itself is
# never modified, and the general EVIDENCE section below still goes
# through it completely unchanged.
_CLOUDTRAIL_TIMELINE_FIELDS = ("event_time", "event_name", "service", "error_code")

# (issue #5) _TREND_METRIC_LABELS was removed along with the metric_extremes
# TIMELINE subsection it fed - that section duplicated data already in
# EVIDENCE; the label map is no longer referenced anywhere.


class FollowUpPromptBuilder:

    def __init__(self):
        self.sanitizer = Sanitizer()

    # =====================================================
    # Deterministic timeline - mechanical extraction only, never
    # interpretation. See module docstring.
    # =====================================================

    def _cloudtrail_timeline(self, raw_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Sorted-by-time CloudTrail events, bounded to
        FOLLOW_UP_TIMELINE_MAX_EVENTS - only present for EC2 resources;
        first-class Load Balancer/Auto Scaling Group contexts never carry
        a "cloudtrail" key at all (see context/context_builder.py's
        promotion logic), so this returns [] for them, honestly, rather
        than fabricating an entry."""
        events = (raw_context.get("context") or {}).get("cloudtrail") or []

        extracted = []
        for event in events:
            entry = {field: event.get(field) for field in _CLOUDTRAIL_TIMELINE_FIELDS}
            if entry.get("event_time"):
                extracted.append(entry)

        extracted.sort(key=lambda e: e["event_time"])

        if len(extracted) > FOLLOW_UP_TIMELINE_MAX_EVENTS:
            # Keep the most recent N - the events closest to "now" are
            # the ones most likely relevant to a follow-up question about
            # this investigation; older ones are dropped, not summarized
            # into something that wasn't actually observed.
            extracted = extracted[-FOLLOW_UP_TIMELINE_MAX_EVENTS:]

        return extracted

    # NOTE (issue #5): the old "Metric Extremes" TIMELINE subsection is
    # REMOVED. It derived min/max/first/last directly from the very same
    # sanitized context that already ships verbatim in the EVIDENCE
    # section below, so it was pure token duplication on every single
    # question. CloudTrail events below are NOT duplicated (the sanitized
    # context strips event_time, which _cloudtrail_timeline re-adds for
    # reasoning), so the CloudTrail half of TIMELINE stays.
    #
    # =====================================================
    # Log Investigation awareness - reads an ALREADY-PERSISTED,
    # ALREADY-SANITIZED result (see utils/log_investigation_store.py /
    # api/log_investigation_manager.py) - never re-fetches logs, never
    # re-sanitizes, never calls AWS. Purely a compact summary of data
    # that was already reduced/sanitized before this follow-up question
    # was ever asked.
    # =====================================================

    @staticmethod
    def _log_investigation_summary(log_investigation: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """None when Log Investigation was never performed for this
        investigation - the caller/prompt must say so explicitly (state A
        from the follow-up spec), never silently omit the section."""
        if not log_investigation:
            return {"performed": False}

        evidence_package = log_investigation.get("evidence_package") or {}
        analysis = log_investigation.get("analysis") or {}

        return {
            "performed": True,
            "investigation_plan": log_investigation.get("investigation_plan"),
            "sources": log_investigation.get("sources") or [],
            "incident_window": log_investigation.get("incident_window"),
            "log_source_overall_status": evidence_package.get("log_source"),
            "total_events": evidence_package.get("total_events"),
            "relevant_events": evidence_package.get("relevant_events"),
            "patterns": evidence_package.get("patterns") or [],
            "timeline": evidence_package.get("timeline") or [],
            "limitations": evidence_package.get("limitations") or [],
            "log_analysis_summary": analysis.get("log_analysis_summary"),
            "established_by_logs": analysis.get("established_by_logs"),
            "correlation": analysis.get("correlation"),
        }

    # =====================================================
    # Prompt assembly
    # =====================================================

    def build_evidence_block(
        self,
        report: Dict[str, Any],
        raw_context: Dict[str, Any],
        run_id: str,
        resource_id: str,
        resource_type: Optional[str],
        time_window: Optional[Dict[str, Optional[str]]],
        log_investigation: Optional[Dict[str, Any]] = None,
    ) -> str:
        """The IMMUTABLE-per-investigation portion of the follow-up prompt:
        grounding instructions + the RCA + the sanitized evidence + the
        CloudTrail timeline. Contains NO conversation history and NO user
        question, so it can be built/sanitized ONCE per investigation and
        reused for every follow-up question (issue #5) - and, on a paid
        Google AI tier, handed to Gemini's cached_contents so the evidence
        tokens are paid once, not once per question.

        call sanitize() exactly once here. The per-question prompt
        (build_turn_prompt) reuses this block verbatim and never
        re-sanitizes."""

        sanitized_context = self.sanitizer.sanitize(raw_context)
        log_investigation_summary = self._log_investigation_summary(log_investigation)

        # Only cloudtrail_events now - the metric_extremes half duplicated
        # data already in sanitized_context (see the NOTE above).
        timeline = {
            "cloudtrail_events": self._cloudtrail_timeline(raw_context),
        }

        return f"""
You are the AegisOps SRE investigation assistant.

You are answering a follow-up question about an existing AWS
infrastructure investigation. Use ONLY the investigation evidence and
context provided below. Do not invent metrics, timestamps, AWS resources,
events, deployment changes, or root causes. Do not answer from general
AWS knowledge unless the user's question is explicitly not about this
investigation's evidence - in that case, say so plainly and keep the
general-knowledge answer brief, clearly separated from anything the
investigation actually established.

Clearly distinguish, in your reasoning and in "answer":
1. Observed evidence - directly present in the data below.
2. Strong inference - a conclusion two or more independent signals in the
   data support.
3. Possible hypothesis - plausible but not confirmed by the supplied data.

If the available evidence is insufficient to answer with confidence, say
so explicitly (e.g. "I cannot conclusively determine that from the
available investigation evidence.") rather than guessing. Do not claim
certainty when evidence is incomplete or absent - for example, if no
database telemetry was collected, do not answer a database question as if
it had been.

LOG INVESTIGATION section below (if present) is a SEPARATE, optional,
later stage the user may or may not have triggered for this same
investigation. You MUST distinguish exactly these four cases and never
confuse them:
A. performed is false -> Log Investigation was never run for this
   investigation. Say so plainly (e.g. "Log investigation has not been
   performed for this incident.") - do not guess what logs might show.
B. performed is true AND patterns/timeline/relevant_events show real
   findings -> answer using that actual evidence, and say whether it
   supports, contradicts, or is inconclusive relative to the RCA (see
   "correlation" if present).
C. performed is true AND a source's status is "unavailable",
   "not_configured", or "not_discoverable" -> say the log investigation
   was performed but the required log source was not available/
   discoverable - never say "no errors were found" for a source that was
   never actually queried.
D. performed is true AND relevant_events is 0 for a source that WAS
   queried (status "available") -> say the available log source was
   checked during the incident window but no relevant events matching the
   investigation criteria were found - this is a different fact from case
   C and must never be phrased the same way.

If asked what time period/window was checked, answer using
LOG INVESTIGATION's own "incident_window" (start/end), and when its
"incident_time" is present, phrase the answer as "<start>-<end>, based on
the <incident_time> incident time" (e.g. "10:15-10:45, based on the 10:30
incident time.") - never invent a different time period, and never
describe it as a wider range than incident_window actually states.

Answer the user's question directly first, then provide the supporting
evidence. Keep the response concise but technically useful.

Return ONLY valid JSON. Do not include markdown. Do not wrap the JSON in
```.

Return this exact schema:

{{
    "answer": "",
    "confidence": "HIGH|MEDIUM|LOW",
    "evidence_used": [
        {{"source": "", "signal": "", "observation": "", "timestamp": ""}}
    ],
    "uncertainties": [],
    "follow_up_needed": false
}}

confidence must reflect evidence quality, not be picked from a fixed
numeric rule:
- HIGH: multiple independent signals in the evidence below support the
  same conclusion.
- MEDIUM: the evidence supports a likely explanation but a plausible
  alternative remains.
- LOW: limited evidence, or signals that conflict.

evidence_used should cite only facts that actually appear in TIMELINE or
EVIDENCE below - source is e.g. "CloudWatch"/"CloudTrail"/"ALB", signal is
the metric/event name, timestamp only when the underlying data has one
(omit rather than invent one for a fact with no timestamp).

INVESTIGATION
Resource: {resource_id}
Resource type: {resource_type or "unknown"}
Run: {run_id}
Time window: {json.dumps(time_window) if time_window else "not available"}

RCA
Severity: {report.get("severity", "unknown")}
Confidence: {report.get("confidence", "unknown")}
Summary: {report.get("summary", "")}
Root cause: {report.get("root_cause", "")}
Original evidence: {json.dumps(report.get("evidence") or [], separators=(",", ":"))}
Original recommendations: {json.dumps(report.get("recommendations") or [], separators=(",", ":"))}

LOG INVESTIGATION (see rule above - "performed": false means this stage was
never run for this investigation; do not treat its absence as evidence of
anything about the incident itself)
{json.dumps(log_investigation_summary, separators=(",", ":"), default=str)}

TIMELINE
{json.dumps(timeline, separators=(",", ":"))}

EVIDENCE
{json.dumps(sanitized_context.get("context") or {}, separators=(",", ":"))}
"""

    def build_turn_prompt(
        self,
        conversation_history: List[Dict[str, Any]],
        question: str,
    ) -> str:
        """The per-question portion: only the recent conversation and the
        question itself. Sent alongside the evidence block either as the
        mutable contents of a cached-content call (the block is the cache)
        or appended after it (see build_prompt). Never re-sanitizes and
        never re-reads the investigation - it only formats what is already
        in memory."""

        conversation_block = [
            {"role": turn.get("role"), "content": turn.get("content")}
            for turn in conversation_history
            if turn.get("role") and turn.get("content")
        ]

        return f"""
CONVERSATION
{json.dumps(conversation_block, separators=(",", ":"))}

USER QUESTION
{question}
"""

    def build_prompt(
        self,
        report: Dict[str, Any],
        raw_context: Dict[str, Any],
        run_id: str,
        resource_id: str,
        resource_type: Optional[str],
        time_window: Optional[Dict[str, Optional[str]]],
        conversation_history: List[Dict[str, Any]],
        question: str,
        log_investigation: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Single-string prompt for the UNCACHED fallback path. Equals
        build_evidence_block(...) + build_turn_prompt(...) so the uncached
        and cached paths send Gemini byte-identical content (minus the
        removed metric_extremes duplication) - a cached and uncached answer
        for the same question are grounded in exactly the same evidence."""

        return self.build_evidence_block(
            report=report,
            raw_context=raw_context,
            run_id=run_id,
            resource_id=resource_id,
            resource_type=resource_type,
            time_window=time_window,
            log_investigation=log_investigation,
        ) + self.build_turn_prompt(conversation_history, question)

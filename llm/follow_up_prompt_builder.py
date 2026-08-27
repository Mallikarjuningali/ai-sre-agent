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

# prompt_key -> the human label used in the TIMELINE's "Metric Extremes"
# section, for every trend-shaped metric any resource type's sanitized
# context might carry (EC2, first-class ALB, first-class ASG - see
# llm/sanitizer.py's sanitize_cloudwatch/sanitize_load_balancer/
# sanitize_auto_scaling_group for the exact source of each).
_TREND_METRIC_LABELS = {
    "cpu": "CPU Utilization",
    "memory": "Memory Utilization",
    "disk": "Disk Utilization",
    "network_in": "Network In",
    "network_out": "Network Out",
    "request_count": "Request Count",
    "target_response_time": "Target Response Time",
    "http_4xx": "HTTP 4XX Count",
    "http_5xx": "HTTP 5XX Count",
    "desired_capacity": "Desired Capacity",
    "in_service_instances": "In-Service Instances",
    "pending_instances": "Pending Instances",
}


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

    @staticmethod
    def _metric_extremes(trend: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """First/last/min/max of one metric's H array, plus its unit and
        (only when AWS actually has one configured) alarm threshold -
        every value read directly off H, nothing computed beyond
        min()/max()/indexing."""
        history = trend.get("H") or []
        if not history:
            return None

        values = [point[1] for point in history if isinstance(point, (list, tuple)) and len(point) == 2]
        if not values:
            return None

        min_point = min(history, key=lambda p: p[1])
        max_point = max(history, key=lambda p: p[1])

        result = {
            "unit": trend.get("U"),
            "first": {"timestamp": history[0][0], "value": history[0][1]},
            "last": {"timestamp": history[-1][0], "value": history[-1][1]},
            "min": {"timestamp": min_point[0], "value": min_point[1]},
            "max": {"timestamp": max_point[0], "value": max_point[1]},
        }

        threshold = trend.get("TH")
        if threshold:
            result["configured_alarm_threshold"] = threshold

        return result

    def _metric_timeline(self, sanitized_context: Dict[str, Any]) -> Dict[str, Any]:
        """Extremes for every trend-shaped metric present in the
        sanitized context, regardless of resource type - EC2's
        "cloudwatch" sub-block, or a first-class Load Balancer/Auto
        Scaling Group context's own top-level metric keys."""
        data = sanitized_context.get("context") or {}

        candidates: Dict[str, Any] = {}
        cloudwatch = data.get("cloudwatch")
        if isinstance(cloudwatch, dict):
            candidates.update({k: v for k, v in cloudwatch.items() if isinstance(v, dict) and "H" in v})

        metrics = data.get("metrics")
        if isinstance(metrics, dict):
            candidates.update({k: v for k, v in metrics.items() if isinstance(v, dict) and "H" in v})

        for key in ("desired_capacity", "in_service_instances", "pending_instances"):
            value = data.get(key)
            if isinstance(value, dict) and "H" in value:
                candidates[key] = value

        extremes = {}
        for prompt_key, trend in candidates.items():
            summary = self._metric_extremes(trend)
            if summary:
                extremes[_TREND_METRIC_LABELS.get(prompt_key, prompt_key)] = summary

        return extremes

    # =====================================================
    # Prompt assembly
    # =====================================================

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
    ) -> str:

        sanitized_context = self.sanitizer.sanitize(raw_context)

        timeline = {
            "cloudtrail_events": self._cloudtrail_timeline(raw_context),
            "metric_extremes": self._metric_timeline(sanitized_context),
        }

        conversation_block = [
            {"role": turn.get("role"), "content": turn.get("content")}
            for turn in conversation_history
            if turn.get("role") and turn.get("content")
        ]

        prompt = f"""
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

TIMELINE
{json.dumps(timeline, separators=(",", ":"))}

EVIDENCE
{json.dumps(sanitized_context.get("context") or {}, separators=(",", ":"))}

CONVERSATION
{json.dumps(conversation_block, separators=(",", ":"))}

USER QUESTION
{question}
"""
        return prompt

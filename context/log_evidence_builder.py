"""
=========================================================
AI SRE AGENT
Module : Log Evidence Builder
Purpose:
    Deterministic, local reduction pipeline for the optional Log
    Investigation feature (see api/log_investigation_manager.py):

        raw log/event lines
        -> time filtering
        -> relevance filtering
        -> normalization
        -> deduplication / pattern aggregation
        -> timeline construction
        -> representative sampling
        -> Log Evidence Package

    Pure Python only - never calls AWS, never calls Gemini, never imports
    llm/sanitizer.py or the new llm/log_sanitizer.py (sanitization happens
    one step later, on the package this module returns, entirely inside
    api/log_investigation_manager.py).

    Every function below only organizes/reduces evidence - it never
    decides a root cause. The one place this module makes a judgment call
    at all is _select_relevance_categories(), and even that only decides
    which log LINES to keep, using a small, documented keyword table
    matched against the EXISTING report's own text - it never returns or
    implies a root-cause conclusion. Root-cause interpretation remains
    entirely with Gemini (see llm/log_prompt_builder.py).
=========================================================
"""

import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import json

from config.settings import (
    LOG_MAX_RAW_EVENTS,
    LOG_MAX_RELEVANT_EVENTS,
    LOG_MAX_PATTERNS,
    LOG_MAX_EXAMPLES_PER_PATTERN,
    LOG_MAX_REPRESENTATIVE_EVENTS,
    LOG_MAX_TIMELINE_EVENTS,
    LOG_MAX_EVIDENCE_PACKAGE_BYTES,
)

# =========================================================
# Dynamic relevance filtering - evidence FILTERING, not root-cause
# DETERMINATION. Each category is a documented, extensible set of tokens;
# a category's tokens only ever decide which log lines survive the filter
# below. Whether a category's tokens actually matched the existing
# report's text is recorded in the Log Evidence Package (see
# "relevance_categories_matched") so this is auditable, never a silent
# decision. This is NOT an RCA decision tree - it never assigns a root
# cause, confidence, or severity; Gemini alone interprets what survives.
# =========================================================

_RELEVANCE_CATEGORIES: Dict[str, List[str]] = {
    "generic_error": [
        "error", "exception", "fail", "failed", "failure", "fatal", "panic", "traceback",
    ],
    "http_5xx": [
        "5xx", "500", "502", "503", "504", "timeout", "upstream", "connection", "application exception",
        "bad gateway", "service unavailable", "gateway timeout",
    ],
    "memory_pressure": [
        "oom", "out of memory", "outofmemory", "killed", "memory", "exit code 137", "cannot allocate memory",
    ],
    "database": [
        "connection refused", "connection timeout", "database", "pool exhausted", "sql", "deadlock",
        "too many connections", "db connection",
    ],
    "disk_pressure": [
        "no space left", "disk full", "disk usage", "enospc",
    ],
    "network": [
        "dns", "network unreachable", "connection reset", "econnreset", "packet loss",
    ],
}


def _select_relevance_categories(report: Dict[str, Any]) -> Dict[str, List[str]]:
    """Returns {category: tokens} for "generic_error" (always included -
    a baseline every investigation gets, regardless of RCA text) plus any
    other category whose own tokens appear (case-insensitive substring
    match) in the existing report's root_cause/summary/evidence text."""

    haystack_parts = [
        str(report.get("root_cause") or ""),
        str(report.get("summary") or ""),
    ]
    haystack_parts.extend(str(item) for item in (report.get("evidence") or []))
    haystack = " ".join(haystack_parts).lower()

    selected = {"generic_error": _RELEVANCE_CATEGORIES["generic_error"]}

    for category, tokens in _RELEVANCE_CATEGORIES.items():
        if category == "generic_error":
            continue
        if any(token in haystack for token in tokens):
            selected[category] = tokens

    return selected


def relevance_tokens_for_report(report: Dict[str, Any]) -> List[str]:
    """The flattened, deduplicated token list actually used to filter log
    lines, plus the category names that produced it (for
    "relevance_categories_matched" in the final package)."""
    categories = _select_relevance_categories(report)
    tokens = sorted({token for tokens in categories.values() for token in tokens})
    return tokens, sorted(categories.keys())


# =========================================================
# Pipeline stage 1 - time filtering
# =========================================================

def filter_by_time(events: List[Dict[str, Any]], window: Dict[str, datetime]) -> List[Dict[str, Any]]:
    """Keeps only events within [window["start"], window["end"]], bounded
    defensively to LOG_MAX_RAW_EVENTS even though callers (collector/logs.py)
    already bound their own fetch - a second, independent bound."""

    start, end = window["start"], window["end"]
    filtered = [e for e in events if e.get("timestamp") and start <= e["timestamp"] <= end]
    filtered.sort(key=lambda e: e["timestamp"])
    return filtered[:LOG_MAX_RAW_EVENTS]


# =========================================================
# Pipeline stage 2 - relevance filtering
# =========================================================

def event_is_relevant(
    event: Dict[str, Any],
    tokens: List[str],
    tokens_by_source: Optional[Dict[str, List[str]]] = None,
) -> bool:
    """A single event is relevant when EITHER the generic, report-driven
    tokens match its message, OR - when this event carries a "source" key
    and that source has its own investigation-specific filter terms in
    tokens_by_source (see context/evidence_gap.py's per-component
    "filters_by_source") - one of THAT source's own terms matches. Both
    checks are case-insensitive substring matches; neither ever replaces
    the other, matching the required "generic OR investigation-specific"
    relevance model."""

    message = event.get("message", "").lower()

    if any(token in message for token in tokens):
        return True

    if tokens_by_source:
        source_tokens = tokens_by_source.get(event.get("source"))
        if source_tokens and any(token in message for token in source_tokens):
            return True

    return False


def filter_by_relevance(
    events: List[Dict[str, Any]],
    tokens: List[str],
    tokens_by_source: Optional[Dict[str, List[str]]] = None,
) -> List[Dict[str, Any]]:
    """Keeps only events whose message contains at least one relevance
    token (case-insensitive substring match) - a mechanical text filter,
    not a root-cause judgment. `tokens` is the generic, report-driven set
    (always applied); `tokens_by_source`, if given, is an ADDITIONAL,
    per-source-type set of investigation-plan-derived terms (see
    context/evidence_gap.py::build_log_investigation_plan's
    "filters_by_source") applied only to events whose own "source" key
    matches - an event is kept if EITHER check matches, never only the
    investigation-specific one (the generic detection is never removed).
    Bounded to LOG_MAX_RELEVANT_EVENTS via even sampling across the full
    ordered match list (never biased toward only the earliest or only the
    latest matches)."""

    matched = [e for e in events if event_is_relevant(e, tokens, tokens_by_source)]
    return _bounded_sample(matched, LOG_MAX_RELEVANT_EVENTS)


# =========================================================
# Pipeline stage 3 - normalization
# =========================================================

_IPV4_RE = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_HEX_ID_RE = re.compile(r"\b[0-9a-fA-F]{12,}\b")
_ISO_TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\b")
_TIME_ONLY_RE = re.compile(r"\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b")
_NUMBER_RE = re.compile(r"\b\d{4,}\b")


def normalize_message(message: str) -> str:
    """Templates dynamic values (IPs, UUIDs, hex ids, timestamps, long
    numbers) into placeholders so repeated occurrences of the same
    underlying error group into one pattern instead of thousands of
    unique strings. Purely mechanical regex substitution - never
    interprets what the message means."""

    normalized = message
    normalized = _ISO_TIMESTAMP_RE.sub("<TIMESTAMP>", normalized)
    normalized = _TIME_ONLY_RE.sub("<TIME>", normalized)
    normalized = _IPV4_RE.sub("<IP>", normalized)
    normalized = _UUID_RE.sub("<ID>", normalized)
    normalized = _HEX_ID_RE.sub("<ID>", normalized)
    normalized = _NUMBER_RE.sub("<NUM>", normalized)
    return normalized.strip()


# =========================================================
# Pipeline stage 4 - deduplication / pattern aggregation
# =========================================================

def _representative_examples(events_sorted: List[Dict[str, Any]], cap: int) -> List[str]:
    """First-few + a few representative-middle + last-few raw messages
    for one pattern group, bounded to `cap` total - preserves real
    original event text (never the normalized pattern) so a human/Gemini
    can see what actually happened, without ever sending every
    occurrence."""

    if len(events_sorted) <= cap:
        return [e["message"] for e in events_sorted]

    sampled = _bounded_sample(events_sorted, cap)
    return [e["message"] for e in sampled]


def deduplicate_and_aggregate(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Groups events by normalized pattern. Returns pattern dicts sorted
    by occurrence count (descending) - the caller is responsible for
    capping to LOG_MAX_PATTERNS and recording truncation, since this
    function has no access to the package's own "limitations" list."""

    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        pattern = normalize_message(event.get("message", ""))
        groups[pattern].append(event)

    patterns = []
    for pattern, group_events in groups.items():
        group_events.sort(key=lambda e: e["timestamp"])
        patterns.append({
            "pattern": pattern,
            "count": len(group_events),
            "first_seen": group_events[0]["timestamp"].isoformat(),
            "last_seen": group_events[-1]["timestamp"].isoformat(),
            "examples": _representative_examples(group_events, LOG_MAX_EXAMPLES_PER_PATTERN),
        })

    patterns.sort(key=lambda p: p["count"], reverse=True)
    return patterns


# =========================================================
# Pipeline stage 5 - timeline construction
# =========================================================

def build_timeline(patterns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One chronological entry per pattern's FIRST real occurrence -
    derived only from actual observed evidence (patterns' own first_seen),
    never an invented event. Sorted chronologically, bounded to
    LOG_MAX_TIMELINE_EVENTS (earliest entries kept, since a truncated
    timeline should still tell the story of how the incident began)."""

    timeline = [
        {
            "timestamp": pattern["first_seen"],
            "description": f'{pattern["pattern"]} (first of {pattern["count"]} occurrences)',
        }
        for pattern in patterns
    ]
    timeline.sort(key=lambda entry: entry["timestamp"])
    return timeline[:LOG_MAX_TIMELINE_EVENTS]


# =========================================================
# Pipeline stage 6 - representative sampling (global)
# =========================================================

def sample_representative_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A small, bounded, chronologically-even global sample of the
    relevant (post-filter) events - distinct from each pattern's own
    per-pattern examples above. Gives Gemini a handful of real,
    timestamped raw-shaped examples to ground its answer."""

    sampled = _bounded_sample(events, LOG_MAX_REPRESENTATIVE_EVENTS)
    return [{"timestamp": e["timestamp"].isoformat(), "message": e["message"]} for e in sampled]


# =========================================================
# Shared helper - even sampling (never biased to only one end)
# =========================================================

def _bounded_sample(items: List[Any], cap: int) -> List[Any]:
    """Returns up to `cap` items evenly spread across the full ordered
    list (first, last, and evenly-spaced items between) - never just the
    first N or just the last N, so a bounded sample still represents the
    whole window, not only its beginning or only its end."""

    if len(items) <= cap or cap <= 0:
        return list(items)[:cap] if cap > 0 else []

    if cap == 1:
        return [items[0]]

    indices = sorted({round(i * (len(items) - 1) / (cap - 1)) for i in range(cap)})
    return [items[i] for i in indices]


# =========================================================
# Final assembly
# =========================================================

def build_evidence_package(
    resource_id: str,
    resource_type: Optional[str],
    log_source: str,
    requested_window: Dict[str, Any],
    analyzed_window: Dict[str, Any],
    raw_events: List[Dict[str, Any]],
    report: Dict[str, Any],
    additional_tokens: Optional[List[str]] = None,
    additional_tokens_by_source: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """Runs the full pipeline and assembles the Log Evidence Package.
    `requested_window`/`analyzed_window` are both
    {"start": iso_str, "end": iso_str, "confidence": "derived"|"inferred"} -
    identical unless a source-specific constraint narrowed what was
    actually queried (mirrors the Cost Anomaly Detection feature's own
    requested-vs-analyzed distinction for a different bounded-window
    case).

    `additional_tokens`/`additional_tokens_by_source` are optional,
    investigation-plan-derived terms (see
    context/evidence_gap.py::build_log_investigation_plan's
    "evidence_to_check"/"filters_by_source") - when given, they are UNIONED
    with (never replace) the existing generic, report-driven relevance
    tokens, so the plan actually drives what gets kept without weakening
    the pre-existing generic detection. `total_events` still reflects every
    raw event fetched, before either relevance check - only
    `relevant_events` and everything derived from it is affected."""

    limitations: List[str] = []

    total_events = len(raw_events)

    time_window_for_filter = {
        "start": datetime.fromisoformat(analyzed_window["start"]) if isinstance(analyzed_window["start"], str) else analyzed_window["start"],
        "end": datetime.fromisoformat(analyzed_window["end"]) if isinstance(analyzed_window["end"], str) else analyzed_window["end"],
    }
    time_filtered = filter_by_time(raw_events, time_window_for_filter)

    if total_events >= LOG_MAX_RAW_EVENTS:
        limitations.append(
            "Evidence was truncated because the configured investigation limit "
            f"on raw events ({LOG_MAX_RAW_EVENTS}) was reached."
        )

    report_tokens, categories_matched = relevance_tokens_for_report(report)
    plan_tokens = sorted({token.lower() for token in (additional_tokens or [])})
    tokens = sorted(set(report_tokens) | set(plan_tokens))
    tokens_by_source = None
    if additional_tokens_by_source:
        tokens_by_source = {
            source: sorted({token.lower() for token in terms})
            for source, terms in additional_tokens_by_source.items()
        }

    relevant_events = filter_by_relevance(time_filtered, tokens, tokens_by_source)

    relevant_before_cap = len([e for e in time_filtered if event_is_relevant(e, tokens, tokens_by_source)])
    if relevant_before_cap > LOG_MAX_RELEVANT_EVENTS:
        limitations.append(
            "Evidence was truncated because the configured investigation limit "
            f"on relevant events ({LOG_MAX_RELEVANT_EVENTS}) was reached."
        )

    patterns = deduplicate_and_aggregate(relevant_events)
    if len(patterns) > LOG_MAX_PATTERNS:
        limitations.append(
            "Evidence was truncated because the configured investigation limit "
            f"on distinct patterns ({LOG_MAX_PATTERNS}) was reached; the lowest-"
            "occurrence patterns were dropped first."
        )
        patterns = patterns[:LOG_MAX_PATTERNS]

    timeline = build_timeline(patterns)
    representative_events = sample_representative_events(relevant_events)

    package = {
        "resource_id": resource_id,
        "resource_type": resource_type,
        "log_source": log_source,
        "requested_window": requested_window,
        "analyzed_window": analyzed_window,
        "relevance_categories_matched": categories_matched,
        "total_events": total_events,
        "relevant_events": len(relevant_events),
        "patterns": patterns,
        "timeline": timeline,
        "representative_events": representative_events,
        "limitations": limitations,
    }

    return enforce_byte_budget(package)


def enforce_byte_budget(package: Dict[str, Any]) -> Dict[str, Any]:
    """Final hard cap on the serialized package's size, applied after
    every earlier bound - a last safety net, not the primary control.
    Drops the lowest-count patterns first (they're the least informative),
    then trims representative_events, until the package fits within
    LOG_MAX_EVIDENCE_PACKAGE_BYTES - it never silently ships an oversized
    package; every drop is recorded in limitations."""

    def _size(pkg: Dict[str, Any]) -> int:
        return len(json.dumps(pkg, default=str).encode("utf-8"))

    if _size(package) <= LOG_MAX_EVIDENCE_PACKAGE_BYTES:
        return package

    truncated_note = (
        "Evidence was truncated because the configured investigation limit "
        f"on evidence package size ({LOG_MAX_EVIDENCE_PACKAGE_BYTES} bytes) was reached."
    )

    patterns = list(package["patterns"])
    while patterns and _size(package) > LOG_MAX_EVIDENCE_PACKAGE_BYTES:
        patterns.pop()  # already sorted by count descending - drop the least frequent
        package = {**package, "patterns": patterns}

    representative_events = list(package["representative_events"])
    while representative_events and _size(package) > LOG_MAX_EVIDENCE_PACKAGE_BYTES:
        representative_events.pop()
        package = {**package, "representative_events": representative_events}

    if truncated_note not in package["limitations"]:
        package = {**package, "limitations": package["limitations"] + [truncated_note]}

    return package


def empty_evidence_package(resource_id: str, resource_type: Optional[str], log_source: str, reason: str) -> Dict[str, Any]:
    """The honest "logs unavailable" package - log_source is
    "unavailable" and every count is 0, but this is explicitly NOT the
    same shape as "queried successfully, found nothing relevant"
    (relevant_events would also be 0 there, but log_source would name the
    real source that WAS queried) - callers must check log_source, never
    infer from relevant_events == 0 alone."""

    return {
        "resource_id": resource_id,
        "resource_type": resource_type,
        "log_source": "unavailable",
        "requested_window": None,
        "analyzed_window": None,
        "relevance_categories_matched": [],
        "total_events": 0,
        "relevant_events": 0,
        "patterns": [],
        "timeline": [],
        "representative_events": [],
        "limitations": [reason],
    }

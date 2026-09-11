"""
=========================================================
AI SRE AGENT
Module : Evidence Gap Assessment
Purpose:
    Decides, purely from an EXISTING RCA report's own text, what the
    optional Log Investigation feature should do before it discovers or
    fetches anything:

      1. gap_detected - does the RCA's own wording indicate it couldn't
         fully establish the underlying cause (e.g. "suspected",
         "insufficient evidence", "unable to determine")?
      2. categories / source_hints - which kind of log evidence (web
         server, application, authentication, system service, container,
         network) the RCA's own wording points toward, and which tokens
         should be used to PRIORITIZE discovery of a matching log source.
      3. (build_log_investigation_plan) a structured, human-readable plan
         - which component is suspected, which named log SOURCE TYPES
         would be relevant (e.g. "nginx_error"/"nginx_access"/
         "systemd_nginx" - not yet a discovered/fetched source, just what
         WOULD be relevant), why, and which specific evidence terms to
         look for once a matching source is actually found.

    This module never reads a metric value or a numeric threshold - it
    only pattern-matches the RCA's own root_cause/summary/evidence
    strings, exactly the same "mechanical text matching, never RCA
    interpretation" style context/log_evidence_builder.py's
    _select_relevance_categories() already uses for a different purpose
    (that one decides which FETCHED LOG LINES to keep; this one decides
    WHICH SOURCE TYPE to prioritize and WHY logs are being fetched at
    all). Neither module imports the other - both are used side by side
    by api/log_investigation_manager.py.

    Deliberately NOT implemented as an 8th field Gemini must emit during
    the original RCA call (llm/prompt_builder.py) - that file is the
    highest-blast-radius prompt in the repo (every Full/Single Resource
    Investigation depends on it) and has never asked Gemini for a nested
    object. Computing the plan here, from the RCA's own already-generated
    text, satisfies "dynamically derived, never hardcoded metric->log
    mappings" with zero risk to the existing RCA pipeline.

    This is evidence classification, not root-cause determination:
    nothing here ever assigns a severity, root cause, or confidence value
    - Gemini alone interprets what the discovered log evidence means.
=========================================================
"""

from typing import Any, Dict, List, Optional

# Generic uncertainty phrasing that indicates the existing RCA could not
# fully establish the underlying cause - read from the RCA's OWN text
# (Gemini's own words), never a numeric metric comparison.
_GAP_SIGNALS: List[str] = [
    "suspected", "insufficient evidence", "unable to determine", "cannot determine",
    "cannot confirm", "unclear", "further investigation", "additional evidence",
    "not confirmed", "undetermined", "inconclusive", "unknown cause",
]

# category -> {
#   "signals": tokens indicating this category in the RCA's own text,
#   "source_hints": tokens used only to prioritize which discovered log
#       group/stream NAME to prefer (see collector/logs.py) - never a
#       root-cause decision,
#   "filters_by_source": {source_type_name: [evidence terms]} - NAMED
#       source-type identifiers that would be relevant if this category is
#       suspected (e.g. "nginx_error"/"nginx_access"/"systemd_nginx" - not
#       yet a discovered/fetched source, just what WOULD be relevant),
#       each mapped to ITS OWN specific evidence terms - e.g. nginx_error
#       gets upstream/worker terms, nginx_access gets HTTP status terms,
#       systemd_nginx gets service-lifecycle terms. These ARE the terms
#       actually applied during relevance filtering once a real source is
#       discovered and classified against these same names (see
#       api/log_investigation_manager.py) - never a
#       "X token found -> Y root cause" rule; Gemini alone interprets what
#       any matched evidence actually means,
#   "specific_names": optional {token: component_name} - when a more
#       specific technology name (e.g. "nginx") appears in the RCA text,
#       the plan names the component after it instead of the generic
#       category name, matching how a human would describe it.
# }
_EVIDENCE_GAP_CATEGORIES: Dict[str, Dict[str, Any]] = {
    "web_server": {
        "signals": [
            "web tier", "web-tier", "web server", "nginx", "apache", "httpd",
            "upstream", "gateway", "5xx", "502", "503", "504", "reverse proxy",
        ],
        "source_hints": ["nginx", "apache", "httpd", "web", "proxy"],
        "filters_by_source": {
            "nginx_error": [
                "upstream timeout", "connection refused", "upstream error", "worker failure",
                "connection reset", "configuration error", "startup error",
            ],
            "nginx_access": ["http 500", "http 502", "http 503", "http 504", "5xx"],
            "systemd_nginx": ["service restart", "failed", "stopped", "started", "crash"],
        },
        "specific_names": {"nginx": "nginx", "apache": "apache", "httpd": "httpd"},
    },
    "application": {
        "signals": [
            "application", "app tier", "app-tier", "service failure", "exception",
            "stack trace", "jvm", "java", "service unavailable", "process crash",
        ],
        "source_hints": ["app", "application", "service", "java"],
        "filters_by_source": {
            "application_log": [
                "exception", "stack trace", "service unavailable", "process crash",
                "startup failure", "out of memory", "connection failure",
            ],
            "systemd_service": ["service restart", "failed", "stopped", "started", "crash"],
        },
        "specific_names": {"java": "java_application", "jvm": "java_application"},
    },
    "authentication": {
        "signals": [
            "authentication", "unauthorized", "401", "403", "login failed",
            "auth failure", "access denied",
        ],
        "source_hints": ["auth", "sso", "login", "identity"],
        "filters_by_source": {
            "application_log": ["authentication failure", "unauthorized", "access denied", "token expired"],
            "auth_service_log": ["authentication failure", "unauthorized", "access denied", "token expired"],
        },
        "specific_names": {},
    },
    "system_service": {
        "signals": [
            "systemd", "service crash", "process failed", "daemon", "service failed",
            "kernel", "os-level",
        ],
        "source_hints": ["system", "syslog", "messages", "daemon"],
        "filters_by_source": {
            "systemd_journal": ["service failed", "process failed", "daemon crash", "restart", "start-limit-hit"],
            "system_log": ["oom-killer", "segfault", "kernel panic", "disk error"],
        },
        "specific_names": {},
    },
    "container": {
        "signals": [
            "container", "docker", "pod ", "containerd", "oom killed",
        ],
        "source_hints": ["docker", "container", "pod"],
        "filters_by_source": {
            "container_runtime_log": ["container killed", "oom killed", "restart loop", "exit code"],
            "application_log": ["exception", "stack trace", "process crash"],
        },
        "specific_names": {},
    },
    "network": {
        "signals": [
            "connection refused", "connection timeout", "connection reset", "network unreachable",
            "packet loss", "security group", "dns",
        ],
        "source_hints": ["network", "flow"],
        "filters_by_source": {
            "vpc_flow_logs": ["reject", "connection refused", "connection timeout", "packet loss"],
        },
        "specific_names": {},
    },
}

# Resource-type-native components - always relevant to describe for that
# resource type (not gated on RCA text matching), since these ARE the
# primary real evidence source for that resource type. "required" (below)
# still reflects only what the RCA's own text indicates - these entries
# only describe WHAT WOULD be checked if a log investigation is run, not
# whether one is needed.
_RESOURCE_NATIVE_COMPONENTS: Dict[str, Dict[str, Any]] = {
    "Load Balancer": {
        "component": "load_balancer_access",
        "filters_by_source": {
            "alb_access_log": ["5xx", "4xx", "target connection failure", "target timeout"],
        },
        "why_needed": "ALB access logs record the real request/response codes and target processing time for this load balancer.",
    },
    "Auto Scaling Group": {
        "component": "scaling_activity",
        "filters_by_source": {
            "asg_scaling_activity": ["launch failure", "instance replacement"],
            "asg_lifecycle": ["health check failure", "lifecycle timeout", "termination"],
        },
        "why_needed": "Scaling activities and lifecycle events record instance replacement, launch, and health-check history for this group.",
    },
}


def _flatten_unique(term_lists) -> List[str]:
    """Flattens per-source term lists into one order-preserving, de-duplicated
    list - this is what "evidence_to_check" (the existing, backward-compatible
    flat field every caller already reads) is derived from."""

    seen = set()
    flattened: List[str] = []
    for terms in term_lists:
        for term in terms:
            if term not in seen:
                seen.add(term)
                flattened.append(term)
    return flattened


def _report_text(report: Dict[str, Any]) -> str:
    parts = [str(report.get("root_cause") or ""), str(report.get("summary") or "")]
    parts.extend(str(item) for item in (report.get("evidence") or []))
    return " ".join(parts).lower()


def assess_evidence_gap(report: Dict[str, Any]) -> Dict[str, Any]:
    """Returns {gap_detected, categories, source_hints, signals} - purely
    from matching the existing RCA's own text against the tables above.
    Never reads a metric/threshold value; never assigns a root cause."""

    text = _report_text(report)

    matched_signals = [signal for signal in _GAP_SIGNALS if signal in text]
    gap_detected = bool(matched_signals)

    categories: List[str] = []
    source_hints: List[str] = []

    for category, spec in _EVIDENCE_GAP_CATEGORIES.items():
        matched = [signal for signal in spec["signals"] if signal in text]
        if matched:
            categories.append(category)
            source_hints.extend(spec["source_hints"])
            matched_signals.extend(matched)

    return {
        "gap_detected": gap_detected or bool(categories),
        "categories": sorted(categories),
        "source_hints": sorted(set(source_hints)),
        "signals": sorted(set(matched_signals)),
    }


def build_log_investigation_plan(report: Dict[str, Any], resource_type: Optional[str] = None) -> Dict[str, Any]:
    """Structured log investigation plan derived from assess_evidence_gap()'s
    own matches (reused, not duplicated) plus this resource type's native
    evidence source, if any (e.g. ALB access logs, ASG scaling activity -
    always described since they ARE that resource type's primary real
    evidence source, independent of RCA wording).

    Returns {"required": bool, "reason": "...", "components": [
        {"component": "...", "resource_type": "...", "log_sources": [...],
         "why_needed": "...", "evidence_to_check": [...],
         "filters_by_source": {source_type_name: [...]}}
    ]}

    "log_sources" stays a flat list of source-type names and
    "evidence_to_check" stays the flattened union of every source's terms
    (both backward compatible with every existing reader of this shape);
    "filters_by_source" is a new, additive field carrying the actual
    per-source-type term mapping that
    api/log_investigation_manager.py uses to apply investigation-specific
    relevance filtering to the correct discovered source.

    "required" reflects only assess_evidence_gap()'s own text-derived
    gap_detected - it is informational, never a gate on whether the user
    is allowed to trigger "Investigate Logs" (that stays fully optional,
    per the feature's own design)."""

    gap = assess_evidence_gap(report)
    text = _report_text(report)

    components: List[Dict[str, Any]] = []
    seen_component_names = set()

    for category in gap["categories"]:
        spec = _EVIDENCE_GAP_CATEGORIES[category]

        component_name = category
        for token, specific_name in (spec.get("specific_names") or {}).items():
            if token in text:
                component_name = specific_name
                break

        if component_name in seen_component_names:
            continue
        seen_component_names.add(component_name)

        matched_signals = [signal for signal in spec["signals"] if signal in text]
        why_needed = (
            f"The existing RCA's own wording ({', '.join(matched_signals)}) suggests "
            f"{component_name.replace('_', ' ')} evidence may help explain the underlying cause."
        )

        filters_by_source = {source: list(terms) for source, terms in spec["filters_by_source"].items()}
        components.append({
            "component": component_name,
            "resource_type": resource_type,
            "log_sources": list(filters_by_source.keys()),
            "why_needed": why_needed,
            "evidence_to_check": _flatten_unique(filters_by_source.values()),
            "filters_by_source": filters_by_source,
        })

    native = _RESOURCE_NATIVE_COMPONENTS.get(resource_type)
    if native and native["component"] not in seen_component_names:
        native_filters_by_source = {source: list(terms) for source, terms in native["filters_by_source"].items()}
        components.append({
            "component": native["component"],
            "resource_type": resource_type,
            "log_sources": list(native_filters_by_source.keys()),
            "why_needed": native["why_needed"],
            "evidence_to_check": _flatten_unique(native_filters_by_source.values()),
            "filters_by_source": native_filters_by_source,
        })

    if gap["gap_detected"] and gap["signals"]:
        reason = (
            "The existing RCA's own wording indicates the underlying cause is not fully "
            f"established (signals: {', '.join(gap['signals'])})."
        )
    elif gap["gap_detected"]:
        reason = "The existing RCA's own wording indicates the underlying cause is not fully established."
    else:
        reason = "The existing RCA does not indicate a specific evidence gap - log evidence would be gathered at the user's request."

    return {
        "required": gap["gap_detected"],
        "reason": reason,
        "components": components,
    }

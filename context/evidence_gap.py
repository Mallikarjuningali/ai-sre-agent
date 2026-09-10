"""
=========================================================
AI SRE AGENT
Module : Evidence Gap Assessment
Purpose:
    Decides two things about an EXISTING RCA report, purely from its own
    text, before the optional Log Investigation feature discovers/fetches
    anything:

      1. gap_detected - does the RCA's own wording indicate it couldn't
         fully establish the underlying cause (e.g. "suspected",
         "insufficient evidence", "unable to determine")?
      2. categories / source_hints - which kind of log evidence (web
         server, application, authentication, system service, container)
         the RCA's own wording points toward, and which tokens should be
         used to PRIORITIZE discovery of a matching log source.

    This module never reads a metric value or a numeric threshold - it
    only pattern-matches the RCA's own root_cause/summary/evidence
    strings, exactly the same "mechanical text matching, never RCA
    interpretation" style context/log_evidence_builder.py's
    _select_relevance_categories() already uses for a different purpose
    (that one decides which FETCHED LOG LINES to keep; this one decides
    WHICH SOURCE to prioritize and WHY logs are being fetched at all).
    Neither module imports the other - both are used side by side by
    api/log_investigation_manager.py.

    This is evidence classification, not root-cause determination:
    nothing here ever assigns a severity, root cause, or confidence value
    - Gemini alone interprets what the discovered log evidence means.
=========================================================
"""

from typing import Any, Dict, List

# Generic uncertainty phrasing that indicates the existing RCA could not
# fully establish the underlying cause - read from the RCA's OWN text
# (Gemini's own words), never a numeric metric comparison.
_GAP_SIGNALS: List[str] = [
    "suspected", "insufficient evidence", "unable to determine", "cannot determine",
    "cannot confirm", "unclear", "further investigation", "additional evidence",
    "not confirmed", "undetermined", "inconclusive", "unknown cause",
]

# category -> {"signals": tokens indicating this category in the RCA's own
# text, "source_hints": tokens used only to prioritize which discovered
# log group/stream name to prefer, never to decide a root cause}.
_EVIDENCE_GAP_CATEGORIES: Dict[str, Dict[str, List[str]]] = {
    "web_server": {
        "signals": [
            "web tier", "web-tier", "web server", "nginx", "apache", "httpd",
            "upstream", "gateway", "5xx", "502", "503", "504", "reverse proxy",
        ],
        "source_hints": ["nginx", "apache", "httpd", "web", "proxy"],
    },
    "application": {
        "signals": [
            "application", "app tier", "app-tier", "service failure", "exception",
            "stack trace", "jvm", "java", "service unavailable", "process crash",
        ],
        "source_hints": ["app", "application", "service", "java"],
    },
    "authentication": {
        "signals": [
            "authentication", "unauthorized", "401", "403", "login failed",
            "auth failure", "access denied",
        ],
        "source_hints": ["auth", "sso", "login", "identity"],
    },
    "system_service": {
        "signals": [
            "systemd", "service crash", "process failed", "daemon", "service failed",
            "kernel", "os-level",
        ],
        "source_hints": ["system", "syslog", "messages", "daemon"],
    },
    "container": {
        "signals": [
            "container", "docker", "pod ", "containerd", "oom killed",
        ],
        "source_hints": ["docker", "container", "pod"],
    },
}


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

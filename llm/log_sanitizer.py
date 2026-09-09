"""
=========================================================
AI SRE AGENT
Module : Log Sanitizer
Purpose:
    NEW, ISOLATED sanitizer for the optional Log Investigation feature's
    Log Evidence Package (see context/log_evidence_builder.py and
    api/log_investigation_manager.py). Operates ONLY on that package -
    never imported by llm/sanitizer.py, context/context_builder.py,
    llm/prompt_builder.py, analyzer/analyzer.py, or
    api/investigation_manager.py. The existing infra sanitizer
    (llm/sanitizer.py) is not modified by this feature and continues
    to run, unchanged, on the original RCA pipeline.

    Log lines can carry sensitive values metrics/CloudTrail never do
    (raw request IPs, internal hostnames, URLs, auth headers, tokens,
    connection strings, credentials) - this module removes those while
    preserving what actually drives RCA: timestamps, HTTP status codes,
    exception/error type names, ports, event ordering, frequency/counts,
    and the normalized error pattern itself.

    Applied only to the package's free-text fields
    (patterns[].pattern/.examples, representative_events[].message,
    timeline[].description) - every structured field (counts, timestamps,
    window dicts, resource_id/resource_type/log_source) passes through
    untouched, since it is never free text and carries no log content.
=========================================================
"""

import re
from copy import deepcopy
from typing import Any, Dict


# =========================================================
# Redaction patterns - applied in this specific order so an earlier,
# more specific pattern (e.g. a full connection string or URL) is
# redacted as one unit before a later, more general pattern (e.g. a bare
# IP) would otherwise partially match inside it.
# =========================================================

_DB_CONNECTION_STRING_RE = re.compile(
    r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://\S+", re.IGNORECASE
)

_URL_RE = re.compile(r"https?://([^/\s:]+)(?::\d+)?(/[^\s]*)?", re.IGNORECASE)

_AUTHORIZATION_HEADER_RE = re.compile(r"(?i)\bauthorization\s*:\s*\S.*")
_BEARER_TOKEN_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-_.~+/]+=*")

_AWS_ACCESS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_GENERIC_API_KEY_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")
_LABELED_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|passwd)\s*[:=]\s*\S+"
)

_PRIVATE_IPV4_RE = re.compile(
    r"\b(?:10(?:\.\d{1,3}){3}"
    r"|172\.(?:1[6-9]|2\d|3[0-1])(?:\.\d{1,3}){2}"
    r"|192\.168(?:\.\d{1,3}){2})\b(:\d+)?"
)
_ANY_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b(:\d+)?")

_HOSTNAME_RE = re.compile(
    r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b"
)

_PRIVATE_HOST_HINTS = ("internal", "private", "local", "corp", "intranet")

_FREE_TEXT_FIELDS_ON_PATTERN = ("pattern",)


def _looks_private(host: str) -> bool:
    host_lower = host.lower()
    if any(hint in host_lower for hint in _PRIVATE_HOST_HINTS):
        return True
    if _PRIVATE_IPV4_RE.match(host):
        return True
    return False


def _redact_urls(text: str) -> str:
    def _replace(match: "re.Match") -> str:
        host = match.group(1) or ""
        path = match.group(2) or ""
        label = "<PRIVATE_URL>" if _looks_private(host) else "<PUBLIC_URL>"
        return f"{label}{path}"

    return _URL_RE.sub(_replace, text)


def _redact_ips(text: str) -> str:
    def _replace_private(match: "re.Match") -> str:
        port = match.group(1) or ""
        return f"<PRIVATE_IP>{port}"

    def _replace_any(match: "re.Match") -> str:
        port = match.group(1) or ""
        return f"<PUBLIC_IP>{port}"

    text = _PRIVATE_IPV4_RE.sub(_replace_private, text)
    text = _ANY_IPV4_RE.sub(_replace_any, text)
    return text


def _redact_hostnames(text: str) -> str:
    """Only applied AFTER URL/IP redaction, so a hostname that was part
    of a URL (already replaced with <PRIVATE_URL>/<PUBLIC_URL>) or an IP
    (already replaced with <PRIVATE_IP>/<PUBLIC_IP>) is never
    double-processed - this only catches bare hostnames appearing outside
    a URL."""

    def _replace(match: "re.Match") -> str:
        return "<HOSTNAME>"

    return _HOSTNAME_RE.sub(_replace, text)


def _sanitize_text(text: str) -> str:
    """Applies every redaction rule to one free-text string, in order.
    Preserved untouched by every rule below: timestamps, HTTP status
    codes, exception/error type names, bare numbers/counts, and event
    ordering - none of those match any pattern here."""

    if not text:
        return text

    sanitized = text
    sanitized = _DB_CONNECTION_STRING_RE.sub("<REDACTED_CONNECTION_STRING>", sanitized)
    sanitized = _redact_urls(sanitized)
    sanitized = _AUTHORIZATION_HEADER_RE.sub("Authorization: <REDACTED>", sanitized)
    sanitized = _BEARER_TOKEN_RE.sub("Bearer <REDACTED>", sanitized)
    sanitized = _AWS_ACCESS_KEY_RE.sub("<REDACTED_TOKEN>", sanitized)
    sanitized = _GENERIC_API_KEY_TOKEN_RE.sub("<REDACTED_TOKEN>", sanitized)
    sanitized = _LABELED_SECRET_RE.sub(lambda m: f"{m.group(1)}=<REDACTED>", sanitized)
    sanitized = _redact_ips(sanitized)
    sanitized = _redact_hostnames(sanitized)
    return sanitized


class LogSanitizer:
    """The single entry point - sanitize(evidence_package) -> a deep-copied,
    redacted package. Every structured field (counts/timestamps/window
    dicts/resource_id/resource_type/log_source/limitations) is passed
    through unchanged; only the four free-text fields listed in the
    module docstring are rewritten."""

    def sanitize(self, evidence_package: Dict[str, Any]) -> Dict[str, Any]:
        package = deepcopy(evidence_package)

        package["patterns"] = [
            self._sanitize_pattern(pattern) for pattern in (package.get("patterns") or [])
        ]

        package["representative_events"] = [
            {**event, "message": _sanitize_text(event.get("message", ""))}
            for event in (package.get("representative_events") or [])
        ]

        package["timeline"] = [
            {**entry, "description": _sanitize_text(entry.get("description", ""))}
            for entry in (package.get("timeline") or [])
        ]

        return package

    @staticmethod
    def _sanitize_pattern(pattern: Dict[str, Any]) -> Dict[str, Any]:
        sanitized = dict(pattern)
        sanitized["pattern"] = _sanitize_text(pattern.get("pattern", ""))
        sanitized["examples"] = [_sanitize_text(example) for example in (pattern.get("examples") or [])]
        return sanitized

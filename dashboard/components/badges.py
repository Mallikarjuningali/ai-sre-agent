"""Small inline HTML fragments: severity/status badges and status dots."""
from __future__ import annotations

import html

from .formatting import dot_color_for, kind_for_severity, kind_for_status


def severity_badge(severity: str) -> str:
    kind = kind_for_severity(severity)
    label = html.escape(str(severity or "UNKNOWN").upper())
    return f'<span class="ao-badge ao-badge-{kind}">{label}</span>'


def status_badge(status: str) -> str:
    kind = kind_for_status(status)
    label = html.escape(str(status or "UNKNOWN").upper())
    return f'<span class="ao-badge ao-badge-{kind}">{label}</span>'


def new_badge(label: str = "NEW") -> str:
    return f'<span class="ao-badge ao-badge-new">{html.escape(label)}</span>'


def status_dot(status: str) -> str:
    kind = kind_for_status(status)
    color = dot_color_for(kind)
    return f'<span class="ao-dot" style="background:{color}"></span>'


def status_pill(status: str, label: str | None = None) -> str:
    text = html.escape(label or str(status or "Unknown"))
    return f'<span class="ao-status-pill">{status_dot(status)}{text}</span>'

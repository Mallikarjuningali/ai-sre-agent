"""Small formatting helpers shared across components/views."""
from __future__ import annotations

from datetime import datetime, timezone

SEVERITY_KIND = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
}

STATUS_KIND = {
    "completed": "completed",
    "success": "completed",
    "running": "running",
    "in_progress": "running",
    "failed": "failed",
    "error": "failed",
    "healthy": "healthy",
    "warning": "warning",
    "degraded": "warning",
    "critical": "critical",
    "down": "failed",
    "completed_with_errors": "warning",
    "partial_success": "warning",
}

DOT_COLOR = {
    "critical": "var(--red)",
    "high": "var(--orange)",
    "medium": "var(--amber)",
    "low": "var(--blue)",
    "healthy": "var(--green)",
    "warning": "var(--amber)",
    "completed": "var(--green)",
    "running": "var(--blue)",
    "failed": "var(--red)",
    "unknown": "var(--gray)",
}


def parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def time_ago(value) -> str:
    dt = parse_dt(value)
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hr ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    return dt.strftime("%b %d, %Y")


def format_datetime(value, fmt: str = "%b %d, %Y %I:%M %p") -> str:
    dt = parse_dt(value)
    return dt.strftime(fmt) if dt else "—"


def format_duration(seconds) -> str:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "—"
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def format_number(value) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "—"
    if num == int(num):
        return f"{int(num):,}"
    return f"{num:,.1f}"


def kind_for_severity(severity: str) -> str:
    return SEVERITY_KIND.get(str(severity).lower(), "neutral")


def kind_for_status(status: str) -> str:
    return STATUS_KIND.get(str(status).lower(), "neutral")


def dot_color_for(kind: str) -> str:
    return DOT_COLOR.get(kind, "var(--gray)")

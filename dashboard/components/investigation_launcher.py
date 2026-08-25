"""
Investigation launcher — the primary action on the Investigation page.

Before an investigation starts, the operator picks a mode:
  - Full Infrastructure: every collector, every resource, one complete RCA.
  - Single Resource: one AWS resource, picked from a live-discovered inventory.

Starting either mode calls InvestigationService (POST /investigation/full or
POST /investigation/resource — see services/CONTRACT.md). This module never
computes or fabricates investigation results itself: KPI numbers come from
SummaryService, and once a run is in flight, every field in the progress
panel (phase, percent, current resource, elapsed/remaining) comes from
InvestigationService's status poll. When the configured backend can't
accept a live run (local/S3 data source), starting a run surfaces a clear
inline message instead of simulating one.

The Single Resource picker's inventory can come from either of two
sources: ResourceService's cached resources.json feed (a byproduct of a
previous investigation, if one has run), or - via the "Refresh Resources"
button - ResourceDiscoveryService, which queries AWS directly
(GET /investigation/resources) and needs no prior investigation to have
run at all. See _refresh_resources() below.
"""
from __future__ import annotations

import html
import time
from datetime import datetime, timezone

import streamlit as st

from .cards import card, card_title, empty_state
from .formatting import format_duration, format_number, parse_dt, time_ago
from .icons import svg_icon
from .badges import status_badge
from services import InvestigationActionError, ResourceDiscoveryActionError

_SESSION_KEY = "launch_active_run"
_ERROR_KEY = "launch_error"

# Live-refreshed resource inventory (see _refresh_resources) - kept in
# session_state, separate from ResourceService's resources.json-backed
# cache, so "Refresh Resources" never depends on output/context/*.json or
# on Full Investigation having run first.
_REFRESHED_INVENTORY_KEY = "refresh_resources_inventory"
_REFRESHED_AT_KEY = "refresh_resources_last_refreshed"
_REFRESH_ERROR_KEY = "refresh_resources_error"
_SELECTION_CLEARED_KEY = "refresh_resources_selection_cleared"

_DEFAULT_PHASES = [
    {"key": "COLLECTING_METRICS", "label": "Collecting Metrics"},
    {"key": "BUILDING_CONTEXT", "label": "Building Context"},
    {"key": "RUNNING_AI_ANALYSIS", "label": "Running AI Analysis"},
    {"key": "GENERATING_RCA", "label": "Generating RCA"},
    {"key": "PUBLISHING_DASHBOARD", "label": "Publishing Dashboard"},
]

_TERMINAL_STATES = {"COMPLETED", "FAILED"}


def render_launcher(services, config) -> None:
    st.markdown(
        """
        <div class="ao-section-header">
          <div class="ao-section-header-title">Choose Investigation Mode</div>
          <div class="ao-section-header-subtitle">Pick what AegisOps AI should investigate, then launch it below.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    active = st.session_state.get(_SESSION_KEY)
    if active:
        _progress_fragment(services)
    else:
        _render_mode_cards(services)

    st.markdown("<div style='height:1.4rem'></div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Selection state — two large mode cards
# ---------------------------------------------------------------------------

def _render_mode_cards(services) -> None:
    error = st.session_state.pop(_ERROR_KEY, None)

    col1, col2 = st.columns(2, gap="large")

    with col1, card(key="mode_card_full"):
        _render_full_card(services)

    with col2, card(key="mode_card_resource"):
        _render_resource_card(services)

    if error:
        st.markdown(
            f"""
            <div class="ao-launch-error">
              {svg_icon("alert-triangle", size=15, color="#fca5a5")}
              <span>{html.escape(error)}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _mini_stat(label: str, value_html: str) -> str:
    # Single-line output is deliberate: concatenating multiple multi-line,
    # indented HTML snippets before one st.markdown() call leaves a
    # whitespace-only line at each join point. Streamlit's markdown parser
    # reads that as the end of the HTML block, and renders everything after
    # it as a literal indented code block instead of HTML.
    return (
        f'<div class="ao-mode-stat">'
        f'<div class="ao-mode-stat-label">{html.escape(label)}</div>'
        f'<div class="ao-mode-stat-value">{value_html}</div>'
        f"</div>"
    )


def _render_full_card(services) -> None:
    summary = services.summary.get_overview()
    collectors = services.collector.get_collector_health()
    kpis = summary.get("kpis") or {}
    latest = summary.get("latest_execution") or {}

    st.markdown(
        f"""
        <div class="ao-mode-card-header">
          <div class="ao-mode-icon ao-mode-icon-accent">{svg_icon("layers", size=20)}</div>
          <div>
            <div class="ao-mode-title">Full Infrastructure</div>
            <div class="ao-mode-tag">Comprehensive</div>
          </div>
        </div>
        <div class="ao-mode-desc">
          Run the AI investigation across the entire AWS environment — collects every
          resource, runs every collector, and generates one complete RCA report.
        </div>
        """,
        unsafe_allow_html=True,
    )

    total_resources = (kpis.get("total_resources") or {}).get("value")
    stats_html = (
        _mini_stat("Estimated Resources", format_number(total_resources) if total_resources is not None else "—")
        + _mini_stat("Estimated Time", "5–10 min")
        + _mini_stat(
            "Last Execution",
            time_ago(latest.get("start_time")) if latest.get("start_time") else "Never run",
        )
    )
    st.markdown(f'<div class="ao-mode-stats-row">{stats_html}</div>', unsafe_allow_html=True)

    checklist_items = ["All EC2 Instances"] + list(collectors.keys())
    checklist_html = "".join(
        f'<div class="ao-mode-checklist-item"><span class="ao-check">✓</span>{html.escape(item)}</div>'
        for item in checklist_items
    )
    st.markdown(
        f'<div class="ao-mode-checklist-label">Investigates</div><div class="ao-mode-checklist">{checklist_html}</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="ao-mode-warning">
          {svg_icon("alert-triangle", size=14, color="#fcd34d")}
          <span>This may take several minutes. Every discovered resource will be analyzed.</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button("Start Full Investigation", key="launch_start_full", type="primary", width="stretch"):
        _start_full(services)


def _resource_display_text(resource: dict) -> str:
    """`label (id)` when label adds information, otherwise just `id` - keeps
    the dropdown's own text (which st.selectbox already filters as you type)
    from showing a redundant 'i-0123 · i-0123' when label is missing or
    duplicates the id."""
    resource_id = resource["id"]
    label = resource.get("label")
    if not label or label == resource_id:
        return resource_id
    return f"{label} ({resource_id})"


def _render_resource_card(services) -> None:
    st.markdown(
        f"""
        <div class="ao-mode-card-header">
          <div class="ao-mode-icon ao-mode-icon-blue">{svg_icon("cpu", size=20)}</div>
          <div>
            <div class="ao-mode-title">Single Resource</div>
            <div class="ao-mode-tag">Targeted</div>
          </div>
        </div>
        <div class="ao-mode-desc">
          Investigate one specific AWS resource for a fast, focused root cause analysis.
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button("↻ Refresh Resources", key="refresh_resources_btn", width="stretch"):
        _refresh_resources(services)

    refresh_error = st.session_state.pop(_REFRESH_ERROR_KEY, None)
    if refresh_error:
        st.markdown(
            f'<div class="ao-launch-error">{svg_icon("alert-triangle", size=14, color="#fca5a5")}'
            f"<span>{html.escape(refresh_error)}</span></div>",
            unsafe_allow_html=True,
        )

    selection_cleared = st.session_state.pop(_SELECTION_CLEARED_KEY, None)
    if selection_cleared:
        st.caption(f"⚠️ {selection_cleared}")

    last_refreshed = st.session_state.get(_REFRESHED_AT_KEY)
    if last_refreshed:
        st.caption(f"Last refreshed {time_ago(last_refreshed)} (live from AWS)")

    # Prefer the live-refreshed inventory (this session, this AWS call).
    # Falls back to the published resources.json feed so the picker still
    # works exactly as before if the operator hasn't clicked Refresh yet
    # but a previous investigation already published one - Refresh is what
    # removes the dependency on that feed, not a requirement to use it.
    inventory = st.session_state.get(_REFRESHED_INVENTORY_KEY) or services.resource.get_inventory()

    if not inventory:
        empty_state("No resources discovered yet", 'Click "Refresh Resources" to query AWS directly', "🗂️")
        st.button("Start Resource Investigation", key="launch_start_resource", type="primary", width="stretch", disabled=True)
        return

    resource_types = list(inventory.keys())
    resource_type = st.selectbox("Resource Type", resource_types, key="launch_resource_type")

    resources = inventory.get(resource_type) or []
    if not resources:
        st.selectbox("Resource", ["No resources discovered for this type"], key="launch_resource_empty", disabled=True)
        selected_resource = None
    else:
        sorted_resources = sorted(resources, key=lambda r: _resource_display_text(r).lower())
        options = {_resource_display_text(r): r for r in sorted_resources}
        selected_label = st.selectbox(
            "Resource",
            list(options.keys()),
            key="launch_resource_id",
            placeholder="Search by ID or name…",
        )
        selected_resource = options[selected_label]

    st.markdown(f'<div class="ao-mode-stats-row">{_mini_stat("Estimated Time", "30 sec – 2 min")}</div>', unsafe_allow_html=True)
    st.markdown("<div style='height:0.4rem'></div>", unsafe_allow_html=True)

    if st.button(
        "Start Resource Investigation",
        key="launch_start_resource",
        type="primary",
        width="stretch",
        disabled=selected_resource is None,
    ):
        _start_resource(services, resource_type, selected_resource)


def _refresh_resources(services) -> None:
    """Queries AWS directly (GET /investigation/resources) and replaces the
    picker's inventory. Never starts an investigation, never creates a
    report, never calls Gemini - this is discovery only. Preserves the
    currently selected resource if it still exists post-refresh (matched
    by id, not by the selectbox's display label); otherwise clears the
    selection and surfaces a message instead of silently pointing at a
    resource that's gone."""
    prev_type = st.session_state.get("launch_resource_type")
    prev_label = st.session_state.get("launch_resource_id")
    prev_inventory = st.session_state.get(_REFRESHED_INVENTORY_KEY) or services.resource.get_inventory()

    prev_id = None
    if prev_type and prev_label:
        for r in prev_inventory.get(prev_type) or []:
            if _resource_display_text(r) == prev_label:
                prev_id = r.get("id")
                break

    try:
        inventory = services.resource_discovery.discover()
    except ResourceDiscoveryActionError as exc:
        st.session_state[_REFRESH_ERROR_KEY] = str(exc)
        st.rerun()
        return

    st.session_state[_REFRESHED_INVENTORY_KEY] = inventory
    st.session_state[_REFRESHED_AT_KEY] = datetime.now(timezone.utc).isoformat()
    st.session_state.pop(_REFRESH_ERROR_KEY, None)

    still_exists = bool(
        prev_id
        and prev_type
        and any(r.get("id") == prev_id for r in inventory.get(prev_type) or [])
    )

    if prev_id and not still_exists:
        # Clear the widgets' own keys so Streamlit doesn't try to render a
        # now-stale selection - falls back to the first entry instead.
        st.session_state.pop("launch_resource_type", None)
        st.session_state.pop("launch_resource_id", None)
        st.session_state[_SELECTION_CLEARED_KEY] = (
            f'Previously selected resource "{prev_id}" no longer exists — selection cleared.'
        )

    st.rerun()


def _start_full(services) -> None:
    try:
        result = services.investigation.start_full_investigation()
    except InvestigationActionError as exc:
        st.session_state[_ERROR_KEY] = str(exc)
        st.rerun()
        return
    _enter_running_state(mode="full", label="Full Infrastructure", result=result)


def _start_resource(services, resource_type: str, resource: dict) -> None:
    try:
        result = services.investigation.start_resource_investigation(resource_type, resource["id"])
    except InvestigationActionError as exc:
        st.session_state[_ERROR_KEY] = str(exc)
        st.rerun()
        return
    label = f'{resource_type} · {resource.get("label", resource["id"])}'
    _enter_running_state(mode="resource", label=label, result=result)


def _enter_running_state(mode: str, label: str, result: dict) -> None:
    st.session_state[_SESSION_KEY] = {
        "run_id": result.get("run_id", "unknown"),
        "mode": mode,
        "label": label,
        "started_at_iso": result.get("started_at"),
        "started_at_epoch": time.time(),
        "last_status": None,
    }
    st.session_state.pop(_ERROR_KEY, None)
    st.rerun()


# ---------------------------------------------------------------------------
# Running state — professional progress interface, auto-polls the backend
# ---------------------------------------------------------------------------

@st.fragment(run_every=3)
def _progress_fragment(services) -> None:
    active = st.session_state.get(_SESSION_KEY)
    if not active:
        return

    status = None
    poll_error = None
    try:
        status = services.investigation.get_run_status(active["run_id"])
    except InvestigationActionError as exc:
        poll_error = str(exc)

    if status:
        active["last_status"] = status
        st.session_state[_SESSION_KEY] = active
    else:
        status = active.get("last_status")

    _render_progress_panel(active, status, poll_error)


def _render_progress_panel(active: dict, status: dict | None, poll_error: str | None) -> None:
    run_status = (status or {}).get("status", "QUEUED")
    is_terminal = run_status in _TERMINAL_STATES
    phases = (status or {}).get("phases") or _DEFAULT_PHASES
    percent = _clamp_percent((status or {}).get("percent"))
    phase_label = (status or {}).get("phase_label") or ("Queued" if run_status == "QUEUED" else "Waiting for backend status…")
    current_resource = (status or {}).get("current_resource") or "—"
    elapsed = _elapsed_seconds(active, status)
    remaining = (status or {}).get("remaining_seconds_estimate")

    with card(key="progress_panel"):
        header_icon = (
            svg_icon("check-circle", size=18, color="#86efac")
            if run_status == "COMPLETED"
            else svg_icon("alert-triangle", size=18, color="#fca5a5")
            if run_status == "FAILED"
            else '<span class="ao-pulse-dot"></span>'
        )
        title = {
            "COMPLETED": "Investigation Complete",
            "FAILED": "Investigation Failed",
        }.get(run_status, "Running Investigation…")

        st.markdown(
            f"""
            <div class="ao-progress-header">
              <div class="ao-progress-header-left">
                <div class="ao-progress-icon">{header_icon}</div>
                <div>
                  <div class="ao-progress-title">{html.escape(title)}</div>
                  <div class="ao-progress-subtitle">{html.escape(active.get("label", ""))} · <span class="ao-mono">{html.escape(active.get("run_id", ""))}</span></div>
                </div>
              </div>
              {status_badge(run_status)}
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            f"""
            <div class="ao-progress-bar-row">
              <div class="ao-progress-bar-track">
                <div class="ao-progress-bar-fill" style="width:{percent}%;"></div>
              </div>
              <div class="ao-progress-percent">{percent}%</div>
            </div>
            <div class="ao-progress-phase-label">{html.escape(phase_label)}</div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(_phase_stepper_html(phases), unsafe_allow_html=True)

        meta_html = (
            _mini_stat("Current Resource", f'<span class="ao-mono" style="font-size:13px;">{html.escape(str(current_resource))}</span>')
            + _mini_stat("Elapsed", format_duration(elapsed))
            + _mini_stat("Remaining (est.)", format_duration(remaining) if remaining is not None else "—")
        )
        st.markdown(f'<div class="ao-mode-stats-row" style="margin-top:0.9rem;">{meta_html}</div>', unsafe_allow_html=True)

        if poll_error:
            st.markdown(
                f'<div class="ao-launch-error" style="margin-top:0.9rem;">{svg_icon("alert-triangle", size=14, color="#fcd34d")}'
                f'<span>Reconnecting to backend status feed…</span></div>',
                unsafe_allow_html=True,
            )

        if is_terminal:
            st.markdown("<div style='height:0.6rem'></div>", unsafe_allow_html=True)
            col_a, col_b = st.columns([1, 1])
            with col_a:
                if st.button("View Full Report →", key="progress_view_report", width="stretch"):
                    # Same hand-off app.py's own query-param handling uses for
                    # a real URL (?page=report&run_id=...) - just set directly
                    # since we're already in-session. Report Viewer, not
                    # Investigation, is where completed reports live now.
                    st.session_state["preselected_run_id"] = active.get("run_id")
                    st.session_state["active_page"] = "report"
                    st.session_state.pop(_SESSION_KEY, None)
                    st.rerun()
            with col_b:
                if st.button("Back to Investigation Modes", key="progress_dismiss", width="stretch"):
                    st.session_state.pop(_SESSION_KEY, None)
                    st.rerun()
        else:
            st.markdown("<div style='height:0.3rem'></div>", unsafe_allow_html=True)
            st.caption("This panel refreshes automatically every few seconds.")


def _phase_stepper_html(phases: list[dict]) -> str:
    # Kept single-line per fragment for the same reason as _mini_stat above —
    # see that docstring comment.
    n = len(phases)
    items = []
    for i, phase in enumerate(phases):
        state = phase.get("state", "pending")
        dot_content = "✓" if state == "done" else ""
        items.append(
            f'<div class="ao-phase-step ao-phase-step-{state}">'
            f'<div class="ao-phase-dot">{dot_content}</div>'
            f'<div class="ao-phase-label">{html.escape(phase.get("label", ""))}</div>'
            f"</div>"
        )
        if i < n - 1:
            connector_state = "done" if state == "done" else "pending"
            items.append(f'<div class="ao-phase-connector ao-phase-connector-{connector_state}"></div>')
    return f'<div class="ao-phase-stepper">{"".join(items)}</div>'


def _clamp_percent(value) -> int:
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, round(pct)))


def _elapsed_seconds(active: dict, status: dict | None) -> int:
    if status and status.get("elapsed_seconds") is not None:
        try:
            return int(status["elapsed_seconds"])
        except (TypeError, ValueError):
            pass

    started_at = (status or {}).get("started_at") or active.get("started_at_iso")
    dt = parse_dt(started_at)
    if dt:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))

    return max(0, int(time.time() - active.get("started_at_epoch", time.time())))

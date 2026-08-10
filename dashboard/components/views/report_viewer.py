"""Report Viewer — the dedicated place to review a completed investigation.

Reached only via a link/button (Execution History's "Open Report →", or the
launcher's own "View Full Report →" on completion) - never a persistent
sidebar item, since a report only makes sense in the context of a specific
run_id. Investigation (investigation.py) stays focused on launching runs and
showing live progress; this page is where the *result* actually lives.
"""
from __future__ import annotations

import streamlit as st

from ..badges import new_badge, severity_badge, status_badge
from ..cards import card, card_title, empty_state
from ..formatting import format_datetime, format_duration, format_number
from ..topbar import page_header

_SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
_RUN_ID_KEY = "report_viewer_run_id"
_EXPANDED_KEY = "report_viewer_expanded_id"


def render(services, config) -> None:
    run_id = _resolve_run_id()

    st.markdown(
        '<a href="?page=history" target="_self" class="ao-link-btn">← Back to Execution History</a>',
        unsafe_allow_html=True,
    )
    st.markdown("<div style='height:0.6rem'></div>", unsafe_allow_html=True)

    if not run_id:
        page_header("Investigation Report", "No investigation selected")
        with card():
            empty_state(
                "No report selected",
                "Open a report from Execution History, or launch a new investigation",
                "📄",
            )
        return

    execution = services.history.get_execution(run_id)
    reports = services.report.get_reports_for_run(run_id)

    page_header("Investigation Report", f"Run {run_id}")

    _render_overview_card(run_id, execution, reports)

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    st.markdown(
        """
        <div class="ao-section-header">
          <div class="ao-section-header-title">Investigated Resources</div>
          <div class="ao-section-header-subtitle">Each resource's AI root cause analysis, reviewed independently.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not reports:
        with card():
            empty_state("No AI report for this run", "The backend hasn't published a report for this run yet", "🤖")
        return

    sorted_reports = sorted(
        reports, key=lambda r: _SEVERITY_RANK.get(str(r.get("severity", "")).upper(), 0), reverse=True
    )

    expanded_id = st.session_state.get(_EXPANDED_KEY)

    for report in sorted_reports:
        report_key = report.get("report_id") or report.get("instance_id") or "resource"
        is_expanded = expanded_id == report_key

        _render_resource_card(report, report_key, is_expanded)

        if is_expanded:
            st.markdown("<div style='height:0.7rem'></div>", unsafe_allow_html=True)
            _render_resource_overview_card(report)
            _render_ai_investigation_card(report)
            _render_root_cause_card(report)
            col_evidence, col_recommendations = st.columns(2)
            with col_evidence:
                _render_evidence_card(report)
            with col_recommendations:
                _render_recommendations_card(report)
            col_telemetry, col_confidence = st.columns([1.4, 1])
            with col_telemetry:
                _render_telemetry_card(report)
            with col_confidence:
                _render_confidence_card(report)

        st.markdown("<div style='height:0.9rem'></div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Run resolution - durable across this page's own reruns (e.g. toggling
# "View Details"), unlike a plain single-use pop of preselected_run_id.
# ---------------------------------------------------------------------------

def _resolve_run_id() -> str | None:
    incoming = st.session_state.pop("preselected_run_id", None)
    if incoming:
        st.session_state[_RUN_ID_KEY] = incoming
        st.session_state.pop(_EXPANDED_KEY, None)
    return st.session_state.get(_RUN_ID_KEY)


# ---------------------------------------------------------------------------
# Execution overview
# ---------------------------------------------------------------------------

def _render_overview_card(run_id: str, execution: dict | None, reports: list[dict]) -> None:
    execution = execution or {}
    with card():
        card_title("Execution Overview")

        row1 = st.columns(4)
        row1_fields = [
            ("Run ID", run_id),
            ("Status", None),
            ("Duration", format_duration(execution.get("execution_time"))),
            ("Started At", format_datetime(execution.get("start_time"))),
        ]
        for col, (label, value) in zip(row1, row1_fields):
            with col:
                st.markdown(f'<div class="ao-kpi-label">{label}</div>', unsafe_allow_html=True)
                if label == "Status":
                    status = execution.get("status")
                    st.markdown(status_badge(status) if status else "—", unsafe_allow_html=True)
                elif label == "Run ID":
                    st.markdown(f'<span class="ao-mono" style="font-size:13px;">{value}</span>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<div style="font-size:14px; font-weight:600; margin-top:2px;">{value}</div>', unsafe_allow_html=True)

        st.markdown("<div style='height:0.8rem'></div>", unsafe_allow_html=True)

        row2 = st.columns(4)
        row2_fields = [
            ("Finished At", format_datetime(execution.get("finished_at"))),
            ("Resources Analyzed", format_number(execution.get("resources_analyzed", len(reports)))),
            ("Resources Skipped", format_number(execution.get("resources_skipped", 0))),
            ("Reports Generated", format_number(execution.get("reports_generated", len(reports)))),
        ]
        for col, (label, value) in zip(row2, row2_fields):
            with col:
                st.markdown(f'<div class="ao-kpi-label">{label}</div>', unsafe_allow_html=True)
                st.markdown(f'<div style="font-size:14px; font-weight:600; margin-top:2px;">{value}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Compact per-resource card
# ---------------------------------------------------------------------------

def _truncate(text: str | None, length: int = 160) -> str:
    text = text or "No summary available."
    return text if len(text) <= length else text[: length - 1].rstrip() + "…"


def _render_resource_card(report: dict, report_key: str, is_expanded: bool) -> None:
    with card(key=f"resource_card_{report_key}"):
        col_info, col_action = st.columns([4, 1])
        with col_info:
            badge_html = severity_badge(report.get("severity", ""))
            if report.get("is_new"):
                badge_html += new_badge("NEW")
            elif report.get("severity_changed_from"):
                badge_html += new_badge("WORSE")
            st.markdown(
                f"""
                <div style="display:flex; align-items:center; gap:10px; margin-bottom:4px;">
                  <span style="font-size:14.5px; font-weight:700; color:var(--text-primary);">{report.get("instance_id", "—")}</span>
                  {badge_html}
                </div>
                <div style="font-size:12px; color:var(--text-muted); margin-bottom:6px;">{report.get("resource_type", "EC2 Instance")}</div>
                <div style="font-size:13px; color:var(--text-secondary); line-height:1.45;">{_truncate(report.get("summary"))}</div>
                """,
                unsafe_allow_html=True,
            )
        with col_action:
            st.markdown("<div style='height:0.3rem'></div>", unsafe_allow_html=True)
            label = "Hide Details" if is_expanded else "View Details"
            if st.button(label, key=f"toggle_{report_key}", width="stretch"):
                st.session_state[_EXPANDED_KEY] = None if is_expanded else report_key
                st.rerun()


# ---------------------------------------------------------------------------
# Expanded detail cards - each clearly separated, no continuous scroll block
# ---------------------------------------------------------------------------

def _render_resource_overview_card(report: dict) -> None:
    with card():
        card_title("Overview")
        cols = st.columns(4)
        fields = [
            ("Resource ID", report.get("instance_id", "—")),
            ("Resource Type", report.get("resource_type", "—")),
            ("Detected At", format_datetime(report.get("detected_at"))),
            ("Severity", None),
        ]
        for col, (label, value) in zip(cols, fields):
            with col:
                st.markdown(f'<div class="ao-kpi-label">{label}</div>', unsafe_allow_html=True)
                if label == "Severity":
                    st.markdown(severity_badge(report.get("severity", "")), unsafe_allow_html=True)
                elif label == "Resource ID":
                    st.markdown(f'<span class="ao-mono" style="font-size:13px;">{value}</span>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<div style="font-size:14px; font-weight:600; margin-top:2px;">{value}</div>', unsafe_allow_html=True)
    st.markdown("<div style='height:0.9rem'></div>", unsafe_allow_html=True)


def _render_ai_investigation_card(report: dict) -> None:
    with card():
        card_title("AI Investigation")
        badge_html = severity_badge(report.get("severity", ""))
        if report.get("is_new"):
            badge_html += new_badge("NEW")
        elif report.get("severity_changed_from"):
            badge_html += new_badge("WORSE")
        st.markdown(
            f"""
            <div style="display:flex; align-items:center; gap:10px; margin-bottom:0.6rem;">
              {badge_html}
              <span class="ao-mono" style="font-size:12.5px; color:var(--text-secondary);">{report.get("instance_id", "—")}</span>
            </div>
            <div style="font-size:14px; color:var(--text-primary); line-height:1.5;">{report.get("summary", "No summary available.")}</div>
            """,
            unsafe_allow_html=True,
        )
    st.markdown("<div style='height:0.9rem'></div>", unsafe_allow_html=True)


def _render_root_cause_card(report: dict) -> None:
    with card():
        card_title("Root Cause")
        root_cause = report.get("root_cause")
        if root_cause:
            st.markdown(
                f"""
                <div style="background:var(--red-soft); border:1px solid rgba(239,68,68,0.3); border-radius:var(--radius-sm);
                            padding:0.9rem 1.1rem; font-size:14px; color:var(--text-primary); line-height:1.5;">
                  {root_cause}
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            empty_state("Root cause not yet determined", "Gemini AI analysis is still in progress or unavailable", "🧩")
    st.markdown("<div style='height:0.9rem'></div>", unsafe_allow_html=True)


def _render_evidence_card(report: dict) -> None:
    with card():
        card_title("Evidence")
        evidence = report.get("evidence") or []
        if evidence:
            items_html = "".join(
                f'<div style="display:flex; gap:9px; padding:0.5rem 0; border-bottom:1px solid var(--border-subtle);">'
                f'<span style="color:var(--text-muted); font-size:12px; margin-top:2px;">▸</span>'
                f'<span style="font-size:13.5px; color:var(--text-primary); line-height:1.45;">{e}</span></div>'
                for e in evidence
            )
            st.markdown(items_html, unsafe_allow_html=True)
        else:
            empty_state("No evidence collected", "", "📄")


def _render_recommendations_card(report: dict) -> None:
    with card():
        card_title("Recommendations")
        recommendations = report.get("recommendations") or []
        if recommendations:
            items_html = "".join(
                f'<div style="display:flex; gap:9px; padding:0.5rem 0; border-bottom:1px solid var(--border-subtle);">'
                f'<span style="color:var(--green); font-size:13px; margin-top:2px;">✓</span>'
                f'<span style="font-size:13.5px; color:var(--text-primary); line-height:1.45;">{r}</span></div>'
                for r in recommendations
            )
            st.markdown(items_html, unsafe_allow_html=True)
        else:
            empty_state("No recommendations yet", "", "💡")


def _metric_text(value, suffix: str = "%") -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        return f"{value}{suffix}"
    return str(value)


def _render_kpi_row(cols, fields) -> None:
    for col, (label, value) in zip(cols, fields):
        with col:
            st.markdown(f'<div class="ao-kpi-label">{label}</div>', unsafe_allow_html=True)
            st.markdown(f'<div style="font-size:14px; font-weight:600; margin-top:2px;">{value}</div>', unsafe_allow_html=True)


def _render_telemetry_card(report: dict) -> None:
    with card():
        card_title("Telemetry")
        telemetry = report.get("telemetry")
        if not telemetry:
            empty_state("No telemetry captured", "This report predates telemetry tracking, or the underlying data was unavailable", "📉")
            return

        # report["resource_type"] here is already the *labeled* value
        # dashboard_export.py's label_resource_type() produces - "EC2
        # Instance" for EC2 (not "EC2"), "Load Balancer" and "Auto Scaling
        # Group" pass through unchanged - matching utils/dashboard_export.py's
        # build_telemetry() field shapes for each type exactly.
        resource_type = report.get("resource_type")

        if resource_type == "Load Balancer":
            _render_load_balancer_telemetry(telemetry)
        elif resource_type == "Auto Scaling Group":
            _render_auto_scaling_group_telemetry(telemetry)
        else:
            _render_ec2_telemetry(telemetry)


def _render_ec2_telemetry(telemetry: dict) -> None:
    row1 = st.columns(4)
    row1_fields = [
        ("State", str(telemetry.get("state") or "—").title()),
        ("CPU", _metric_text(telemetry.get("cpu"))),
        ("Memory", _metric_text(telemetry.get("memory"))),
        ("Disk", _metric_text(telemetry.get("disk"))),
    ]
    _render_kpi_row(row1, row1_fields)

    st.markdown("<div style='height:0.7rem'></div>", unsafe_allow_html=True)

    row2 = st.columns(3)
    row2_fields = [
        ("Network In", format_number(telemetry.get("network_in"))),
        ("Network Out", format_number(telemetry.get("network_out"))),
        ("Status Check", telemetry.get("status_check") or "—"),
    ]
    _render_kpi_row(row2, row2_fields)


def _render_load_balancer_telemetry(telemetry: dict) -> None:
    row1 = st.columns(3)
    row1_fields = [
        ("Request Count", format_number(telemetry.get("request_count"))),
        ("Target Response Time", _metric_text(telemetry.get("target_response_time"), suffix="s")),
        ("Healthy Targets", format_number(telemetry.get("healthy_targets"))),
    ]
    _render_kpi_row(row1, row1_fields)

    st.markdown("<div style='height:0.7rem'></div>", unsafe_allow_html=True)

    row2 = st.columns(3)
    row2_fields = [
        ("Unhealthy Targets", format_number(telemetry.get("unhealthy_targets"))),
        ("HTTP 4XX", format_number(telemetry.get("http_4xx"))),
        ("HTTP 5XX", format_number(telemetry.get("http_5xx"))),
    ]
    _render_kpi_row(row2, row2_fields)


def _render_auto_scaling_group_telemetry(telemetry: dict) -> None:
    row1 = st.columns(4)
    row1_fields = [
        ("Desired Capacity", format_number(telemetry.get("desired_capacity"))),
        ("In Service", format_number(telemetry.get("in_service_instances"))),
        ("Pending", format_number(telemetry.get("pending_instances"))),
        ("Standby", format_number(telemetry.get("standby_instances"))),
    ]
    _render_kpi_row(row1, row1_fields)

    scaling_activities = telemetry.get("scaling_activities") or []
    if scaling_activities:
        st.markdown("<div style='height:0.9rem'></div>", unsafe_allow_html=True)
        st.markdown('<div class="ao-kpi-label" style="margin-bottom:0.4rem;">Recent Scaling Activities</div>', unsafe_allow_html=True)
        items_html = "".join(
            f'<div style="display:flex; gap:9px; padding:0.4rem 0; border-bottom:1px solid var(--border-subtle);">'
            f'<span style="color:var(--text-muted); font-size:12px; margin-top:2px;">▸</span>'
            f'<span style="font-size:13px; color:var(--text-primary); line-height:1.45;">'
            f'<strong>{activity.get("status") or "—"}</strong> — {activity.get("description") or "No description"}</span></div>'
            for activity in scaling_activities[:5]
        )
        st.markdown(items_html, unsafe_allow_html=True)


def _render_confidence_card(report: dict) -> None:
    with card():
        card_title("Confidence")
        confidence = report.get("ai_confidence")
        if confidence is None:
            empty_state("No confidence score", "Gemini didn't return a confidence value for this report", "🎯")
            return
        pct = min(max(float(confidence), 0.0), 1.0)
        st.markdown(
            f'<div style="font-size:28px; font-weight:700; color:var(--text-primary); margin-bottom:0.6rem;">{pct * 100:.0f}%</div>',
            unsafe_allow_html=True,
        )
        st.progress(pct)

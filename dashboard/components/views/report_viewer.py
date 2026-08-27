"""Report Viewer — the dedicated place to review a completed investigation.

Reached only via a link/button (Execution History's "Open Report →", or the
launcher's own "View Full Report →" on completion) - never a persistent
sidebar item, since a report only makes sense in the context of a specific
run_id. Investigation (investigation.py) stays focused on launching runs and
showing live progress; this page is where the *result* actually lives.
"""
from __future__ import annotations

import streamlit as st

from services import FollowUpActionError

from ..badges import new_badge, severity_badge, status_badge
from ..cards import card, card_title, empty_state
from ..formatting import format_datetime, format_duration, format_number
from ..topbar import page_header

_SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
_RUN_ID_KEY = "report_viewer_run_id"
_EXPANDED_KEY = "report_viewer_expanded_id"
_FOLLOW_UP_ERROR_KEY = "report_viewer_follow_up_error"


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
            _render_follow_up_card(services, run_id, report, report_key)

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


# ---------------------------------------------------------------------------
# Follow-up Q&A - "Ask AegisOps". Evidence-grounded questions about this
# specific resource's RCA - see api/follow_up_manager.py for the backend.
# This section only renders what the backend actually returns; it never
# computes, guesses, or fabricates an answer itself. The original RCA
# above is never modified by anything in this section.
# ---------------------------------------------------------------------------

_CONFIDENCE_COLORS = {
    "HIGH": ("#86efac", "rgba(34,197,94,0.14)"),
    "MEDIUM": ("#fdba74", "rgba(245,158,11,0.14)"),
    "LOW": ("#94a3b8", "rgba(148,163,184,0.14)"),
}


def _confidence_badge(confidence: str) -> str:
    fg, bg = _CONFIDENCE_COLORS.get(str(confidence or "").upper(), _CONFIDENCE_COLORS["LOW"])
    return f'<span style="background:{bg}; color:{fg}; padding:2px 9px; border-radius:999px; font-size:11px; font-weight:700;">{confidence}</span>'


def _investigation_id_for(run_id: str, report: dict) -> str | None:
    resource_id = report.get("report_id") or report.get("instance_id")
    if not resource_id:
        return None
    return f"{run_id}__{resource_id}"


def _render_conversation_turn(turn: dict) -> None:
    role = turn.get("role")
    content = turn.get("content") or ""

    if role == "user":
        st.markdown(
            f"""
            <div style="margin-bottom:0.6rem;">
              <div style="font-size:11px; font-weight:700; letter-spacing:0.05em; color:var(--text-muted); margin-bottom:3px;">YOU ASKED</div>
              <div style="font-size:14px; color:var(--text-primary);">{content}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    evidence_used = turn.get("evidence_used") or []
    evidence_html = ""
    if evidence_used:
        items = "".join(
            f'<div style="display:flex; gap:8px; padding:0.2rem 0;">'
            f'<span style="color:var(--text-muted); font-size:12px;">▸</span>'
            f'<span style="font-size:12.5px; color:var(--text-secondary); line-height:1.4;">'
            f'{e.get("observation") or ""}'
            f'{" — " + e.get("signal") if e.get("signal") else ""}'
            f'{" (" + e.get("timestamp") + ")" if e.get("timestamp") else ""}</span></div>'
            for e in evidence_used if isinstance(e, dict) and e.get("observation")
        )
        if items:
            evidence_html = (
                '<div style="margin-top:0.5rem;">'
                '<div style="font-size:11px; font-weight:700; letter-spacing:0.05em; color:var(--text-muted); margin-bottom:2px;">EVIDENCE</div>'
                f"{items}</div>"
            )

    uncertainties = turn.get("uncertainties") or []
    uncertainty_html = ""
    if uncertainties:
        items = "".join(
            f'<div style="font-size:12px; color:var(--text-muted); padding:0.15rem 0;">⚠ {u}</div>'
            for u in uncertainties if u
        )
        uncertainty_html = f'<div style="margin-top:0.4rem;">{items}</div>'

    confidence = turn.get("confidence")
    confidence_html = _confidence_badge(confidence) if confidence else ""

    st.markdown(
        f"""
        <div style="background:var(--bg-elevated); border:1px solid var(--border-subtle); border-radius:var(--radius-sm);
                    padding:0.8rem 1rem; margin-bottom:0.8rem;">
          <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:8px; margin-bottom:0.4rem;">
            <div style="font-size:11px; font-weight:700; letter-spacing:0.05em; color:var(--text-muted);">AEGISOPS</div>
            {confidence_html}
          </div>
          <div style="font-size:14px; color:var(--text-primary); line-height:1.5;">{content}</div>
          {evidence_html}
          {uncertainty_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_follow_up_card(services, run_id: str, report: dict, report_key: str) -> None:
    investigation_id = _investigation_id_for(run_id, report)

    with card():
        card_title("Ask AegisOps", "Ask a question about this investigation")

        if not investigation_id:
            empty_state("Follow-up questions aren't available for this report", "", "💬")
            return

        error = st.session_state.pop(f"{_FOLLOW_UP_ERROR_KEY}_{report_key}", None)

        try:
            conversation_data = services.follow_up.get_conversation(investigation_id)
        except FollowUpActionError as exc:
            st.info(str(exc))
            conversation_data = {"conversation": []}

        conversation = conversation_data.get("conversation") or []

        if conversation:
            for turn in conversation:
                _render_conversation_turn(turn)
            st.markdown("<div style='height:0.4rem'></div>", unsafe_allow_html=True)

        if error:
            st.error(error)

        input_key = f"follow_up_question_{report_key}"
        col_input, col_button = st.columns([5, 1])
        with col_input:
            question = st.text_input(
                "Ask a question about this investigation…",
                key=input_key,
                label_visibility="collapsed",
                placeholder="Ask a question about this investigation…",
            )
        with col_button:
            ask_clicked = st.button("Ask", key=f"follow_up_ask_{report_key}", width="stretch", type="primary")

        if ask_clicked:
            _handle_follow_up_question(services, investigation_id, question, report_key, input_key)


def _handle_follow_up_question(services, investigation_id: str, question: str, report_key: str, input_key: str) -> None:
    if not (question or "").strip():
        st.session_state[f"{_FOLLOW_UP_ERROR_KEY}_{report_key}"] = "Please enter a question before clicking Ask."
        st.rerun()
        return

    try:
        with st.spinner("AegisOps is reviewing the investigation evidence…"):
            services.follow_up.ask(investigation_id, question)
    except FollowUpActionError as exc:
        # The investigation data itself is untouched and the prior
        # conversation is not lost - only this one new question failed.
        st.session_state[f"{_FOLLOW_UP_ERROR_KEY}_{report_key}"] = str(exc)
        st.rerun()
        return

    # Clear the input for the next question - popped before rerun so the
    # widget re-renders empty instead of keeping the just-asked text.
    st.session_state.pop(input_key, None)
    st.rerun()

"""Investigation page — launch a run, then show only its own result.

This page never browses arbitrary history anymore (that's Execution
History's job - see history.py). It shows exactly one investigation at a
time: whichever run this session just linked to (History's "Open Report",
or the launcher's own "View Full Report" button - both funnel through the
same `preselected_run_id` session key set in dashboard/app.py), the run
this session is actively tracking, or - failing both - the most recently
completed execution.
"""
from __future__ import annotations

import streamlit as st

from ..badges import new_badge, severity_badge, status_badge
from ..cards import card, card_title, empty_state
from ..formatting import format_duration, format_number
from ..investigation_launcher import render_launcher
from ..resource_tree import render_resource_tree
from ..topbar import page_header

_SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
_SHOW_FULL_KEY = "inv_show_full_report"


def render(services, config) -> None:
    page_header("Investigation", "Launch a new AI investigation and review its current result")

    render_launcher(services, config)

    run_id, run_summary = _resolve_current_run(services)
    if run_id is None:
        return

    st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)
    _render_execution_summary(run_id, run_summary)

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    reports = services.report.get_reports_for_run(run_id)
    _render_latest_report_section(run_id, reports)


# ---------------------------------------------------------------------------
# Which run is "current"
# ---------------------------------------------------------------------------

def _resolve_current_run(services):
    preselected = st.session_state.pop("preselected_run_id", None)

    run_id = preselected
    if not run_id:
        active = st.session_state.get("launch_active_run")
        if active:
            run_id = active.get("run_id")

    executions = services.history.list_executions()

    if not run_id:
        if not executions:
            with card():
                empty_state("No investigations yet", "Launch one above to see its result here", "🔍")
            return None, None
        latest = executions[0]
        return latest.get("run_id"), latest

    run_summary = next((e for e in executions if e.get("run_id") == run_id), None)
    return run_id, run_summary


# ---------------------------------------------------------------------------
# Execution Summary strip
# ---------------------------------------------------------------------------

def _render_execution_summary(run_id: str, run_summary: dict | None) -> None:
    run_summary = run_summary or {}
    with card():
        card_title("Execution Summary")
        cols = st.columns(6)
        fields = [
            ("Run ID", run_id or "—"),
            ("Status", None),
            ("Resources", format_number(run_summary.get("resources"))),
            ("Successful", format_number(run_summary.get("successful"))),
            ("Failed", format_number(run_summary.get("failed"))),
            ("Duration", format_duration(run_summary.get("execution_time"))),
        ]
        for col, (label, value) in zip(cols, fields):
            with col:
                st.markdown(f'<div class="ao-kpi-label">{label}</div>', unsafe_allow_html=True)
                if label == "Status":
                    status = run_summary.get("status")
                    st.markdown(status_badge(status) if status else "—", unsafe_allow_html=True)
                elif label == "Run ID":
                    st.markdown(f'<span class="ao-mono" style="font-size:13px;">{value}</span>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<div style="font-size:16px; font-weight:700; margin-top:2px;">{value}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Latest Investigation Report
# ---------------------------------------------------------------------------

def _featured_report(reports: list[dict]) -> dict | None:
    if not reports:
        return None
    return max(reports, key=lambda r: _SEVERITY_RANK.get(str(r.get("severity", "")).upper(), 0))


def _render_latest_report_section(run_id: str, reports: list[dict]) -> None:
    st.markdown(
        """
        <div class="ao-section-header">
          <div class="ao-section-header-title">Latest Investigation Report</div>
          <div class="ao-section-header-subtitle">The AI root cause analysis for this investigation.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not reports:
        with card():
            empty_state("No AI report yet", "Still running, or the backend hasn't published a report for this run", "🤖")
        return

    featured = _featured_report(reports)
    others = len(reports) - 1

    with card():
        badge_html = severity_badge(featured.get("severity", ""))
        if featured.get("is_new"):
            badge_html += new_badge("NEW")
        elif featured.get("severity_changed_from"):
            badge_html += new_badge("WORSE")

        resource_label = featured.get("instance_id", "—")
        if others > 0:
            resource_label += f" (+{others} more resource{'s' if others > 1 else ''})"

        st.markdown(
            f"""
            <div style="display:flex; align-items:center; gap:10px; margin-bottom:0.6rem;">
              {badge_html}
              <span class="ao-mono" style="font-size:12.5px; color:var(--text-secondary);">{resource_label}</span>
            </div>
            <div style="font-size:14px; color:var(--text-primary); line-height:1.5; margin-bottom:0.9rem;">{featured.get("summary", "No summary available.")}</div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown('<div class="ao-kpi-label" style="margin-bottom:0.4rem;">Root Cause</div>', unsafe_allow_html=True)
        root_cause = featured.get("root_cause")
        if root_cause:
            st.markdown(
                f"""
                <div style="background:var(--red-soft); border:1px solid rgba(239,68,68,0.3); border-radius:var(--radius-sm);
                            padding:0.8rem 1rem; font-size:13.5px; color:var(--text-primary); line-height:1.5; margin-bottom:0.9rem;">
                  {root_cause}
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div style="font-size:13px; color:var(--text-muted); margin-bottom:0.9rem;">Not yet determined.</div>',
                unsafe_allow_html=True,
            )

        st.markdown('<div class="ao-kpi-label" style="margin-bottom:0.4rem;">Recommendations</div>', unsafe_allow_html=True)
        recommendations = featured.get("recommendations") or []
        if recommendations:
            items_html = "".join(
                f'<div style="display:flex; gap:9px; padding:0.4rem 0; border-bottom:1px solid var(--border-subtle);">'
                f'<span style="color:var(--green); font-size:13px; margin-top:2px;">✓</span>'
                f'<span style="font-size:13.5px; color:var(--text-primary); line-height:1.45;">{r}</span></div>'
                for r in recommendations
            )
            st.markdown(items_html, unsafe_allow_html=True)
        else:
            st.markdown('<div style="font-size:13px; color:var(--text-muted);">None yet.</div>', unsafe_allow_html=True)

        st.markdown("<div style='height:0.8rem'></div>", unsafe_allow_html=True)
        if st.button("View Full Report →", key="inv_view_full_report", type="primary"):
            st.session_state[_SHOW_FULL_KEY] = run_id
            st.rerun()

    # Keyed by run_id so navigating to a different investigation doesn't
    # leave a stale expanded report showing.
    if st.session_state.get(_SHOW_FULL_KEY) == run_id:
        st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)
        for report in reports:
            if len(reports) > 1:
                st.markdown(
                    f'<div class="ao-section-header-subtitle" style="margin:0.8rem 0 0.4rem;">{report.get("instance_id", "resource")}</div>',
                    unsafe_allow_html=True,
                )
            _render_full_report(report)
            st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)


def _render_full_report(report: dict) -> None:
    col1, col2 = st.columns([1, 1.3])

    with col1, card():
        card_title("Resource Tree")
        render_resource_tree(report.get("resource_tree"))

    with col2, card():
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
        confidence = report.get("ai_confidence")
        if confidence is not None:
            st.markdown(
                '<div style="margin-top:0.75rem; font-size:12px; color:var(--text-muted);">AI Confidence</div>',
                unsafe_allow_html=True,
            )
            st.progress(min(max(float(confidence), 0.0), 1.0))

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

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

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    col3, col4 = st.columns(2)

    with col3, card():
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

    with col4, card():
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

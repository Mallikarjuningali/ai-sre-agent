"""Investigation page — deep dive into one execution's AI root cause report."""
from __future__ import annotations

import streamlit as st

from ..badges import new_badge, severity_badge, status_badge
from ..cards import card, card_title, empty_state
from ..formatting import format_duration, format_number, format_datetime
from ..investigation_launcher import render_launcher
from ..resource_tree import render_resource_tree
from ..topbar import page_header


def render(services, config) -> None:
    page_header("Investigation", "Launch a new AI investigation, or review a past execution's root cause analysis")

    render_launcher(services, config)

    st.markdown(
        """
        <div class="ao-section-header">
          <div class="ao-section-header-title">Past Executions</div>
          <div class="ao-section-header-subtitle">Browse a previous run and its AI-generated root cause analysis.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    executions = services.history.list_executions()

    if not executions:
        with card():
            empty_state("No executions available", "Run an investigation from the backend to see results here", "🔍")
        return

    run_options = {f'{e.get("run_id", "unknown")} · {e.get("type", "Execution")}': e for e in executions}
    labels = list(run_options.keys())

    preselected_run_id = st.session_state.pop("preselected_run_id", None)
    if preselected_run_id:
        for label, e in run_options.items():
            if e.get("run_id") == preselected_run_id:
                st.session_state["inv_run_select"] = label
                break

    col_a, col_b = st.columns([1.4, 1])
    with col_a:
        selected_label = st.selectbox("Execution", labels, key="inv_run_select")
    selected_run = run_options[selected_label]
    run_id = selected_run.get("run_id")

    reports = services.report.get_reports_for_run(run_id)
    if not reports:
        # Backend may not have linked reports to run_id yet; fall back to all reports.
        reports = services.report.list_reports()

    report = None
    with col_b:
        if reports:
            report_options = {
                f'{r.get("instance_id", r.get("report_id", "resource"))} · {r.get("severity", "—")}': r
                for r in reports
            }
            selected_report_label = st.selectbox("Investigated Resource", list(report_options.keys()), key="inv_report_select")
            report = report_options[selected_report_label]
        else:
            st.selectbox("Investigated Resource", ["No reports available"], key="inv_report_select_empty", disabled=True)

    st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)

    # --- Execution Summary --------------------------------------------
    with card():
        card_title("Execution Summary")
        cols = st.columns(6)
        fields = [
            ("Run ID", selected_run.get("run_id", "—")),
            ("Status", None),
            ("Resources", format_number(selected_run.get("resources"))),
            ("Successful", format_number(selected_run.get("successful"))),
            ("Failed", format_number(selected_run.get("failed"))),
            ("Duration", format_duration(selected_run.get("execution_time"))),
        ]
        for col, (label, value) in zip(cols, fields):
            with col:
                st.markdown(f'<div class="ao-kpi-label">{label}</div>', unsafe_allow_html=True)
                if label == "Status":
                    status = selected_run.get("status")
                    st.markdown(status_badge(status) if status else "—", unsafe_allow_html=True)
                elif label == "Run ID":
                    st.markdown(f'<span class="ao-mono" style="font-size:13px;">{value}</span>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<div style="font-size:16px; font-weight:700; margin-top:2px;">{value}</div>', unsafe_allow_html=True)

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    if report is None:
        with card():
            empty_state("No AI report for this execution", "The backend has not published a report for this run yet", "🤖")
        return

    # --- Resource Tree / AI Investigation ------------------------------
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

    # --- Root Cause ------------------------------------------------------
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

    # --- Evidence / Recommendations --------------------------------------
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

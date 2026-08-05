"""Execution History page — search, filter and open past execution reports."""
from __future__ import annotations

import streamlit as st

from ..badges import status_badge
from ..cards import card, card_title, empty_state
from ..formatting import format_duration
from ..topbar import page_header


def render(services, config) -> None:
    page_header("Execution History", "Search and review previous investigation executions")

    executions = services.history.list_executions()

    with card():
        card_title("Previous Executions", f"{len(executions)} total")

        col_search, col_status, col_type = st.columns([2, 1, 1])
        with col_search:
            query = st.text_input("Search", placeholder="Search by run ID or type…", label_visibility="collapsed")
        with col_status:
            statuses = ["All"] + sorted({e.get("status") for e in executions if e.get("status")})
            status_filter = st.selectbox("Status", statuses, label_visibility="collapsed")
        with col_type:
            types = ["All"] + sorted({e.get("type") for e in executions if e.get("type")})
            type_filter = st.selectbox("Type", types, label_visibility="collapsed")

        filtered = services.history.search(executions, query)
        filtered = services.history.filter_by_status(filtered, status_filter)
        if type_filter != "All":
            filtered = [e for e in filtered if e.get("type") == type_filter]

        st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)

        if not filtered:
            empty_state("No executions match your filters", "Try clearing the search or filters above", "🔍")
            return

        columns = ["Execution ID", "Type", "Status", "Resources", "Successful", "Failed", "Duration", "Start Time", ""]
        head = "".join(f"<th>{c}</th>" for c in columns)
        rows_html = []
        for run in filtered:
            run_id = run.get("run_id", "—")
            open_link = f'<a class="ao-link-btn" href="?page=investigation&run_id={run_id}" target="_self">Open Report →</a>'
            cells = [
                f'<span class="ao-mono">{run_id}</span>',
                run.get("type", "—"),
                status_badge(run.get("status", "")) if run.get("status") else "—",
                str(run.get("resources", "—")),
                str(run.get("successful", "—")),
                str(run.get("failed", "—")),
                format_duration(run.get("execution_time")),
                run.get("start_time", "—"),
                open_link,
            ]
            rows_html.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")

        st.markdown(
            f"""
            <div style="overflow-x:auto;">
            <table class="ao-table">
              <thead><tr>{head}</tr></thead>
              <tbody>{"".join(rows_html)}</tbody>
            </table>
            </div>
            """,
            unsafe_allow_html=True,
        )

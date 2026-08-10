"""Execution History page — search, filter, review, and manage previous executions."""
from __future__ import annotations

import html

import streamlit as st

from services import ExecutionActionError, invalidate

from ..badges import status_badge
from ..cards import card, card_title, empty_state
from ..formatting import format_duration
from ..icons import svg_icon
from ..topbar import page_header

_SELECTED_KEY = "history_selected_run_ids"
_DELETE_ERROR_KEY = "history_delete_error"


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

        selected_ids = _render_selection_bar(services, filtered)
        _render_delete_error()

        st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)

        columns = ["Execution ID", "Type", "Status", "Resources", "Successful", "Failed", "Duration", "Start Time", ""]
        head = "".join(f"<th>{c}</th>" for c in columns)
        rows_html = []
        for run in filtered:
            run_id = run.get("run_id", "—")
            open_link = f'<a class="ao-link-btn" href="?page=report&run_id={run_id}" target="_self">Open Report →</a>'
            row_class = "ao-row-selected" if run_id in selected_ids else ""
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
            rows_html.append(f'<tr class="{row_class}">' + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")

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


# ---------------------------------------------------------------------------
# Selection + delete action
# ---------------------------------------------------------------------------

def _render_selection_bar(services, filtered: list[dict]) -> set[str]:
    run_ids = [e.get("run_id") for e in filtered if e.get("run_id")]
    label_map = {
        e.get("run_id"): f'{e.get("run_id")} · {e.get("status", "—")} · {e.get("start_time") or "—"}'
        for e in filtered
        if e.get("run_id")
    }

    # A search/filter change can shrink `run_ids` out from under a selection
    # already stored under this widget's key - Streamlit requires stored
    # values to be a subset of the current options, so prune first.
    stored = st.session_state.get(_SELECTED_KEY, [])
    valid_stored = [r for r in stored if r in run_ids]
    if valid_stored != stored:
        st.session_state[_SELECTED_KEY] = valid_stored

    col_select, col_action = st.columns([3, 1])
    with col_select:
        chosen = st.multiselect(
            "Select executions",
            run_ids,
            format_func=lambda r: label_map.get(r, r),
            key=_SELECTED_KEY,
            label_visibility="collapsed",
            placeholder="Select executions to delete…",
        )
    with col_action:
        if st.button(
            f"Delete Selected ({len(chosen)})",
            key="history_delete_selected",
            type="secondary",
            width="stretch",
            disabled=not chosen,
        ):
            _confirm_delete_dialog(services, list(chosen), label_map)

    return set(chosen)


def _render_delete_error() -> None:
    error = st.session_state.pop(_DELETE_ERROR_KEY, None)
    if not error:
        return
    st.markdown(
        f"""
        <div class="ao-launch-error">
          {svg_icon("alert-triangle", size=15, color="#fca5a5")}
          <span>{html.escape(error)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


@st.dialog("Delete selected executions?")
def _confirm_delete_dialog(services, run_ids: list[str], label_map: dict) -> None:
    st.write(
        f"This will permanently delete **{len(run_ids)}** execution(s) and all associated "
        "reports, summaries, and archived data:"
    )
    for run_id in run_ids:
        st.markdown(f"- `{label_map.get(run_id, run_id)}`")
    st.caption("This cannot be undone. Running investigations are not affected.")

    col_cancel, col_confirm = st.columns(2)
    with col_cancel:
        if st.button("Cancel", key="history_delete_cancel", width="stretch"):
            st.rerun()
    with col_confirm:
        if st.button("Delete Permanently", key="history_delete_confirm", type="primary", width="stretch"):
            _perform_delete(services, run_ids)


def _perform_delete(services, run_ids: list[str]) -> None:
    try:
        services.execution.delete_executions(run_ids)
    except ExecutionActionError as exc:
        st.session_state[_DELETE_ERROR_KEY] = str(exc)
        st.rerun()
        return

    # Feed files were just rewritten on the backend - drop every cached read
    # so the next render pulls the fresh executions/reports/summary/etc.
    invalidate()
    st.session_state.pop(_SELECTED_KEY, None)
    st.success(f"Deleted {len(run_ids)} execution(s).")
    st.rerun()

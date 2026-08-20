"""Execution History page — search, filter, review, and manage previous executions."""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from services import ExecutionActionError, invalidate

from ..badges import status_badge
from ..cards import card, card_title, empty_state
from ..formatting import format_datetime, format_duration
from ..icons import svg_icon
from ..topbar import page_header

_SELECTED_KEY = "history_selected_run_ids"  # dict[run_id, bool] - persists across reruns/filter changes
_SELECT_ALL_KEY = "history_select_all"
_EDITOR_KEY = "history_selection_editor"
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

        _render_delete_error()
        _render_selection_table(services, filtered)


# ---------------------------------------------------------------------------
# Selection table (checkbox column + Select All) + delete action
# ---------------------------------------------------------------------------

def _render_selection_table(services, filtered: list[dict]) -> set[str]:
    visible_ids = [e.get("run_id") for e in filtered if e.get("run_id")]
    label_map = {
        e.get("run_id"): f'{e.get("run_id")} · {e.get("status", "—")} · {format_datetime(e.get("start_time"))}'
        for e in filtered
        if e.get("run_id")
    }

    # A search/filter change can shrink `visible_ids` out from under a
    # selection made before the filter changed - prune first, same intent
    # as the old dropdown's pruning, adapted to a dict keyed by run_id.
    selected = st.session_state.get(_SELECTED_KEY, {})
    selected = {run_id: value for run_id, value in selected.items() if run_id in visible_ids}
    st.session_state[_SELECTED_KEY] = selected

    # Keep the "Select All" checkbox's own displayed state in sync with
    # reality (e.g. after a row was toggled off individually) - must be
    # written to session_state before the checkbox widget is instantiated
    # below, not after.
    all_selected = bool(visible_ids) and all(selected.get(run_id, False) for run_id in visible_ids)
    if st.session_state.get(_SELECT_ALL_KEY) != all_selected:
        st.session_state[_SELECT_ALL_KEY] = all_selected

    st.checkbox(
        "Select All",
        key=_SELECT_ALL_KEY,
        on_change=_on_select_all_toggle,
        args=(visible_ids,),
    )

    # Re-read: the callback above (if it just fired) already updated this.
    selected = st.session_state.get(_SELECTED_KEY, {})

    rows = [
        {
            "Select": selected.get(run.get("run_id"), False),
            "Run ID": run.get("run_id") or "—",
            "Status": str(run.get("status") or "—").upper(),
            "Investigation Type": run.get("type") or "—",
            "Resources": run.get("resources"),
            "Successful": run.get("successful"),
            "Failed": run.get("failed"),
            "Duration": format_duration(run.get("execution_time")),
            "Started At": format_datetime(run.get("start_time")),
            "Report": f"?page=report&run_id={run.get('run_id')}" if run.get("run_id") else None,
        }
        for run in filtered
    ]
    df = pd.DataFrame(rows)

    edited_df = st.data_editor(
        df,
        column_config={
            "Select": st.column_config.CheckboxColumn(label="", default=False, width="small"),
            "Resources": st.column_config.NumberColumn(label="Resources", format="%d"),
            "Successful": st.column_config.NumberColumn(label="Successful", format="%d"),
            "Failed": st.column_config.NumberColumn(label="Failed", format="%d"),
            "Report": st.column_config.LinkColumn(label="Report", display_text="Open Report →"),
        },
        column_order=[
            "Select", "Run ID", "Status", "Investigation Type",
            "Resources", "Successful", "Failed", "Duration", "Started At", "Report",
        ],
        disabled=[
            "Run ID", "Status", "Investigation Type",
            "Resources", "Successful", "Failed", "Duration", "Started At", "Report",
        ],
        hide_index=True,
        num_rows="fixed",
        width="stretch",
        key=_EDITOR_KEY,
    )

    new_selected = {
        run_id: bool(is_selected)
        for run_id, is_selected in zip(visible_ids, edited_df["Select"].tolist())
    }
    st.session_state[_SELECTED_KEY] = new_selected

    selected_ids = {run_id for run_id, is_selected in new_selected.items() if is_selected and run_id}

    st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)

    if st.button(
        f"Delete Selected ({len(selected_ids)})",
        key="history_delete_selected",
        type="secondary",
        width="stretch",
        disabled=not selected_ids,
    ):
        _confirm_delete_dialog(services, sorted(selected_ids), label_map)

    return selected_ids


def _on_select_all_toggle(visible_ids: list[str]) -> None:
    new_value = st.session_state.get(_SELECT_ALL_KEY, False)
    st.session_state[_SELECTED_KEY] = {run_id: new_value for run_id in visible_ids}


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
    st.session_state.pop(_SELECT_ALL_KEY, None)
    st.success(f"Deleted {len(run_ids)} execution(s).")
    st.rerun()

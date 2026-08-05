"""Styled HTML table + list-row renderers."""
from __future__ import annotations

import html

import streamlit as st

from .cards import empty_state


def render_table(columns: list[str], rows: list[list[str]], empty_title: str = "No data",
                  empty_subtitle: str = "", empty_icon: str = "🗂️") -> None:
    if not rows:
        empty_state(empty_title, empty_subtitle, empty_icon)
        return

    head = "".join(f"<th>{html.escape(col)}</th>" for col in columns)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{cell}</td>" for cell in row)
        body_rows.append(f"<tr>{cells}</tr>")

    st.markdown(
        f"""
        <div style="overflow-x:auto;">
        <table class="ao-table">
          <thead><tr>{head}</tr></thead>
          <tbody>{"".join(body_rows)}</tbody>
        </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_list_rows(items_html: list[str], empty_title: str = "No data",
                      empty_subtitle: str = "", empty_icon: str = "🗂️") -> None:
    if not items_html:
        empty_state(empty_title, empty_subtitle, empty_icon)
        return
    st.markdown("".join(items_html), unsafe_allow_html=True)


def collector_row(name: str, status: str, icon_glyph: str = "⚙") -> str:
    from .badges import status_dot

    ok = str(status).strip().lower() == "healthy"
    color = "var(--green)" if ok else "var(--red)" if str(status).strip().lower() in ("down", "failed") else "var(--amber)"
    return f"""
    <div class="ao-list-row">
      <div class="ao-list-left">
        <div class="ao-list-icon">{icon_glyph}</div>
        <div class="ao-list-title">{html.escape(name)}</div>
      </div>
      <span class="ao-status-pill"><span class="ao-dot" style="background:{color}"></span>{html.escape(str(status))}</span>
    </div>
    """


def investigation_row(severity_badge_html: str, resource_id: str, summary: str, time_label: str) -> str:
    return f"""
    <div class="ao-investigation-row">
      <div style="display:flex; align-items:flex-start; justify-content:space-between; gap:10px;">
        <div style="min-width:0;">
          <div style="display:flex; align-items:center; gap:8px; margin-bottom:3px;">
            {severity_badge_html}
            <span class="ao-mono" style="font-size:12px; color:var(--text-secondary);">{html.escape(resource_id)}</span>
          </div>
          <div class="ao-list-subtitle" style="white-space:normal;">{html.escape(summary)}</div>
        </div>
        <div class="ao-investigation-time">{html.escape(time_label)}</div>
      </div>
    </div>
    """

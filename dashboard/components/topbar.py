"""Page header: title, subtitle, and optional right-aligned meta text."""
from __future__ import annotations

import html

import streamlit as st


def page_header(title: str, subtitle: str = "", right_html: str = "") -> None:
    st.markdown(
        f"""
        <div class="ao-page-header">
          <div>
            <div class="ao-page-title">{html.escape(title)}</div>
            <div class="ao-page-subtitle">{html.escape(subtitle)}</div>
          </div>
          <div>{right_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def last_refreshed_chip(timestamp_label: str) -> str:
    return (
        f'<div style="font-size:12px; color:var(--text-muted); border:1px solid var(--border-subtle); '
        f'background:var(--bg-surface); padding:0.45rem 0.85rem; border-radius:var(--radius-sm); '
        f'display:flex; align-items:center; gap:6px;">'
        f'<span class="ao-status-dot" style="background:var(--green); width:6px; height:6px;"></span>'
        f"Last updated {html.escape(timestamp_label)}</div>"
    )

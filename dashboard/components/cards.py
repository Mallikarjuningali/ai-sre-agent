"""Card-style layout primitives: KPI tiles, section cards, empty states."""
from __future__ import annotations

import html
import itertools
from contextlib import contextmanager

import streamlit as st

_card_id_counter = itertools.count()

_ICON_KIND_COLORS = {
    "accent": ("var(--accent)", "var(--accent-soft)"),
    "green": ("var(--green)", "var(--green-soft)"),
    "amber": ("var(--amber)", "var(--amber-soft)"),
    "orange": ("var(--orange)", "var(--orange-soft)"),
    "red": ("var(--red)", "var(--red-soft)"),
    "blue": ("var(--blue)", "var(--blue-soft)"),
}


def kpi_card(label: str, value: str, icon: str = "📊", icon_kind: str = "accent",
             delta: float | None = None, delta_label: str = "", delta_is_positive_good: bool = True) -> None:
    fg, bg = _ICON_KIND_COLORS.get(icon_kind, _ICON_KIND_COLORS["accent"])

    delta_html = ""
    if delta is not None:
        is_up = delta > 0
        is_flat = delta == 0
        good = (is_up and delta_is_positive_good) or ((not is_up) and not delta_is_positive_good)
        css_kind = "flat" if is_flat else ("up" if good else "down")
        arrow = "→" if is_flat else ("↑" if is_up else "↓")
        delta_text = html.escape(f"{arrow} {abs(delta):g}% {delta_label}".strip())
        delta_html = f'<div class="ao-kpi-delta ao-kpi-delta-{css_kind}">{delta_text}</div>'

    st.markdown(
        f"""
        <div class="ao-kpi">
          <div class="ao-kpi-row">
            <div>
              <div class="ao-kpi-label">{html.escape(label)}</div>
              <div class="ao-kpi-value">{html.escape(str(value))}</div>
            </div>
            <div class="ao-kpi-icon" style="background:{bg}; color:{fg};">{icon}</div>
          </div>
          {delta_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def card_title(title: str, tag: str = "") -> None:
    tag_html = f'<span class="ao-card-title-tag">{html.escape(tag)}</span>' if tag else ""
    st.markdown(
        f'<div class="ao-card-title"><span>{html.escape(title)}</span>{tag_html}</div>',
        unsafe_allow_html=True,
    )


@contextmanager
def card():
    """A bordered card panel. Use as `with card(): ...` so every child
    element (charts, tables, widgets) genuinely nests inside the card's
    DOM node — unlike a raw markdown '<div>' opened/closed across separate
    st.markdown calls, which the browser silently auto-closes."""
    key = f"ao_card_{next(_card_id_counter)}"
    with st.container(border=True, key=key):
        yield


def empty_state(title: str, subtitle: str = "", icon: str = "🗂️") -> None:
    st.markdown(
        f"""
        <div class="ao-empty-state">
          <div class="ao-empty-icon">{icon}</div>
          <div class="ao-empty-title">{html.escape(title)}</div>
          <div class="ao-empty-sub">{html.escape(subtitle)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

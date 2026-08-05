"""Left navigation sidebar."""
from __future__ import annotations

import streamlit as st

from .icons import svg_icon

NAV_ITEMS = [
    ("overview", "Overview", ":material/grid_view:"),
    ("investigation", "Investigation", ":material/search:"),
    ("history", "Execution History", ":material/history:"),
    ("analytics", "Analytics", ":material/monitoring:"),
    ("settings", "Settings", ":material/settings:"),
]


def render_sidebar(active_page: str, collector_health: dict[str, str] | None = None) -> None:
    with st.sidebar:
        st.markdown(
            f"""
            <div class="ao-brand">
              <div class="ao-brand-mark">{svg_icon("shield", size=18, color="white")}</div>
              <div class="ao-brand-text">
                <div class="ao-brand-title">AegisOps AI</div>
                <div class="ao-brand-sub">Cloud Investigation Platform</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        for key, label, icon in NAV_ITEMS:
            is_active = key == active_page
            css_class = "ao-nav-active" if is_active else ""
            st.markdown(f'<div class="{css_class}">', unsafe_allow_html=True)
            if st.button(label, key=f"nav_{key}", width="stretch", icon=icon):
                st.session_state["active_page"] = key
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)

        collector_health = collector_health or {}
        total = len(collector_health)
        healthy = sum(1 for v in collector_health.values() if str(v).strip().lower() == "healthy")
        all_ok = total > 0 and healthy == total
        if total == 0:
            dot_color, status_text = "var(--gray)", "No collector data"
        elif all_ok:
            dot_color, status_text = "var(--green)", "All Systems Operational"
        else:
            dot_color, status_text = "var(--amber)", f"{healthy}/{total} Collectors Healthy"

        st.markdown(
            f"""
            <div class="ao-sidebar-footer">
              <div class="ao-sidebar-footer-title">System Status</div>
              <div style="font-size:13px; font-weight:600; color:var(--text-primary);">
                <span class="ao-status-dot" style="background:{dot_color}"></span>{status_text}
              </div>
              <div class="ao-version">AegisOps AI · Dashboard v1.0.0</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

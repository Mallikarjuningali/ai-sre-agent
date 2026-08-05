"""
AegisOps AI — Dashboard entrypoint.

Frontend only. Reads pre-computed JSON published by the AegisOps AI backend
(collectors -> CloudWatch/CloudTrail/ALB/Auto Scaling -> Gemini AI root cause
analysis) via a swappable DataSource (local files / S3 / REST API — see
services/data_source.py and the Settings page). This file only wires
config -> services -> the active page; all data and business logic live in
services/, all layout/markup lives in components/.
"""
from __future__ import annotations

import streamlit as st

from components.sidebar import render_sidebar
from components.theme import configure_page, inject_theme
from components.views import analytics, history, investigation, overview, settings
from services import DataSourceError, build_services, load_config

PAGES = {
    "overview": ("Overview", overview),
    "investigation": ("Investigation", investigation),
    "history": ("Execution History", history),
    "analytics": ("Analytics", analytics),
    "settings": ("Settings", settings),
}


def _consume_query_params() -> None:
    params = st.query_params
    page = params.get("page")
    run_id = params.get("run_id")

    if page in PAGES:
        st.session_state["active_page"] = page
    if run_id:
        st.session_state["preselected_run_id"] = run_id

    if page or run_id:
        st.query_params.clear()


def main() -> None:
    configure_page("Dashboard")
    inject_theme()

    _consume_query_params()
    st.session_state.setdefault("active_page", "overview")

    config = load_config()

    try:
        svc = build_services(config)
        collector_health = svc.collector.get_collector_health()
    except DataSourceError as exc:
        svc = None
        collector_health = {}
        sidebar_error = str(exc)
    else:
        sidebar_error = None

    render_sidebar(st.session_state["active_page"], collector_health=collector_health)

    active_page = st.session_state["active_page"]
    title, page_module = PAGES[active_page]

    if svc is None:
        st.error(
            f"Could not reach the configured backend data source.\n\n**{sidebar_error}**\n\n"
            "Open **Settings** to review the backend configuration."
        )
        if active_page != "settings":
            return
        # Settings page needs a working data source to build its own instance too,
        # so fall back to a local-only Services object just for this page's chrome.
        from services import AppConfig, build_services as _build

        svc = _build(AppConfig(data_source_type="local"))

    try:
        page_module.render(svc, config)
    except DataSourceError as exc:
        st.error(f"Failed to load data for **{title}**: {exc}")


if __name__ == "__main__":
    main()

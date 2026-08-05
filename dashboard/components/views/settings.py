"""Settings page — theme, refresh interval, backend (data source) configuration.

Persists to assets/config/settings.json via services.config.save_config, so
changes made here take effect immediately for every other page (app.py
rebuilds the services layer from config on every run).
"""
from __future__ import annotations

import streamlit as st

from services import AppConfig, save_config

from ..cards import card, card_title
from ..topbar import page_header

DATA_SOURCE_LABELS = {
    "local": "Local Files (dev/offline)",
    "s3": "Amazon S3",
    "rest": "REST API",
}
DATA_SOURCE_KEYS = list(DATA_SOURCE_LABELS.keys())


def render(services, config: AppConfig) -> None:
    page_header("Settings", "Theme, refresh behaviour and backend data source configuration")

    col1, col2 = st.columns([1, 1.4])

    with col1:
        with card():
            card_title("Theme")
            st.selectbox(
                "Appearance",
                ["Dark (Enterprise) — active", "Light — coming soon"],
                index=0,
                disabled=True,
                help="AegisOps AI ships dark-only today to match SRE/NOC tooling. Light theme is on the roadmap.",
            )

        st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

        with card():
            card_title("Refresh Interval")
            st.caption("How long service responses are cached before the dashboard re-reads the backend.")
            refresh_interval = st.slider(
                "Refresh interval (seconds)",
                min_value=5,
                max_value=300,
                value=int(config.refresh_interval_seconds),
                step=5,
                label_visibility="collapsed",
            )
            st.caption(f"Data refreshes at most every **{refresh_interval}s**.")

    with col2, card():
        card_title("Backend Configuration", services.data_source.label)

        current_index = DATA_SOURCE_KEYS.index(config.data_source_type) if config.data_source_type in DATA_SOURCE_KEYS else 0
        source_choice_label = st.selectbox(
            "Data source",
            [DATA_SOURCE_LABELS[k] for k in DATA_SOURCE_KEYS],
            index=current_index,
        )
        source_type = DATA_SOURCE_KEYS[[DATA_SOURCE_LABELS[k] for k in DATA_SOURCE_KEYS].index(source_choice_label)]

        st.markdown("<div style='height:0.4rem'></div>", unsafe_allow_html=True)

        with st.form("backend_config_form"):
            local_data_dir, s3_bucket, s3_prefix, aws_region, rest_base_url, rest_api_key = (
                config.local_data_dir,
                config.s3_bucket,
                config.s3_prefix,
                config.aws_region,
                config.rest_base_url,
                config.rest_api_key,
            )

            if source_type == "local":
                st.caption("Reads JSON fixtures from disk — used for local development and demos.")
                local_data_dir = st.text_input("Local data directory", value=config.local_data_dir)
            elif source_type == "s3":
                st.caption("Reads JSON objects the backend publishes to an S3 bucket. Recommended for production.")
                s3_bucket = st.text_input("S3 bucket", value=config.s3_bucket, placeholder="aegisops-reports")
                col_a, col_b = st.columns(2)
                with col_a:
                    s3_prefix = st.text_input("Key prefix", value=config.s3_prefix, placeholder="aegisops/")
                with col_b:
                    aws_region = st.text_input("AWS region", value=config.aws_region, placeholder="us-east-1")
                st.caption("Credentials are resolved via the standard AWS SDK chain (instance role, env vars, ~/.aws).")
            else:
                st.caption("Reads from a REST API exposed by the backend. Reserved for a future backend release.")
                rest_base_url = st.text_input("Base URL", value=config.rest_base_url, placeholder="https://api.aegisops.internal")
                rest_api_key = st.text_input("API key", value=config.rest_api_key, type="password")

            saved = st.form_submit_button("Save Changes", type="primary")

        if saved:
            new_config = AppConfig(
                data_source_type=source_type,
                local_data_dir=local_data_dir,
                s3_bucket=s3_bucket,
                s3_prefix=s3_prefix,
                aws_region=aws_region,
                rest_base_url=rest_base_url,
                rest_api_key=rest_api_key,
                theme=config.theme,
                refresh_interval_seconds=refresh_interval,
            )
            save_config(new_config)
            st.success("Settings saved.")
            st.rerun()

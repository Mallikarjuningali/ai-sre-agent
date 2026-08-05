"""Injects the custom dark theme CSS and disables Streamlit's default chrome."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

_CSS_PATH = Path(__file__).resolve().parent.parent / "styles" / "theme.css"


def inject_theme() -> None:
    css = _CSS_PATH.read_text()
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def configure_page(title: str) -> None:
    st.set_page_config(
        page_title=f"{title} · AegisOps AI",
        page_icon="🛡️",
        layout="wide",
        initial_sidebar_state="expanded",
    )

"""Investigation page — launch an investigation and track its live progress.

Purely a launch surface. Completed reports live on their own dedicated page
now (report_viewer.py, reached via Execution History's "Open Report →" or
this page's own launcher's "View Full Report →" on completion) - this page
no longer renders any report content itself.
"""
from __future__ import annotations

from ..investigation_launcher import render_launcher
from ..topbar import page_header


def render(services, config) -> None:
    page_header("Investigation", "Launch a new AI investigation and track its live progress")

    render_launcher(services, config)

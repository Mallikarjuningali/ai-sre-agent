"""Dashboard Overview page.

Every value rendered here comes from SummaryService / CollectorService /
HistoryService. This module contains no business data of its own — if the
backend hasn't published a field yet, the corresponding panel renders an
empty state instead of a fabricated number.
"""
from __future__ import annotations

import streamlit as st

from ..badges import new_badge, severity_badge, status_badge
from ..cards import card, card_title, empty_state, kpi_card
from ..charts import health_donut, line_trend, stacked_severity_bar
from ..formatting import format_duration, format_number, time_ago
from ..tables import collector_row, investigation_row, render_list_rows, render_table
from ..topbar import last_refreshed_chip, page_header

KPI_META = {
    "total_resources": {"label": "Total Resources", "icon": "🖥️", "kind": "accent", "positive_good": True},
    "critical_issues": {"label": "Critical Issues", "icon": "⛔", "kind": "red", "positive_good": False},
    "high_severity": {"label": "High Severity", "icon": "⚠️", "kind": "orange", "positive_good": False},
    "investigations": {"label": "Investigations", "icon": "🔎", "kind": "blue", "positive_good": True},
    "success_rate": {"label": "Success Rate", "icon": "✅", "kind": "green", "positive_good": True},
}

COLLECTOR_ICONS = {
    "cloudwatch": "☁️",
    "cloudtrail": "🧭",
    "alb": "⚖️",
    "autoscaling": "📈",
}


def render(services, config) -> None:
    summary = services.summary.get_overview()
    collectors = services.collector.get_collector_health()
    executions = services.history.list_executions()

    generated_at = summary.get("generated_at")
    page_header(
        "Overview",
        "Real-time infrastructure overview and AI-powered insights",
        right_html=last_refreshed_chip(time_ago(generated_at) if generated_at else "—"),
    )

    # --- KPI row -----------------------------------------------------
    kpis = summary.get("kpis") or {}
    if kpis:
        cols = st.columns(len(kpis))
        for col, (kpi_key, kpi_data) in zip(cols, kpis.items()):
            meta = KPI_META.get(kpi_key, {"label": kpi_key.replace("_", " ").title(), "icon": "📊", "kind": "accent", "positive_good": True})
            value = kpi_data.get("value")
            suffix = "%" if kpi_key == "success_rate" else ""
            display_value = f"{format_number(value)}{suffix}" if value is not None else "—"
            with col:
                kpi_card(
                    meta["label"],
                    display_value,
                    icon=meta["icon"],
                    icon_kind=meta["kind"],
                    delta=kpi_data.get("delta"),
                    delta_label=kpi_data.get("delta_label", ""),
                    delta_is_positive_good=meta["positive_good"],
                )
    else:
        with card():
            empty_state("No summary metrics yet", "Waiting on the backend to publish summary.json", "📊")

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    # --- Health / Collectors / Recent investigations ------------------
    col1, col2, col3 = st.columns([1.1, 1, 1.2])

    with col1, card():
        card_title("Infrastructure Health")
        health = summary.get("infrastructure_health") or {}
        if health:
            total = health.get("total", 0)
            healthy = health.get("healthy", 0)
            warning = health.get("warning", 0)
            critical = health.get("critical", 0)
            unknown = health.get("unknown", 0)
            fig = health_donut(total, healthy, warning, critical, unknown)
            st.plotly_chart(fig, config={"displayModeBar": False})
            legend_rows = [
                ("Healthy", healthy, "var(--green)"),
                ("Warning", warning, "var(--amber)"),
                ("Critical", critical, "var(--red)"),
                ("Unknown", unknown, "var(--gray)"),
            ]
            legend_html = "".join(
                f'<div style="display:flex; align-items:center; justify-content:space-between; padding:3px 0; font-size:12.5px;">'
                f'<span style="display:flex; align-items:center; gap:7px; color:var(--text-secondary);">'
                f'<span class="ao-dot" style="background:{color}; width:8px; height:8px;"></span>{label}</span>'
                f'<span style="font-weight:600;">{format_number(count)}</span></div>'
                for label, count, color in legend_rows
            )
            st.markdown(legend_html, unsafe_allow_html=True)
        else:
            empty_state("No health data", "Waiting on infrastructure_health from the backend", "🩺")

    with col2, card():
        card_title("Collector Health")
        rows = [
            collector_row(name, status, COLLECTOR_ICONS.get(name.lower(), "⚙️"))
            for name, status in collectors.items()
        ]
        render_list_rows(rows, "No collector data", "Waiting on collectors.json from the backend", "🛰️")

    with col3, card():
        card_title("Recent AI Investigations")
        recent = summary.get("recent_investigations") or []
        rows = []
        for item in recent:
            badge_html = severity_badge(item.get("severity", ""))
            if item.get("is_new"):
                badge_html += new_badge("NEW")
            elif item.get("severity_changed_from"):
                badge_html += new_badge("WORSE")
            rows.append(
                investigation_row(
                    badge_html,
                    item.get("report_id") or item.get("instance_id", "—"),
                    item.get("summary", ""),
                    time_ago(item.get("detected_at")),
                )
            )
        render_list_rows(rows, "No investigations yet", "AI investigations will appear here once triggered", "🧠")

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    # --- Trend charts ---------------------------------------------------
    col4, col5, col6 = st.columns([1.1, 1.2, 1])

    with col4, card():
        card_title("Incidents by Severity", "Last 7 periods")
        trend = summary.get("incidents_by_severity_trend") or []
        if trend:
            dates = [t.get("date", "") for t in trend]
            series = {
                "critical": [t.get("critical", 0) for t in trend],
                "high": [t.get("high", 0) for t in trend],
                "medium": [t.get("medium", 0) for t in trend],
                "low": [t.get("low", 0) for t in trend],
            }
            st.plotly_chart(stacked_severity_bar(dates, series), config={"displayModeBar": False})
        else:
            empty_state("No trend data", "Waiting on incidents_by_severity_trend", "📉")

    with col5, card():
        card_title("Top Affected Resources", "Last 7 periods")
        resources = summary.get("top_affected_resources") or []
        rows = [
            [f'<span class="ao-mono">{r.get("resource_id", "—")}</span>', r.get("type", "—"), str(r.get("incidents", 0))]
            for r in resources
        ]
        render_table(["Resource", "Type", "Incidents"], rows, "No affected resources", "Nothing flagged in this window", "✅")

    with col6, card():
        card_title("Investigation Trend", "Last 7 periods")
        trend = summary.get("investigation_trend") or []
        if trend:
            x = [t.get("date", "") for t in trend]
            y = [t.get("count", 0) for t in trend]
            st.plotly_chart(line_trend(x, y), config={"displayModeBar": False})
        else:
            empty_state("No trend data", "Waiting on investigation_trend", "📈")

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    # --- Latest executions ------------------------------------------
    with card():
        card_title("Latest Executions")
        latest = executions[:6]
        rows = []
        for run in latest:
            rows.append(
                [
                    f'<span class="ao-mono">{run.get("run_id", "—")}</span>',
                    run.get("type", "—"),
                    status_badge(run.get("status", "")) if run.get("status") else "—",
                    str(run.get("resources", "—")),
                    str(run.get("successful", "—")),
                    str(run.get("failed", "—")),
                    format_duration(run.get("execution_time")),
                    run.get("start_time", "—"),
                ]
            )
        render_table(
            ["Execution ID", "Type", "Status", "Resources", "Successful", "Failed", "Duration", "Start Time"],
            rows,
            "No executions yet",
            "Executions will appear here once the backend runs an investigation",
            "🗂️",
        )

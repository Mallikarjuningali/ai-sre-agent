"""Analytics page — trend and distribution views sourced from AnalyticsService."""
from __future__ import annotations

import streamlit as st

from ..cards import card, card_title, empty_state
from ..charts import donut_distribution, line_trend, multi_line_trend
from ..formatting import format_number
from ..tables import render_table
from ..topbar import page_header


def render(services, config) -> None:
    page_header("Analytics", "Trends and statistics across executions, incidents and collectors")

    analytics = services.analytics.get_analytics()

    col1, col2 = st.columns(2)

    with col1, card():
        card_title("Incident Trend", "Investigations opened over time")
        trend = analytics.get("incident_trend") or []
        if trend:
            x = [t.get("date", "") for t in trend]
            y = [t.get("count", 0) for t in trend]
            st.plotly_chart(line_trend(x, y), config={"displayModeBar": False})
        else:
            empty_state("No incident trend data", "Waiting on incident_trend from the backend", "📈")

    with col2, card():
        card_title("Severity Trend", "Incidents by severity over time")
        trend = analytics.get("severity_trend") or []
        if trend:
            x = [t.get("date", "") for t in trend]
            series = {
                "Critical": [t.get("critical", 0) for t in trend],
                "High": [t.get("high", 0) for t in trend],
                "Medium": [t.get("medium", 0) for t in trend],
                "Low": [t.get("low", 0) for t in trend],
            }
            st.plotly_chart(multi_line_trend(x, series), config={"displayModeBar": False})
        else:
            empty_state("No severity trend data", "Waiting on severity_trend from the backend", "📊")

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    col3, col4 = st.columns([1, 1.2])

    with col3, card():
        card_title("Resource Distribution", "By resource type")
        dist = analytics.get("resource_distribution") or []
        if dist:
            labels = [d.get("type", "Unknown") for d in dist]
            values = [d.get("count", 0) for d in dist]
            st.plotly_chart(donut_distribution(labels, values), config={"displayModeBar": False})
        else:
            empty_state("No resource distribution data", "Waiting on resource_distribution from the backend", "🧱")

    with col4, card():
        card_title("Collector Statistics", "Events processed per collector")
        stats = analytics.get("collector_statistics") or []
        rows = [
            [
                s.get("name", "—"),
                format_number(s.get("events_processed")),
                format_number(s.get("errors")),
                f'{format_number(s.get("avg_latency_ms"))} ms' if s.get("avg_latency_ms") is not None else "—",
            ]
            for s in stats
        ]
        render_table(
            ["Collector", "Events Processed", "Errors", "Avg Latency"],
            rows,
            "No collector statistics",
            "Waiting on collector_statistics from the backend",
            "🛰️",
        )

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    with card():
        card_title("Execution Time Trend", "Average execution duration over time")
        trend = analytics.get("execution_time_trend") or []
        if trend:
            x = [t.get("date", "") for t in trend]
            y = [t.get("avg_seconds", 0) for t in trend]
            st.plotly_chart(line_trend(x, y, height=260), config={"displayModeBar": False})
        else:
            empty_state("No execution time data", "Waiting on execution_time_trend from the backend", "⏱️")

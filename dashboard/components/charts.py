"""Plotly chart builders, pre-styled to match the dark enterprise theme."""
from __future__ import annotations

import plotly.graph_objects as go

FONT = dict(family="Inter, -apple-system, Segoe UI, Roboto, sans-serif", color="#9aa4b6", size=12)

COLORS = {
    "critical": "#ef4444",
    "high": "#f97316",
    "medium": "#f59e0b",
    "low": "#3b82f6",
    "healthy": "#22c55e",
    "warning": "#f59e0b",
    "unknown": "#8b93a3",
    "accent": "#7c6cf6",
    "blue": "#3b82f6",
}


def _base_layout(**overrides) -> dict:
    layout = dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=FONT,
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=11)),
        hoverlabel=dict(bgcolor="#161b26", bordercolor="#262c3a", font=dict(color="#eef1f6", size=12)),
    )
    layout.update(overrides)
    return layout


def _grid_axis(**overrides) -> dict:
    axis = dict(gridcolor="rgba(255,255,255,0.06)", zerolinecolor="rgba(255,255,255,0.06)", linecolor="rgba(255,255,255,0.08)")
    axis.update(overrides)
    return axis


def health_donut(total: int, healthy: int, warning: int, critical: int, unknown: int) -> go.Figure:
    labels = ["Healthy", "Warning", "Critical", "Unknown"]
    values = [healthy, warning, critical, unknown]
    colors = [COLORS["healthy"], COLORS["warning"], COLORS["critical"], COLORS["unknown"]]

    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=values,
                hole=0.72,
                marker=dict(colors=colors, line=dict(color="#10141d", width=3)),
                textinfo="none",
                hovertemplate="%{label}: %{value} (%{percent})<extra></extra>",
                sort=False,
            )
        ]
    )
    fig.update_layout(
        **_base_layout(
            showlegend=False,
            height=220,
            annotations=[
                dict(text=f"<b>{total:,}</b>", x=0.5, y=0.56, font=dict(size=26, color="#eef1f6"), showarrow=False),
                dict(text="Total", x=0.5, y=0.4, font=dict(size=12, color="#626d80"), showarrow=False),
            ],
        )
    )
    return fig


def stacked_severity_bar(dates: list[str], series: dict[str, list[int]], height: int = 230) -> go.Figure:
    fig = go.Figure()
    order = ["critical", "high", "medium", "low"]
    names = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}
    for key in order:
        if key not in series:
            continue
        fig.add_trace(
            go.Bar(
                x=dates,
                y=series[key],
                name=names[key],
                marker=dict(color=COLORS[key]),
                hovertemplate=f"{names[key]}: %{{y}}<extra></extra>",
            )
        )
    fig.update_layout(
        **_base_layout(height=height, barmode="stack"),
        xaxis=_grid_axis(showgrid=False),
        yaxis=_grid_axis(),
    )
    return fig


def line_trend(x: list[str], y: list[float], name: str = "", height: int = 230, fill: bool = True) -> go.Figure:
    fig = go.Figure(
        data=[
            go.Scatter(
                x=x,
                y=y,
                mode="lines+markers",
                name=name,
                line=dict(color=COLORS["accent"], width=2.5, shape="spline", smoothing=0.4),
                marker=dict(size=6, color=COLORS["accent"], line=dict(color="#0a0d14", width=1.5)),
                fill="tozeroy" if fill else "none",
                fillcolor="rgba(124,108,246,0.12)",
                hovertemplate="%{x}: %{y}<extra></extra>",
            )
        ]
    )
    fig.update_layout(
        **_base_layout(height=height, showlegend=False),
        xaxis=_grid_axis(showgrid=False),
        yaxis=_grid_axis(),
    )
    return fig


def multi_line_trend(x: list[str], series: dict[str, list[float]], height: int = 260) -> go.Figure:
    palette = [COLORS["accent"], COLORS["blue"], COLORS["healthy"], COLORS["warning"]]
    fig = go.Figure()
    for i, (name, values) in enumerate(series.items()):
        color = palette[i % len(palette)]
        fig.add_trace(
            go.Scatter(
                x=x,
                y=values,
                mode="lines+markers",
                name=name,
                line=dict(color=color, width=2.25),
                marker=dict(size=5, color=color),
                hovertemplate=f"{name}: %{{y}}<extra></extra>",
            )
        )
    fig.update_layout(
        **_base_layout(height=height),
        xaxis=_grid_axis(showgrid=False),
        yaxis=_grid_axis(),
    )
    return fig


def horizontal_bar(labels: list[str], values: list[float], height: int = 260) -> go.Figure:
    fig = go.Figure(
        data=[
            go.Bar(
                x=values,
                y=labels,
                orientation="h",
                marker=dict(color=COLORS["accent"]),
                hovertemplate="%{y}: %{x}<extra></extra>",
            )
        ]
    )
    fig.update_layout(
        **_base_layout(height=height, showlegend=False),
        xaxis=_grid_axis(),
        yaxis=_grid_axis(showgrid=False, autorange="reversed"),
        margin=dict(l=10, r=10, t=10, b=10),
    )
    return fig


def donut_distribution(labels: list[str], values: list[float], height: int = 260) -> go.Figure:
    palette = ["#7c6cf6", "#3b82f6", "#22c55e", "#f59e0b", "#f97316", "#ef4444", "#8b93a3"]
    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=values,
                hole=0.6,
                marker=dict(colors=palette, line=dict(color="#10141d", width=2)),
                textinfo="percent",
                textfont=dict(color="#eef1f6", size=11),
                hovertemplate="%{label}: %{value} (%{percent})<extra></extra>",
            )
        ]
    )
    fig.update_layout(**_base_layout(height=height, legend=dict(orientation="v", yanchor="middle", y=0.5, xanchor="left", x=1.05)))
    return fig

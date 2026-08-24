"""
Cost Explorer page — AWS billing/cost visibility, completely separate
from the infra Investigation pipeline. Every value shown here comes from
a real AWS Cost Explorer query (collector/cost_explorer.py via
services.cost_explorer / services.cost_refresh); nothing is fabricated,
and nothing here reads or displays infra investigation report data.
"""
from __future__ import annotations

from datetime import datetime

import streamlit as st

from services import CostExplorerActionError, invalidate

from ..badges import severity_badge
from ..cards import card, card_title, empty_state, kpi_card
from ..charts import horizontal_bar, line_trend
from ..formatting import time_ago
from ..tables import render_table
from ..topbar import page_header

_REFRESH_ERROR_KEY = "cost_explorer_refresh_error"


def render(services, config) -> None:
    page_header("Cost Explorer", "AWS billing and cost analysis for the current account")

    summary = services.cost_explorer.get_summary()

    col_meta, col_refresh = st.columns([4, 1])
    with col_meta:
        generated_at = summary.get("generated_at")
        if generated_at:
            st.caption(f"Last refreshed {time_ago(generated_at)}")
        else:
            st.caption("No cost data yet — click Refresh to run a Cost Explorer query.")
    with col_refresh:
        if st.button("Refresh", key="cost_explorer_refresh", width="stretch", icon=":material/refresh:"):
            _handle_refresh(services)

    _render_refresh_error()

    st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)

    _render_cost_overview(summary)

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    col_trend, col_anomaly = st.columns([2, 1])

    with col_trend, card():
        history = services.cost_explorer.get_history()
        daily = history.get("daily_history") or []
        card_title("Cost Trend", f"Daily cost — {len(daily)} day(s) of history")
        _render_cost_trend(daily)

    with col_anomaly, card():
        card_title("Cost Anomalies")
        _render_anomalies(services.cost_explorer.get_anomalies())

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    col_services, col_regions = st.columns(2)

    with col_services, card():
        card_title("Service Cost Breakdown")
        services_data = services.cost_explorer.get_services()
        _render_breakdown(services_data.get("service_breakdown") or [], "service", services_data.get("currency"))

    with col_regions, card():
        card_title("Region Cost Breakdown")
        regions_data = services.cost_explorer.get_regions()
        _render_breakdown(regions_data.get("region_breakdown") or [], "region", regions_data.get("currency"))

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    with card():
        card_title("Credits", "AWS Cost Explorer, RECORD_TYPE=Credit")
        _render_credits(summary, services.cost_explorer.get_credits())

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    _render_ai_cost_analysis(services.cost_explorer.get_report())


# ---------------------------------------------------------------------------
# Cost Overview - 4 KPI cards
# ---------------------------------------------------------------------------

def _format_money(value, currency) -> str:
    if value is None:
        return "—"
    if currency == "USD":
        return f"${value:,.2f}"
    return f"{value:,.2f} {currency}" if currency else f"{value:,.2f}"


def _render_cost_overview(summary: dict) -> None:
    total_cost = summary.get("total_cost")
    previous_cost = summary.get("previous_cost")
    change_percent = summary.get("change_percent")
    currency = summary.get("currency")
    credits_total = summary.get("credits_total")

    cols = st.columns(5)

    with cols[0]:
        delta_kwargs = {}
        if change_percent is not None:
            delta_kwargs = dict(delta=change_percent, delta_label="vs previous period", delta_is_positive_good=False)
        kpi_card("Current Cost", _format_money(total_cost, currency), icon="💳", icon_kind="accent", **delta_kwargs)

    with cols[1]:
        kpi_card("Previous Period Cost", _format_money(previous_cost, currency), icon="🗓️", icon_kind="blue")

    with cols[2]:
        change_label = f"{change_percent:+.1f}%" if change_percent is not None else "—"
        change_kind = "red" if (change_percent or 0) > 0 else "green" if change_percent is not None else "accent"
        kpi_card("Change", change_label, icon="📈", icon_kind=change_kind)

    with cols[3]:
        # credits_total is AWS's raw (negative-or-zero) sign; the KPI
        # card shows the "applied" magnitude, per the requirement that
        # only the dashboard - never the backend - flips it to a
        # presentation-friendly positive figure.
        credits_display = abs(credits_total) if credits_total is not None else None
        kpi_card("Credits Applied", _format_money(credits_display, currency), icon="🏷️", icon_kind="green")

    with cols[4]:
        kpi_card("Currency", currency or "—", icon="🌐", icon_kind="accent")


# ---------------------------------------------------------------------------
# Cost trend
# ---------------------------------------------------------------------------

def _format_chart_date(iso_date: str) -> str:
    """"YYYY-MM-DD" (the backend's unambiguous, sortable date) -> "Aug 18"
    for display only. Never sent to the chart library as the raw
    ambiguous "DD-MM"-style string that caused it to misparse dates onto
    a bogus year axis - this formatting happens here, at render time,
    not in the stored/transmitted data."""

    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%b %d")
    except (TypeError, ValueError):
        return iso_date or "—"


def _render_cost_trend(daily: list) -> None:
    if not daily:
        empty_state("No cost history yet", "Click Refresh to fetch AWS Cost Explorer data", "📈")
        return

    x = [_format_chart_date(point[0]) for point in daily]
    y = [point[1] for point in daily]
    st.plotly_chart(line_trend(x, y, height=280), config={"displayModeBar": False})


# ---------------------------------------------------------------------------
# Anomalies - three honest states from the collector, never a fabricated one
# ---------------------------------------------------------------------------

def _render_anomalies(anomalies: dict) -> None:
    status = anomalies.get("status")
    findings = anomalies.get("anomalies") or []

    if status == "found" and findings:
        count = len(findings)
        label = f"{count} Cost {'Anomaly' if count == 1 else 'Anomalies'} Detected"
        st.markdown(
            f'<div style="display:flex; align-items:center; gap:8px; margin-bottom:0.75rem;">'
            f'<span style="font-size:18px;">🟠</span>'
            f'<span style="font-weight:600; color:var(--text-primary);">{label}</span></div>',
            unsafe_allow_html=True,
        )
        for finding in findings:
            impact = finding.get("total_impact")
            impact_text = f"{float(impact):,.2f}" if impact not in (None, "") else "—"
            score = finding.get("anomaly_score")
            score_text = f"{float(score):.0f}" if score not in (None, "") else "—"
            st.markdown(
                f"""
                <div style="border-bottom:1px solid var(--border-subtle); padding:0.55rem 0;">
                  <div style="font-size:13.5px; font-weight:600; color:var(--text-primary);">
                    {finding.get("service") or "Unknown service"}
                  </div>
                  <div style="font-size:12.5px; color:var(--text-secondary); margin-top:2px;">
                    Date: {finding.get("start_date") or "—"} &middot; Impact: {impact_text} &middot; Anomaly score: {score_text}
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        return

    # "reason" is a clean, short, human-written string for every status
    # except "unavailable"/"unsupported_range", where the collector's
    # reason IS the raw AWS/boto3 exception text - too technical (and
    # potentially long) for the main card body, so those two get a short
    # fixed summary here and the real text tucked into a details
    # expander instead of being dumped into the main UI.
    show_details = False

    if status == "not_configured":
        icon, label = "🟡", "Anomaly Detection Not Configured"
        detail = anomalies.get("reason") or "No AWS Cost Anomaly monitors configured"
    elif status == "unsupported_range":
        icon, label = "🟠", "No Cost Anomaly Data Available"
        detail = "AWS Cost Anomaly Detection doesn't support this date range yet."
        show_details = True
    elif status == "unavailable":
        icon, label = "⚪", "Cost Anomaly Data Unavailable"
        detail = "Could not retrieve anomaly data due to an unexpected error."
        show_details = True
    else:  # "none_found", or nothing published yet
        icon, label = "🟢", "No Cost Anomalies Detected"
        detail = anomalies.get("reason") or "AWS did not report any cost anomalies for this period"

    st.markdown(
        f"""
        <div style="display:flex; align-items:center; gap:8px; margin-bottom:0.4rem;">
          <span style="font-size:18px;">{icon}</span>
          <span style="font-weight:600; color:var(--text-primary);">{label}</span>
        </div>
        <div style="font-size:13px; color:var(--text-secondary); line-height:1.5;">{detail}</div>
        """,
        unsafe_allow_html=True,
    )

    raw_reason = anomalies.get("reason")
    if show_details and raw_reason:
        with st.expander("Technical details"):
            st.code(raw_reason, language=None)


# ---------------------------------------------------------------------------
# Service / region breakdown
# ---------------------------------------------------------------------------

def _render_breakdown(items: list, label_key: str, currency) -> None:
    if not items:
        empty_state("No breakdown data yet", "Click Refresh to fetch AWS Cost Explorer data", "🧾")
        return

    top = items[:10]

    labels = [item.get(label_key) or "Unknown" for item in reversed(top)]
    values = [item.get("cost") or 0 for item in reversed(top)]
    st.plotly_chart(horizontal_bar(labels, values, height=max(220, 32 * len(top))), config={"displayModeBar": False})

    rows = [
        [item.get(label_key) or "Unknown", _format_money(item.get("cost"), item.get("currency") or currency)]
        for item in top
    ]
    render_table([label_key.title(), "Cost"], rows, "No breakdown data", "", "🧾")


# ---------------------------------------------------------------------------
# Credits - Gross -> Credits -> Net breakdown, and credit history table
# ---------------------------------------------------------------------------

def _render_credits(summary: dict, credits: dict) -> None:
    currency = summary.get("currency") or credits.get("currency")
    gross_cost = summary.get("gross_cost")
    net_cost = summary.get("total_cost")
    credits_total = credits.get("total")
    history = credits.get("history") or []

    # Only shown when AWS actually returned both figures needed for the
    # arithmetic - never inferred or shown just because total cost is 0.
    if gross_cost is not None and credits_total is not None and net_cost is not None:
        st.markdown(
            f"""
            <div style="display:flex; gap:1.5rem; flex-wrap:wrap; margin-bottom:0.9rem; font-size:13.5px;">
              <div><span style="color:var(--text-secondary);">Gross Cost:</span>
                <span style="color:var(--text-primary); font-weight:600;">{_format_money(gross_cost, currency)}</span></div>
              <div><span style="color:var(--text-secondary);">Credits Applied:</span>
                <span style="color:var(--green); font-weight:600;">{_format_money(abs(credits_total), currency)}</span></div>
              <div><span style="color:var(--text-secondary);">Net Cost:</span>
                <span style="color:var(--text-primary); font-weight:600;">{_format_money(net_cost, currency)}</span></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    total_label = _format_money(abs(credits_total), currency) if credits_total is not None else "—"
    st.markdown(
        f'<div style="font-size:13.5px; color:var(--text-secondary); margin-bottom:0.6rem;">'
        f'Total Credits Applied: <span style="color:var(--text-primary); font-weight:600;">{total_label}</span></div>',
        unsafe_allow_html=True,
    )

    if not history:
        empty_state("No credit records for this period", "AWS returned no RECORD_TYPE=Credit entries", "🏷️")
        return

    rows = [[_format_chart_date(point[0]), _format_money(abs(point[1]), currency)] for point in history]
    render_table(["Date", "Credit"], rows, "No credit history", "", "🏷️")


# ---------------------------------------------------------------------------
# AI Cost Analysis - separate from the infra report UI
# ---------------------------------------------------------------------------

def _render_ai_cost_analysis(report: dict) -> None:
    if not report:
        with card():
            card_title("AI Cost Analysis")
            empty_state("No AI cost analysis yet", "Click Refresh to run a Cost Explorer query and Gemini analysis", "🤖")
        return

    with card():
        card_title("AI Cost Analysis")
        badge_html = severity_badge(report.get("severity", "")) if report.get("severity") else ""
        st.markdown(
            f"""
            <div style="margin-bottom:0.6rem;">{badge_html}</div>
            <div style="font-size:14px; color:var(--text-primary); line-height:1.5;">
              {report.get("summary") or "No summary available."}
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)

    col_root, col_evidence = st.columns(2)

    with col_root, card():
        card_title("Root Cause")
        root_cause = report.get("root_cause")
        if root_cause:
            st.markdown(
                f"""
                <div style="background:var(--red-soft); border:1px solid rgba(239,68,68,0.3); border-radius:var(--radius-sm);
                            padding:0.9rem 1.1rem; font-size:14px; color:var(--text-primary); line-height:1.5;">
                  {root_cause}
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            empty_state("Root cause not yet determined", "", "🧩")

    with col_evidence, card():
        card_title("Evidence")
        evidence = report.get("evidence") or []
        if evidence:
            items_html = "".join(
                f'<div style="display:flex; gap:9px; padding:0.5rem 0; border-bottom:1px solid var(--border-subtle);">'
                f'<span style="color:var(--text-muted); font-size:12px; margin-top:2px;">▸</span>'
                f'<span style="font-size:13.5px; color:var(--text-primary); line-height:1.45;">{e}</span></div>'
                for e in evidence
            )
            st.markdown(items_html, unsafe_allow_html=True)
        else:
            empty_state("No evidence collected", "", "📄")

    st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)

    with card():
        card_title("Recommendations")
        recommendations = report.get("recommendations") or []
        if recommendations:
            items_html = "".join(
                f'<div style="display:flex; gap:9px; padding:0.5rem 0; border-bottom:1px solid var(--border-subtle);">'
                f'<span style="color:var(--green); font-size:13px; margin-top:2px;">✓</span>'
                f'<span style="font-size:13.5px; color:var(--text-primary); line-height:1.45;">{r}</span></div>'
                for r in recommendations
            )
            st.markdown(items_html, unsafe_allow_html=True)
        else:
            empty_state("No recommendations yet", "", "💡")


# ---------------------------------------------------------------------------
# Refresh action
# ---------------------------------------------------------------------------

def _handle_refresh(services) -> None:
    try:
        with st.spinner("Querying AWS Cost Explorer and running Gemini analysis…"):
            services.cost_refresh.refresh()
    except CostExplorerActionError as exc:
        st.session_state[_REFRESH_ERROR_KEY] = str(exc)
        st.rerun()
        return

    # A fresh feed was just published on the backend - drop every cached
    # read so this rerun pulls the new summary/history/services/etc.
    invalidate()
    st.success("Cost Explorer data refreshed.")
    st.rerun()


def _render_refresh_error() -> None:
    error = st.session_state.pop(_REFRESH_ERROR_KEY, None)
    if not error:
        return
    st.error(error)

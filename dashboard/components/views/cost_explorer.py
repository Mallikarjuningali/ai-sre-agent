"""
Cost Explorer page — AWS billing/cost visibility, completely separate
from the infra Investigation pipeline. Every value shown here comes from
a real AWS Cost Explorer query (collector/cost_explorer.py via
services.cost_explorer / services.cost_refresh); nothing is fabricated,
and nothing here reads or displays infra investigation report data.

Organized as a professional billing dashboard, per section:

    Cost Summary      - Gross / Credits / Net / Change, explicitly related
    Cost Trend        - daily time series, actual dates
    Cost by Service    - gross/credits/net/change per service, never
                         hiding a service whose net cost is 0 because
                         credits fully offset real gross usage
    Cost by Region     - same, with an honest note when AWS's own data
                         doesn't attribute credits to a region
    Credits            - total/history/service-level attribution, with an
                         explicit statement that resource-level
                         attribution is not available from AWS
    Cost Anomalies     - every AWS-provided field per anomaly, not just a
                         generic status
    Period Comparison  - the user-selected Month/Period Comparison
    AI Cost Analysis   - Gemini's take, using both periods' real data

See context/cost_context_builder.py's docstring for the exact data model
(current_period/previous_period/change/service_comparison/
region_comparison/anomalies/comparison) every section below reads from.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import streamlit as st

from services import CostExplorerActionError, invalidate

from ..badges import severity_badge
from ..cards import card, card_title, empty_state, kpi_card
from ..charts import horizontal_bar, line_trend, multi_line_trend
from ..formatting import time_ago
from ..tables import render_table
from ..topbar import page_header

_REFRESH_ERROR_KEY = "cost_explorer_refresh_error"
_DATE_RANGE_ERROR_KEY = "cost_explorer_date_range_error"
_FROM_DATE_KEY = "cost_explorer_from_date"
_TO_DATE_KEY = "cost_explorer_to_date"
_TREND_VIEW_KEY = "cost_explorer_trend_view"

# UI-only initial widget default (days), mirroring the backend's own
# default lookback window purely as a starting suggestion - the user can
# freely pick any other range via the date pickers, and whatever they
# pick is what's actually sent; this number is never part of the
# comparison-period calculation itself.
_DEFAULT_COMPARISON_DAYS = 14


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_money(value, currency) -> str:
    if value is None:
        return "—"
    if currency == "USD":
        return f"${value:,.2f}"
    return f"{value:,.2f} {currency}" if currency else f"{value:,.2f}"


def _format_signed_money(value, currency) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ("−" if value < 0 else "")
    return f"{sign}{_format_money(abs(value), currency)}"


def _format_chart_date(iso_date: str) -> str:
    """"YYYY-MM-DD" (the backend's unambiguous, sortable date) -> "Aug 18"
    for display only. Never sent to a chart library as the raw ambiguous
    "DD-MM"-style string that would misparse dates onto a bogus year
    axis - this formatting happens here, at render time, not in the
    stored/transmitted data."""
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%b %d")
    except (TypeError, ValueError):
        return iso_date or "—"


def _format_percent(value) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def _format_period_range(period: dict) -> str:
    return f"{_format_chart_date(period.get('from'))} – {_format_chart_date(period.get('to'))}"


def _change_lookup(comparison_list: list, label_key: str) -> dict:
    """{name: {difference, percentage_change}} from a service_comparison/
    region_comparison list (period_a=current, period_b=previous at the
    top level - see context/cost_context_builder.py's build_context)."""
    return {
        item.get(label_key): {
            "difference": item.get("difference"),
            "percentage_change": item.get("percentage_change"),
        }
        for item in (comparison_list or []) if item.get(label_key)
    }


# ---------------------------------------------------------------------------
# Header, date range picker, refresh
# ---------------------------------------------------------------------------

def _default_from_date() -> date:
    return date.today() - timedelta(days=_DEFAULT_COMPARISON_DAYS - 1)


def _default_to_date() -> date:
    return date.today()


def _render_header_and_controls(services, summary: dict) -> None:
    with card():
        col_meta, col_from, col_to, col_refresh = st.columns([2, 1.3, 1.3, 1])

        with col_meta:
            st.markdown(
                '<div style="font-size:13px; color:var(--text-secondary); margin-bottom:0.15rem;">Account</div>'
                '<div style="font-size:14.5px; font-weight:600; color:var(--text-primary);">Current AWS Account</div>',
                unsafe_allow_html=True,
            )
            generated_at = summary.get("generated_at")
            if generated_at:
                st.caption(f"Last refreshed {time_ago(generated_at)}")
            else:
                st.caption("No cost data yet — click Refresh to run a Cost Explorer query.")

        with col_from:
            st.date_input("From Date", value=_default_from_date(), key=_FROM_DATE_KEY)
        with col_to:
            st.date_input("To Date", value=_default_to_date(), key=_TO_DATE_KEY)
        with col_refresh:
            st.markdown("<div style='height:1.6rem'></div>", unsafe_allow_html=True)
            if st.button("Refresh", key="cost_explorer_refresh", width="stretch", icon=":material/refresh:"):
                _handle_refresh(services)

    _render_refresh_error()
    _render_date_range_error()


def _handle_refresh(services) -> None:
    from_date = st.session_state.get(_FROM_DATE_KEY)
    to_date = st.session_state.get(_TO_DATE_KEY)

    if from_date and to_date and from_date > to_date:
        st.session_state[_DATE_RANGE_ERROR_KEY] = "From Date must be on or before To Date."
        st.rerun()
        return

    from_date_str = from_date.isoformat() if from_date else None
    to_date_str = to_date.isoformat() if to_date else None

    try:
        with st.spinner("Querying AWS Cost Explorer and running Gemini analysis…"):
            services.cost_refresh.refresh(from_date=from_date_str, to_date=to_date_str)
    except CostExplorerActionError as exc:
        st.session_state[_REFRESH_ERROR_KEY] = str(exc)
        st.rerun()
        return

    # A fresh feed was just published on the backend for every section
    # (summary/history/services/regions/credits/anomalies/comparison/
    # report) - drop every cached read so this rerun pulls all of it,
    # never a stale section left over from the previous date selection.
    invalidate()
    st.success("Cost Explorer data refreshed.")
    st.rerun()


def _render_refresh_error() -> None:
    error = st.session_state.pop(_REFRESH_ERROR_KEY, None)
    if not error:
        return
    st.error(error)


def _render_date_range_error() -> None:
    error = st.session_state.pop(_DATE_RANGE_ERROR_KEY, None)
    if not error:
        return
    st.error(error)


# ---------------------------------------------------------------------------
# Cost Summary - Gross / Credits / Net / Change, explicitly related
# ---------------------------------------------------------------------------

def _render_cost_summary(summary: dict) -> None:
    current = summary.get("current_period") or {}
    change = summary.get("change") or {}
    currency = summary.get("currency") or current.get("currency")

    gross_cost = current.get("gross_cost")
    credits_total = (current.get("credits") or {}).get("total")
    net_cost = current.get("net_cost")

    if gross_cost is None and net_cost is None:
        empty_state(
            "No cost data yet",
            "Click Refresh above to run a Cost Explorer query for this account.",
            "💳",
        )
        return

    with card():
        card_title("Cost Summary", "Gross usage, AWS credits applied, and what was actually billed")

        cols = st.columns(4)

        with cols[0]:
            kpi_card("Gross Cost", _format_money(gross_cost, currency), icon="🧾", icon_kind="blue")
        with cols[1]:
            credits_display = abs(credits_total) if credits_total is not None else None
            kpi_card("Credits", f"−{_format_money(credits_display, currency)}" if credits_display else "—",
                      icon="🏷️", icon_kind="green")
        with cols[2]:
            kpi_card("Net Cost", _format_money(net_cost, currency), icon="💳", icon_kind="accent")
        with cols[3]:
            change_percent = change.get("net_cost_change_percent")
            delta_kwargs = {}
            if change_percent is not None:
                delta_kwargs = dict(delta=change_percent, delta_label="vs previous period", delta_is_positive_good=False)
            kpi_card("Change", _format_percent(change_percent), icon="📈", icon_kind="accent", **delta_kwargs)

        st.markdown(
            f"""
            <div style="margin-top:0.9rem; font-size:12.5px; color:var(--text-secondary); line-height:1.6;">
              <b>Gross Cost</b> = usage before credits &nbsp;·&nbsp;
              <b>Credits</b> = AWS credits applied &nbsp;·&nbsp;
              <b>Net Cost</b> = amount after credits
            </div>
            """,
            unsafe_allow_html=True,
        )

        if (
            net_cost is not None and gross_cost is not None
            and round(net_cost, 2) == 0 and round(gross_cost, 2) > 0
        ):
            st.markdown(
                f"""
                <div style="margin-top:0.7rem; padding:0.7rem 1rem; border-radius:var(--radius-sm);
                            background:var(--green-soft); border:1px solid rgba(34,197,94,0.3);
                            font-size:13px; color:var(--text-primary);">
                  ✅ Credits fully offset the reported costs for this period
                  ({_format_money(gross_cost, currency)} in usage, fully covered by credits).
                </div>
                """,
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# Cost Trend
# ---------------------------------------------------------------------------

def _render_cost_trend(history: dict, comparison: dict) -> None:
    daily = history.get("daily_history") or []
    has_comparison = bool(comparison and comparison.get("selected_period"))

    with card():
        card_title("Cost Trend", f"Daily cost — {len(daily)} day(s) of history")

        view_options = ["Current Period"]
        if has_comparison:
            view_options.append("Selected vs Comparison Period")

        if len(view_options) > 1:
            view = st.radio("View", view_options, key=_TREND_VIEW_KEY, horizontal=True, label_visibility="collapsed")
        else:
            view = view_options[0]

        if view == "Selected vs Comparison Period":
            _render_comparison_trend(comparison)
            return

        if not daily:
            empty_state(
                "No daily cost data was returned by AWS for this period",
                "Click Refresh above to fetch AWS Cost Explorer data",
                "📈",
            )
            return

        x = [_format_chart_date(point[0]) for point in daily]
        y = [point[1] for point in daily]
        st.plotly_chart(line_trend(x, y, height=280), config={"displayModeBar": False})


def _render_comparison_trend(comparison: dict) -> None:
    selected = comparison.get("selected_period") or {}
    comparison_period = comparison.get("comparison_period") or {}

    selected_daily = selected.get("daily_history") or []
    comparison_daily = comparison_period.get("daily_history") or []

    if not selected_daily and not comparison_daily:
        empty_state("No daily cost data was returned by AWS for either period", "", "📈")
        return

    st.caption(
        f"Selected: {_format_period_range(selected)}  ·  Comparison: {_format_period_range(comparison_period)}"
    )

    # The two periods have different real calendar dates but (per
    # _comparison_period()) the same duration - a shared x-axis needs a
    # relative "day of period" index rather than asserting one period's
    # actual dates apply to the other's values. The real date ranges for
    # each line are shown above/in the legend, not hidden.
    length = max(len(selected_daily), len(comparison_daily))
    x = [f"Day {i + 1}" for i in range(length)]

    series = {}
    if selected_daily:
        series[f"Selected ({_format_period_range(selected)})"] = [p[1] for p in selected_daily] + [None] * (length - len(selected_daily))
    if comparison_daily:
        series[f"Comparison ({_format_period_range(comparison_period)})"] = [p[1] for p in comparison_daily] + [None] * (length - len(comparison_daily))

    st.plotly_chart(multi_line_trend(x, series, height=280), config={"displayModeBar": False})


# ---------------------------------------------------------------------------
# Cost by Service / Cost by Region
# ---------------------------------------------------------------------------

def _render_breakdown_table(breakdown: list, comparison_list: list, label_key: str, currency, empty_message: str) -> None:
    if not breakdown:
        empty_state(empty_message, "", "🧾")
        return

    changes = _change_lookup(comparison_list, label_key)

    top = breakdown[:12]

    chart_labels = [item.get(label_key) or "Unknown" for item in reversed(top)]
    chart_values = [item.get("gross_cost") or 0 for item in reversed(top)]
    st.plotly_chart(horizontal_bar(chart_labels, chart_values, height=max(220, 30 * len(top))), config={"displayModeBar": False})

    rows = []
    for item in top:
        name = item.get(label_key) or "Unknown"
        entry_currency = item.get("currency") or currency
        change = changes.get(name) or {}
        pct = change.get("percentage_change")
        credit_value = item.get("credits")
        rows.append([
            name,
            _format_money(item.get("gross_cost"), entry_currency),
            _format_signed_money(credit_value, entry_currency) if credit_value else "—",
            _format_money(item.get("net_cost"), entry_currency),
            _format_percent(pct) if pct is not None else (_format_signed_money(change.get("difference"), entry_currency) if change.get("difference") is not None else "—"),
        ])

    render_table([label_key.title(), "Gross Cost", "Credits", "Net Cost", "Change"], rows, empty_message, "", "🧾")


def _render_cost_by_service(services_data: dict) -> None:
    with card():
        card_title("Cost by Service")
        _render_breakdown_table(
            services_data.get("service_breakdown") or [],
            services_data.get("service_comparison") or [],
            "service",
            services_data.get("currency"),
            "No service-level cost data is available.",
        )


def _render_cost_by_region(regions_data: dict) -> None:
    breakdown = regions_data.get("region_breakdown") or []
    total_region_credits = sum(abs(item.get("credits") or 0) for item in breakdown)

    with card():
        card_title("Cost by Region")
        _render_breakdown_table(
            breakdown,
            regions_data.get("region_comparison") or [],
            "region",
            regions_data.get("currency"),
            "No regional cost data was returned by AWS for this period.",
        )

        if breakdown and total_region_credits == 0:
            st.caption(
                "Credits are reported at account level by AWS and are not allocated to individual regions."
            )


# ---------------------------------------------------------------------------
# Credits
# ---------------------------------------------------------------------------

def _render_credits_section(credits: dict) -> None:
    currency = credits.get("currency")
    total = credits.get("total")
    history = credits.get("history") or []
    by_service = credits.get("by_service") or []

    with card():
        card_title("Credits", "AWS Cost Explorer, RECORD_TYPE=Credit")

        total_label = _format_money(abs(total), currency) if total is not None else "—"
        st.markdown(
            f'<div style="font-size:13.5px; color:var(--text-secondary); margin-bottom:0.6rem;">'
            f'Total Credits: <span style="color:var(--text-primary); font-weight:600;">{total_label}</span></div>',
            unsafe_allow_html=True,
        )

        if not history and total is None:
            empty_state("No credit data is available.", "Click Refresh above to fetch AWS Cost Explorer data", "🏷️")
            return

        if not history:
            empty_state("No credit records for this period", "AWS returned no RECORD_TYPE=Credit entries", "🏷️")
        else:
            st.markdown(
                '<div style="font-size:13px; font-weight:600; color:var(--text-primary); margin:0.6rem 0 0.4rem;">'
                'Credit History</div>',
                unsafe_allow_html=True,
            )
            rows = [[_format_chart_date(point[0]), _format_money(abs(point[1]), currency)] for point in history]
            render_table(["Date", "Credit"], rows, "No credit history", "", "🏷️")

        st.markdown(
            '<div style="font-size:13px; font-weight:600; color:var(--text-primary); margin:1rem 0 0.4rem;">'
            'Credit Source (RECORD_TYPE = Credit)</div>',
            unsafe_allow_html=True,
        )
        st.caption("Every credit shown here is an AWS Cost Explorer record with RECORD_TYPE=Credit - not an estimate.")

        st.markdown(
            '<div style="font-size:13px; font-weight:600; color:var(--text-primary); margin:1rem 0 0.4rem;">'
            'Service-Level Attribution</div>',
            unsafe_allow_html=True,
        )
        if by_service:
            rows = [
                [
                    item.get("service") or "Unknown",
                    _format_money(item.get("gross_cost"), item.get("currency") or currency),
                    _format_money(abs(item.get("credits") or 0), item.get("currency") or currency),
                    _format_money(item.get("net_cost"), item.get("currency") or currency),
                ]
                for item in by_service
            ]
            render_table(["Service", "Gross", "Credits", "Net"], rows, "No service-level credit data", "", "🏷️")
        else:
            empty_state(
                "No service-level credit attribution available for this period",
                "AWS did not attribute any credit to a specific service in this window",
                "🏷️",
            )

        st.markdown(
            f"""
            <div style="margin-top:0.9rem; padding:0.7rem 1rem; border-radius:var(--radius-sm);
                        background:var(--bg-elevated); border:1px solid var(--border-subtle);
                        font-size:12.5px; color:var(--text-secondary); line-height:1.55;">
              ℹ️ {credits.get("resource_level_attribution_note") or
                  "AWS Cost Explorer reports these credits at the service/region level. "
                  "Individual resource attribution is not available from the current billing data."}
            </div>
            """,
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Cost Anomalies - every AWS field per anomaly, no fabricated severity
# ---------------------------------------------------------------------------

def _render_anomaly_card(anomaly: dict, currency) -> None:
    score = anomaly.get("anomaly_score")
    max_score = anomaly.get("max_anomaly_score")
    score_text = f"{float(score):.0f}" + (f" / {float(max_score):.0f}" if max_score not in (None, "") else "") if score not in (None, "") else "—"

    fields = [
        ("Service", anomaly.get("service") or "—"),
        ("Region", anomaly.get("region") or "—"),
        ("Start", anomaly.get("start_date") or "—"),
        ("End", anomaly.get("end_date") or "—"),
        ("Anomaly Score", score_text),
        ("Impact", _format_money(anomaly.get("total_impact"), currency)),
        ("Actual Spend", _format_money(anomaly.get("total_actual_spend"), currency)),
        ("Expected Spend", _format_money(anomaly.get("total_expected_spend"), currency)),
    ]
    # Only fields AWS actually returned are shown - "—" above already
    # covers a missing value, but a field entirely absent from the
    # anomaly's own dict (older data / different API version) is
    # dropped rather than shown as a fabricated "—" row.
    rows_html = "".join(
        f'<div style="display:flex; justify-content:space-between; padding:0.3rem 0; '
        f'border-bottom:1px solid var(--border-subtle); font-size:12.5px;">'
        f'<span style="color:var(--text-secondary);">{label}</span>'
        f'<span style="color:var(--text-primary); font-weight:600;">{value}</span></div>'
        for label, value in fields
    )

    monitor_name = anomaly.get("monitor_name")
    monitor_line = f'<div style="font-size:11.5px; color:var(--text-muted); margin-top:0.5rem;">Monitor: {monitor_name}</div>' if monitor_name else ""

    st.markdown(
        f"""
        <div style="border:1px solid var(--border-subtle); border-radius:var(--radius-sm); padding:0.8rem 1rem; margin-bottom:0.7rem;">
          <div style="font-size:13.5px; font-weight:700; color:var(--text-primary); margin-bottom:0.4rem;">
            {anomaly.get("service") or "Unknown service"}
          </div>
          {rows_html}
          {monitor_line}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_anomalies_section(anomalies: dict) -> None:
    status = anomalies.get("status")
    findings = anomalies.get("anomalies") or []
    currency = anomalies.get("currency")

    with card():
        card_title("Cost Anomalies")

        if status == "found" and findings:
            count = len(findings)
            st.markdown(
                f'<div style="display:flex; align-items:center; gap:8px; margin-bottom:0.9rem;">'
                f'<span style="font-size:18px;">🟠</span>'
                f'<span style="font-weight:600; color:var(--text-primary);">'
                f'{count} Cost {"Anomaly" if count == 1 else "Anomalies"} Detected</span></div>',
                unsafe_allow_html=True,
            )
            for finding in findings:
                _render_anomaly_card(finding, currency)
            return

        show_details = False

        if status == "not_configured":
            icon, label = "🟡", "Anomaly Detection Not Configured"
            detail = anomalies.get("reason") or "No AWS Cost Anomaly monitors are configured for this account."
        elif status == "unsupported_range":
            icon, label = "🟠", "Anomaly Data Unavailable For This Range"
            detail = "AWS Cost Anomaly Detection does not support this date range."
            show_details = True
        elif status == "unavailable":
            icon, label = "⚪", "Cost Anomaly Data Unavailable"
            detail = "Could not retrieve anomaly data due to an unexpected error."
            show_details = True
        else:  # "none_found", or nothing published yet
            icon, label = "🟢", "No Cost Anomalies Detected"
            detail = anomalies.get("reason") or "Amazon Cost Explorer did not report an anomaly for the selected period."

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
# Period Comparison - user-selected Month/Period Comparison, numbers only
# (see _render_ai_cost_analysis for Gemini's interpretation of the same data)
# ---------------------------------------------------------------------------

def _render_period_comparison(comparison: dict) -> None:
    with card():
        card_title("Period Comparison")

        if not comparison or not comparison.get("selected_period"):
            empty_state(
                "No comparison yet",
                "Pick a From/To date range above and click Refresh",
                "🔀",
            )
            return

        selected = comparison.get("selected_period") or {}
        comparison_period = comparison.get("comparison_period") or {}
        currency = selected.get("currency") or comparison_period.get("currency")
        credit_comparison = comparison.get("credit_comparison") or {}

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(
                f'<div style="font-size:12px; color:var(--text-secondary); text-transform:uppercase; '
                f'letter-spacing:0.04em;">Selected</div>'
                f'<div style="font-size:15px; font-weight:700; color:var(--text-primary);">'
                f'{_format_period_range(selected)}</div>',
                unsafe_allow_html=True,
            )
        with col_b:
            st.markdown(
                f'<div style="font-size:12px; color:var(--text-secondary); text-transform:uppercase; '
                f'letter-spacing:0.04em;">Previous</div>'
                f'<div style="font-size:15px; font-weight:700; color:var(--text-primary);">'
                f'{_format_period_range(comparison_period)}</div>',
                unsafe_allow_html=True,
            )

        st.markdown("<div style='height:0.7rem'></div>", unsafe_allow_html=True)

        cols = st.columns(3)
        with cols[0]:
            gross_diff = None
            if selected.get("gross_cost") is not None and comparison_period.get("gross_cost") is not None:
                gross_diff = round(selected["gross_cost"] - comparison_period["gross_cost"], 2)
            kpi_card("Gross Cost Change", _format_signed_money(gross_diff, currency), icon="🧾", icon_kind="blue")
        with cols[1]:
            kpi_card("Credits Change", _format_signed_money(credit_comparison.get("difference"), currency), icon="🏷️", icon_kind="green")
        with cols[2]:
            difference = comparison.get("difference")
            pct = comparison.get("percentage_change")
            diff_kind = "red" if (difference or 0) > 0 else "green" if (difference or 0) < 0 else "accent"
            label = _format_signed_money(difference, currency)
            if pct is not None:
                label += f" ({_format_percent(pct)})"
            kpi_card("Net Cost Change", label, icon="📊", icon_kind=diff_kind)

        st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)

        col_svc, col_reg = st.columns(2)
        with col_svc:
            st.markdown(
                '<div style="font-size:13px; font-weight:600; color:var(--text-primary); margin-bottom:0.4rem;">'
                'Service Movers</div>',
                unsafe_allow_html=True,
            )
            _render_mover_table(comparison.get("service_comparison") or [], "service", currency)
        with col_reg:
            st.markdown(
                '<div style="font-size:13px; font-weight:600; color:var(--text-primary); margin-bottom:0.4rem;">'
                'Region Movers</div>',
                unsafe_allow_html=True,
            )
            _render_mover_table(comparison.get("region_comparison") or [], "region", currency)


def _render_mover_table(items: list, label_key: str, currency) -> None:
    if not items:
        empty_state(f"No {label_key} data for either period", "", "🧾")
        return

    top = items[:8]
    rows = [
        [
            item.get(label_key) or "Unknown",
            _format_money(item.get("period_a_cost"), currency),
            _format_money(item.get("period_b_cost"), currency),
            _format_signed_money(item.get("difference"), currency),
            _format_percent(item.get("percentage_change")),
        ]
        for item in top
    ]
    render_table([label_key.title(), "Selected", "Previous", "Difference", "% Change"], rows, "No data", "", "🧾")


# ---------------------------------------------------------------------------
# AI Cost Analysis - Gemini's interpretation, using real data from both periods
# ---------------------------------------------------------------------------

def _render_qa_row(question: str, answer: str) -> None:
    st.markdown(
        f"""
        <div style="margin-bottom:0.8rem;">
          <div style="font-size:12.5px; font-weight:600; color:var(--text-secondary); margin-bottom:0.2rem;">{question}</div>
          <div style="font-size:14px; color:var(--text-primary); line-height:1.5;">{answer}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _driver_name(driver) -> str | None:
    """A driver entry SHOULD be a plain name string per the prompt's
    schema (see llm/cost_prompt_builder.py). Defensively handles Gemini
    still returning a breakdown-shaped object anyway - extracts the name
    field, never falls through to a raw str(dict)/str(list) repr, which
    is exactly the bug this function exists to prevent."""
    if isinstance(driver, str):
        return driver or None
    if isinstance(driver, dict):
        return driver.get("service") or driver.get("region") or driver.get("name")
    return None


def _render_drivers_list(title: str, driver_names: list, breakdown: list, label_key: str, currency) -> None:
    """Renders a numbered list of real gross/credits/net figures for the
    named drivers, looked up from `breakdown` (the actual AWS-sourced
    merged service_breakdown/region_breakdown - never from whatever
    number Gemini itself might have echoed back). A name Gemini
    mentioned that isn't found in the real breakdown is shown without
    fabricated figures rather than guessing one."""
    names = [n for n in (_driver_name(d) for d in (driver_names or [])) if n]
    if not names:
        return

    by_name = {item.get(label_key): item for item in (breakdown or []) if item.get(label_key)}

    st.markdown(
        f'<div style="font-size:12.5px; font-weight:600; color:var(--text-secondary); margin-bottom:0.3rem;">{title}</div>',
        unsafe_allow_html=True,
    )

    rows_html = []
    for i, name in enumerate(names, start=1):
        entry = by_name.get(name)
        if entry:
            entry_currency = entry.get("currency") or currency
            detail = (
                f'Gross: {_format_money(entry.get("gross_cost"), entry_currency)} &middot; '
                f'Credits: {_format_signed_money(entry.get("credits"), entry_currency) if entry.get("credits") else "$0.00"} &middot; '
                f'Net: {_format_money(entry.get("net_cost"), entry_currency)}'
            )
        else:
            detail = "cost data unavailable for this name"
        rows_html.append(
            f'<div style="padding:0.35rem 0; border-bottom:1px solid var(--border-subtle);">'
            f'<div style="font-size:13.5px; font-weight:600; color:var(--text-primary);">{i}. {name}</div>'
            f'<div style="font-size:12.5px; color:var(--text-secondary); margin-top:1px;">{detail}</div></div>'
        )

    st.markdown(f'<div style="margin-bottom:0.8rem;">{"".join(rows_html)}</div>', unsafe_allow_html=True)


def _looks_like_parse_failure(report: dict) -> bool:
    """CostAnalyzer.run() stores {"raw_response": <text>} when Gemini's
    response couldn't be parsed as JSON (see analyzer/cost_analyzer.py) -
    no other report shape has raw_response without also having summary."""
    return bool(report.get("raw_response")) and report.get("summary") is None


def _render_comparison_analysis(report: dict, comparison: dict, currency) -> tuple:
    """The comparison-scoped mini-report - explanation/root_cause/
    evidence/recommendations/drivers, all about comparison.selected_period
    vs comparison.comparison_period specifically, never mixed with the
    separate always-on current_period/previous_period analysis below.
    This keeps the whole AI Cost Analysis section talking about ONE
    period pair whenever a comparison is active.

    Returns (root_cause, evidence, recommendations) for the caller to
    render as sibling cards AFTER this function's own `with card():`
    block has closed - matching the page's existing layout, where Root
    Cause/Evidence/Recommendations are never nested inside another card."""
    selected = comparison.get("selected_period") or {}
    comparison_period = comparison.get("comparison_period") or {}

    st.markdown(
        f'<div style="font-size:12px; color:var(--text-muted); margin-bottom:0.6rem;">'
        f'Comparing {_format_period_range(selected)} against {_format_period_range(comparison_period)}</div>',
        unsafe_allow_html=True,
    )

    if _looks_like_parse_failure(report):
        st.warning("Gemini's response for this refresh could not be parsed as valid JSON.")
        with st.expander("Technical details"):
            st.code(str(report.get("raw_response")), language=None)
        return None, [], []

    analysis = report.get("comparison_analysis")

    if not isinstance(analysis, dict):
        # A comparison was requested and the report itself exists, but
        # comparison_analysis is missing/null - per the prompt, Gemini is
        # explicitly told this is required whenever "comparison" is
        # present, so an absent value here means the response didn't
        # follow the schema this refresh, not that Gemini "had nothing to
        # say." Distinct, honest message - never the generic fallback.
        st.info("Gemini did not return a comparison analysis for this refresh. Try clicking Refresh again.")
        return None, [], []

    explanation = analysis.get("explanation")
    if not explanation:
        st.info("Gemini's comparison analysis is missing an explanation for this refresh.")
    else:
        _render_qa_row("Why did costs change?", explanation)

    _render_drivers_list(
        "Services driving the change",
        analysis.get("service_drivers"),
        selected.get("service_breakdown"),
        "service",
        currency,
    )
    _render_drivers_list(
        "Regions driving the change",
        analysis.get("region_drivers"),
        selected.get("region_breakdown"),
        "region",
        currency,
    )

    if analysis.get("credits_impact"):
        _render_qa_row("What happened to credits?", analysis["credits_impact"])
    if analysis.get("anomalies_summary"):
        _render_qa_row("Were anomalies detected?", analysis["anomalies_summary"])

    return analysis.get("root_cause"), analysis.get("evidence") or [], analysis.get("recommendations") or []


def _render_default_analysis(report: dict, current_period: dict, currency) -> tuple:
    """The always-on analysis of context.current_period vs
    previous_period (the default lookback, independent of any
    user-selected comparison) - shown only when no comparison is
    active, so it's never juxtaposed with a different date range.
    Returns (root_cause, evidence, recommendations) - see
    _render_comparison_analysis's docstring for why."""
    badge_html = severity_badge(report.get("severity", "")) if report.get("severity") else ""
    st.markdown(f'<div style="margin-bottom:0.6rem;">{badge_html}</div>', unsafe_allow_html=True)

    _render_qa_row("AI Summary", report.get("summary") or "No summary available.")

    _render_drivers_list(
        "Services driving the change",
        report.get("top_cost_drivers"),
        (current_period or {}).get("service_breakdown"),
        "service",
        currency,
    )
    _render_drivers_list(
        "Regions driving the change",
        report.get("top_cost_drivers"),
        (current_period or {}).get("region_breakdown"),
        "region",
        currency,
    )

    anomaly_findings = report.get("anomaly_findings") or []
    if anomaly_findings:
        findings_text = "<br>".join(str(f) for f in anomaly_findings if isinstance(f, str))
        if findings_text:
            _render_qa_row("Were anomalies detected?", findings_text)

    return report.get("root_cause"), report.get("evidence") or [], report.get("recommendations") or []


def _render_root_cause_evidence_recommendations(root_cause, evidence: list, recommendations: list) -> None:
    st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)

    col_root, col_evidence = st.columns(2)

    with col_root, card():
        card_title("Root Cause")
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


def _render_ai_cost_analysis(report: dict, comparison: dict, current_period: dict) -> None:
    """report/comparison/current_period are ALL from the same Refresh -
    see render()'s single set of service calls below. has_comparison
    decides which single, consistent period pair the whole section (AI
    Summary/explanation, drivers, root cause, evidence, recommendations)
    describes - never a mix of the comparison's user-picked dates and
    the separate always-on current_period/previous_period lookback."""
    currency = (current_period or {}).get("currency")
    has_comparison = bool(comparison and comparison.get("selected_period"))

    root_cause, evidence, recommendations = None, [], []
    show_footer = False

    with card():
        card_title("AI Cost Analysis")

        if not report:
            empty_state("No AI cost analysis yet", "Click Refresh to run a Cost Explorer query and Gemini analysis", "🤖")
        elif has_comparison:
            root_cause, evidence, recommendations = _render_comparison_analysis(report, comparison, currency)
            show_footer = True
        elif _looks_like_parse_failure(report):
            st.warning("Gemini's response for this refresh could not be parsed as valid JSON.")
            with st.expander("Technical details"):
                st.code(str(report.get("raw_response")), language=None)
        else:
            root_cause, evidence, recommendations = _render_default_analysis(report, current_period, currency)
            show_footer = True

    if show_footer:
        _render_root_cause_evidence_recommendations(root_cause, evidence, recommendations)


# ---------------------------------------------------------------------------
# Page entry point
# ---------------------------------------------------------------------------

def render(services, config) -> None:
    page_header("Cost Explorer", "AWS billing and cost analysis for the current account")

    summary = services.cost_explorer.get_summary()

    _render_header_and_controls(services, summary)

    st.markdown("<div style='height:0.9rem'></div>", unsafe_allow_html=True)

    _render_cost_summary(summary)

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    comparison = services.cost_explorer.get_comparison()

    _render_cost_trend(services.cost_explorer.get_history(), comparison)

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    _render_cost_by_service(services.cost_explorer.get_services())

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    _render_cost_by_region(services.cost_explorer.get_regions())

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    _render_credits_section(services.cost_explorer.get_credits())

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    _render_anomalies_section(services.cost_explorer.get_anomalies())

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    _render_period_comparison(comparison)

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    _render_ai_cost_analysis(services.cost_explorer.get_report(), comparison, summary.get("current_period"))

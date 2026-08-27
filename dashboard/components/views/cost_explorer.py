"""
Cost Explorer page — AWS billing/cost visibility, completely separate
from the infra Investigation pipeline. Every value shown here comes from
a real AWS Cost Explorer query (collector/cost_explorer.py via
services.cost_explorer / services.cost_refresh); nothing is fabricated,
and nothing here reads or displays infra investigation report data.

Two purpose-built interfaces, one shared date range:

    Cost Overview     - "what is my AWS usage/cost for this period?"
                        Gross/Credits/Net, credit coverage & status,
                        trend, service/region breakdown, credits,
                        anomalies. No historical comparison here.

    Cost Comparison   - "how did my cost change, and why?"
                        selected vs comparison period, gross/credits/net
                        change, service/region movers, comparison trend,
                        and the full AI cost analysis (summary, drivers,
                        credit impact, anomaly analysis, explanation,
                        evidence, recommendations).

Both tabs are driven by ONE shared From/To date picker + Refresh button,
rendered once above the tabs (Streamlit tabs are not lazy - both bodies
run every rerun - so a single shared widget is what keeps the two views
from ever showing two different periods). Every refresh through it
populates context/cost_context_builder.py's `comparison.selected_period`
with exactly the picked range - Overview reads that same period, not the
separate always-on `current_period` default lookback, so "what you
picked" and "what you see" are always the same thing on both tabs.

See context/cost_context_builder.py's docstring for the exact data model
(current_period/previous_period/change/service_comparison/
region_comparison/anomalies/comparison) this file reads from - nothing
here recomputes gross/credits/net/difference; it only formats and
displays values the backend already derived.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import streamlit as st

from services import CostExplorerActionError, invalidate

from ..cards import card, card_title, empty_state, kpi_card
from ..charts import horizontal_bar, multi_line_trend
from ..formatting import time_ago
from ..tables import render_table
from ..topbar import page_header

_REFRESH_ERROR_KEY = "cost_explorer_refresh_error"
_DATE_RANGE_ERROR_KEY = "cost_explorer_date_range_error"
_FROM_DATE_KEY = "cost_explorer_from_date"
_TO_DATE_KEY = "cost_explorer_to_date"

_SHOW_ALL_OVERVIEW_SERVICES_KEY = "cost_explorer_show_all_overview_services"
_SHOW_ALL_OVERVIEW_REGIONS_KEY = "cost_explorer_show_all_overview_regions"
_SHOW_ALL_MOVER_SERVICES_KEY = "cost_explorer_show_all_mover_services"
_SHOW_ALL_MOVER_REGIONS_KEY = "cost_explorer_show_all_mover_regions"

_TOP_N_DEFAULT = 12

# UI-only initial widget default (days), mirroring the backend's own
# default lookback window purely as a starting suggestion - the user can
# freely pick any other range via the date pickers, and whatever they
# pick is what's actually sent; this number is never part of the
# comparison-period calculation itself.
_DEFAULT_COMPARISON_DAYS = 14

_RESOURCE_LEVEL_CREDIT_NOTE = (
    "AWS Cost Explorer reports these credits at the service/region level. "
    "Individual resource attribution is not available from the current billing data."
)


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


# ---------------------------------------------------------------------------
# Credit coverage / credit status - Overview-only presentation helpers.
# Pure formatting over gross_cost/credits/net_cost the backend already
# derived (context/cost_context_builder.py::_derive_gross_cost) - no new
# arithmetic, just deciding which honest label applies.
# ---------------------------------------------------------------------------

def _credit_coverage(gross, credits_total, currency) -> tuple[str, str]:
    """Returns (percent_label, subtext). Never divides by a zero/missing
    gross_cost - that would be a misleading percentage, not a real one."""
    if gross is None:
        return "—", "No cost data yet"
    if gross == 0:
        return "—", "No usage this period"

    credits_abs = abs(credits_total) if credits_total is not None else 0.0
    percent = min(credits_abs / gross * 100, 100.0)

    if credits_abs == 0:
        subtext = "No credits applied"
    else:
        subtext = f"{_format_money(credits_abs, currency)} of {_format_money(gross, currency)} covered"

    return f"{percent:.0f}%", subtext


_KPI_ICON_COLORS = {
    "accent": ("var(--accent)", "var(--accent-soft)"),
    "green": ("var(--green)", "var(--green-soft)"),
    "blue": ("var(--blue)", "var(--blue-soft)"),
    "amber": ("var(--amber)", "var(--amber-soft)"),
}


def _render_kpi_with_subtext(label: str, value: str, subtext: str, icon: str, icon_kind: str) -> None:
    """Same visual language as cards.py's kpi_card, with a free-text
    subtext line instead of a directional delta - kpi_card's delta is
    built for an up/down percentage-change indicator, not an arbitrary
    string like "$26.27 of $26.27 covered", so this is a small local
    variant rather than overloading the shared component (which other
    pages also use)."""
    fg, bg = _KPI_ICON_COLORS.get(icon_kind, _KPI_ICON_COLORS["accent"])
    st.markdown(
        f"""
        <div class="ao-kpi">
          <div class="ao-kpi-row">
            <div>
              <div class="ao-kpi-label">{label}</div>
              <div class="ao-kpi-value">{value}</div>
            </div>
            <div class="ao-kpi-icon" style="background:{bg}; color:{fg};">{icon}</div>
          </div>
          <div style="font-size:11.5px; color:var(--text-secondary); margin-top:0.35rem;">{subtext}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_credit_status_banner(gross, credits_total, net_cost, currency) -> None:
    if gross is None or net_cost is None:
        return

    credits_abs = abs(credits_total) if credits_total is not None else 0.0

    if credits_abs == 0:
        st.markdown(
            '<div style="margin-top:0.8rem; font-size:13px; color:var(--text-secondary);">'
            "No AWS credits applied during this period.</div>",
            unsafe_allow_html=True,
        )
        return

    fully_covered = round(net_cost, 2) == 0 and round(gross, 2) > 0
    icon = "✓" if fully_covered else "◐"
    label = "Fully covered by AWS credits" if fully_covered else "Partially covered by AWS credits"
    bg = "var(--green-soft)" if fully_covered else "var(--amber-soft)"
    border = "rgba(34,197,94,0.3)" if fully_covered else "rgba(245,158,11,0.35)"

    def _row(row_label, row_value, weight=600, top_border=False):
        border_style = "border-top:1px solid var(--border-subtle); margin-top:0.2rem; padding-top:0.4rem;" if top_border else ""
        return (
            f'<div style="display:flex; justify-content:space-between; font-size:12.5px; '
            f'padding:0.15rem 0; {border_style}">'
            f'<span style="color:var(--text-secondary);">{row_label}</span>'
            f'<span style="color:var(--text-primary); font-weight:{weight};">{row_value}</span></div>'
        )

    st.markdown(
        f"""
        <div style="margin-top:0.8rem; padding:0.8rem 1rem; border-radius:var(--radius-sm);
                    background:{bg}; border:1px solid {border};">
          <div style="font-size:13.5px; font-weight:700; color:var(--text-primary); margin-bottom:0.55rem;">
            {icon} {label}
          </div>
          {_row("Gross usage", _format_money(gross, currency))}
          {_row("AWS credits", f"−{_format_money(credits_abs, currency)}")}
          {_row("Net cost", _format_money(net_cost, currency), weight=700, top_border=True)}
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Shared controls - one date picker + Refresh, above both tabs
# ---------------------------------------------------------------------------

def _default_from_date() -> date:
    return date.today() - timedelta(days=_DEFAULT_COMPARISON_DAYS - 1)


def _default_to_date() -> date:
    return date.today()


def _render_shared_controls(services, summary: dict) -> None:
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
            st.date_input("From", value=_default_from_date(), key=_FROM_DATE_KEY)
        with col_to:
            st.date_input("To", value=_default_to_date(), key=_TO_DATE_KEY)
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
        with st.spinner("Updating cost data…"):
            services.cost_refresh.refresh(from_date=from_date_str, to_date=to_date_str)
    except CostExplorerActionError as exc:
        st.session_state[_REFRESH_ERROR_KEY] = f"Unable to update cost data. {exc}"
        st.rerun()
        return

    # A fresh feed was just published on the backend for every section
    # (summary/history/services/regions/credits/anomalies/comparison/
    # report) - drop every cached read so this rerun pulls all of it,
    # never a stale section left over from the previous date selection.
    invalidate()
    st.success("Data updated")
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


def _active_period(summary: dict, comparison: dict) -> dict:
    """The single period both Overview's numbers and the shared date
    picker agree on: comparison.selected_period whenever one has been
    fetched (every refresh through the picker above populates it with
    exactly the picked range), falling back to the always-on
    current_period default lookback only if nothing has ever been
    fetched through that flow (e.g. an external caller hit
    POST /cost-explorer/refresh with no dates) - never a blank view when
    real data exists."""
    selected = (comparison or {}).get("selected_period")
    if selected:
        return selected
    return summary.get("current_period") or {}


def _active_anomalies(comparison: dict, anomalies_fallback: dict) -> dict:
    """Same fallback rule as _active_period, for anomalies specifically -
    comparison.anomaly_comparison.selected_period is anomalies for
    exactly the picked range (same status/reason/anomalies shape as the
    top-level feed), computed by the same get_anomalies() call."""
    selected = ((comparison or {}).get("anomaly_comparison") or {}).get("selected_period")
    if selected:
        return selected
    return anomalies_fallback or {}


# ---------------------------------------------------------------------------
# COST OVERVIEW TAB
# ---------------------------------------------------------------------------

def _render_overview_tab(period: dict, anomalies: dict) -> None:
    gross_cost = period.get("gross_cost")
    net_cost = period.get("net_cost")

    if gross_cost is None and net_cost is None:
        empty_state(
            "No cost data yet",
            "Click Refresh above to run a Cost Explorer query for this account.",
            "💳",
        )
        return

    _render_overview_summary(period)
    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)
    _render_overview_trend(period)
    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)
    _render_overview_breakdown_card("Cost by Service", period.get("service_breakdown") or [], "service",
                                     period.get("currency"), _SHOW_ALL_OVERVIEW_SERVICES_KEY,
                                     "No service-level cost data is available.")
    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)
    _render_overview_region_card(period)
    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)
    _render_overview_credits(period)
    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)
    _render_anomalies_section(anomalies, period.get("currency"))


def _render_overview_summary(period: dict) -> None:
    currency = period.get("currency")
    gross_cost = period.get("gross_cost")
    credits_total = (period.get("credits") or {}).get("total")
    net_cost = period.get("net_cost")

    with card():
        card_title("Cost Summary", f"{_format_period_range(period)}")

        cols = st.columns(4)
        with cols[0]:
            kpi_card("Gross Usage", _format_money(gross_cost, currency), icon="🧾", icon_kind="blue")
        with cols[1]:
            credits_display = abs(credits_total) if credits_total is not None else None
            kpi_card("AWS Credits", f"−{_format_money(credits_display, currency)}" if credits_display else "—",
                      icon="🏷️", icon_kind="green")
        with cols[2]:
            kpi_card("Net Cost", _format_money(net_cost, currency), icon="💳", icon_kind="accent")
        with cols[3]:
            percent, subtext = _credit_coverage(gross_cost, credits_total, currency)
            _render_kpi_with_subtext("Credit Coverage", percent, subtext, icon="🛡️", icon_kind="amber")

        st.markdown(
            """
            <div style="margin-top:0.9rem; font-size:12.5px; color:var(--text-secondary); line-height:1.6;">
              <b>Gross Usage</b> = AWS usage before credits &nbsp;·&nbsp;
              <b>AWS Credits</b> = credits applied &nbsp;·&nbsp;
              <b>Net Cost</b> = amount after credits
            </div>
            """,
            unsafe_allow_html=True,
        )

        _render_credit_status_banner(gross_cost, credits_total, net_cost, currency)


def _render_overview_trend(period: dict) -> None:
    daily = period.get("daily_breakdown") or []

    with card():
        card_title("Cost Trend", f"{len(daily)} day(s) — {_format_period_range(period)}")

        if not daily:
            empty_state(
                "No daily cost data was returned by AWS for this period",
                "Click Refresh above to fetch AWS Cost Explorer data",
                "📈",
            )
            return

        x = [_format_chart_date(point[0]) for point in daily]
        series = {
            "Gross": [point[1] for point in daily],
            "Credits": [point[2] for point in daily],
            "Net": [point[3] for point in daily],
        }
        st.plotly_chart(multi_line_trend(x, series, height=280), config={"displayModeBar": False})
        st.caption("Hover a point for the exact daily value.")


def _nonzero_entries(breakdown: list) -> list:
    return [item for item in (breakdown or []) if (item.get("gross_cost") or item.get("net_cost") or item.get("credits"))]


def _render_overview_breakdown_card(title: str, breakdown: list, label_key: str, currency, show_all_key: str, empty_message: str) -> None:
    with card():
        card_title(title)

        if not breakdown:
            empty_state(empty_message, "", "🧾")
            return

        nonzero = _nonzero_entries(breakdown)
        show_all = st.checkbox("Show all", key=show_all_key, value=False)
        items = breakdown if show_all else nonzero

        if not items:
            empty_state(f"No {label_key}s with nonzero cost for this period.", "", "🧾")
            return

        # Sorted by gross usage (already how the backend orders this
        # list - see CostContextBuilder._merge_breakdown_with_credits).
        top = items if show_all else items[:_TOP_N_DEFAULT]

        chart_labels = [item.get(label_key) or "Unknown" for item in reversed(top)]
        chart_values = [item.get("gross_cost") or 0 for item in reversed(top)]
        st.plotly_chart(horizontal_bar(chart_labels, chart_values, height=max(220, 30 * len(top))), config={"displayModeBar": False})

        rows = [
            [
                item.get(label_key) or "Unknown",
                _format_money(item.get("gross_cost"), item.get("currency") or currency),
                _format_signed_money(item.get("credits"), item.get("currency") or currency) if item.get("credits") else "—",
                _format_money(item.get("net_cost"), item.get("currency") or currency),
            ]
            for item in top
        ]
        render_table([label_key.title(), "Gross", "Credits", "Net"], rows, empty_message, "", "🧾")

        if not show_all and len(nonzero) > _TOP_N_DEFAULT:
            st.caption(f"Showing top {_TOP_N_DEFAULT} of {len(nonzero)} — check \"Show all\" to see everything.")


def _render_overview_region_card(period: dict) -> None:
    breakdown = period.get("region_breakdown") or []
    total_region_credits = sum(abs(item.get("credits") or 0) for item in breakdown)

    _render_overview_breakdown_card(
        "Cost by Region", breakdown, "region", period.get("currency"),
        _SHOW_ALL_OVERVIEW_REGIONS_KEY, "No regional cost data was returned by AWS for this period.",
    )

    if breakdown and total_region_credits == 0:
        st.caption("Credits are reported at account level by AWS and are not allocated to individual regions.")


def _render_overview_credits(period: dict) -> None:
    currency = period.get("currency")
    credits = period.get("credits") or {}
    total = credits.get("total")
    history = credits.get("history") or []
    by_service = [item for item in (period.get("service_breakdown") or []) if item.get("service") and item.get("credits")]

    with card():
        card_title("AWS Credits")

        total_label = _format_money(abs(total), currency) if total is not None else "—"
        st.markdown(
            f'<div style="font-size:13.5px; color:var(--text-secondary); margin-bottom:0.6rem;">'
            f'Total credits: <span style="color:var(--text-primary); font-weight:600;">{total_label}</span></div>',
            unsafe_allow_html=True,
        )

        if not history and total is None:
            empty_state("No credit data is available.", "Click Refresh above to fetch AWS Cost Explorer data", "🏷️")
            return

        st.markdown(
            '<div style="font-size:13px; font-weight:600; color:var(--text-primary); margin:0.6rem 0 0.4rem;">'
            'Credit History</div>',
            unsafe_allow_html=True,
        )
        if history:
            x = [_format_chart_date(point[0]) for point in history]
            y = [abs(point[1]) for point in history]
            st.plotly_chart(multi_line_trend(x, {"Credits": y}, height=220), config={"displayModeBar": False})
            st.caption("Hover a point for the exact daily credit amount.")
        else:
            empty_state("No credit records for this period", "AWS returned no RECORD_TYPE=Credit entries", "🏷️")

        st.markdown(
            '<div style="font-size:13px; font-weight:600; color:var(--text-primary); margin:1rem 0 0.4rem;">'
            'Where were credits applied?</div>',
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

        st.caption(f"ℹ️ {_RESOURCE_LEVEL_CREDIT_NOTE}")


# ---------------------------------------------------------------------------
# Cost Anomalies - shared by Overview (raw AWS fields) - every AWS-
# provided field per anomaly, no fabricated severity.
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
        ("Actual Spend", _format_money(anomaly.get("total_actual_spend"), currency)),
        ("Expected Spend", _format_money(anomaly.get("total_expected_spend"), currency)),
        ("Difference", _format_signed_money(anomaly.get("total_impact"), currency)),
    ]
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
            ⚠ Cost anomaly detected — {anomaly.get("service") or "Unknown service"}
          </div>
          {rows_html}
          {monitor_line}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_anomalies_section(anomalies: dict, currency=None) -> None:
    status = anomalies.get("status")
    findings = anomalies.get("anomalies") or []
    currency = anomalies.get("currency") or currency

    with card():
        card_title("Cost Anomalies")

        if status == "found" and findings:
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
            icon, label = "✓", "No cost anomalies detected"
            detail = anomalies.get("reason") or "No AWS cost anomaly was detected for this period."

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
# COST COMPARISON TAB
# ---------------------------------------------------------------------------

def _driver_name(driver) -> str | None:
    """A driver entry SHOULD be a plain name string per the prompt's
    schema (see llm/cost_prompt_builder.py). Defensively handles Gemini
    still returning a breakdown-shaped object anyway - extracts the name
    field, never falls through to a raw str(dict)/str(list) repr."""
    if isinstance(driver, str):
        return driver or None
    if isinstance(driver, dict):
        return driver.get("service") or driver.get("region") or driver.get("name")
    return None


def _render_comparison_tab(comparison: dict, report: dict) -> None:
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

    _render_comparison_header(selected, comparison_period)
    st.markdown("<div style='height:0.9rem'></div>", unsafe_allow_html=True)
    _render_period_summary(selected, comparison_period, comparison, currency)
    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)
    _render_movers(comparison, currency)
    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)
    _render_comparison_trend(comparison)
    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)
    _render_ai_cost_analysis(report, comparison, currency)


def _render_comparison_header(selected: dict, comparison_period: dict) -> None:
    with card():
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(
                '<div style="font-size:12px; color:var(--text-secondary); text-transform:uppercase; '
                'letter-spacing:0.04em;">Selected Period</div>'
                f'<div style="font-size:16px; font-weight:700; color:var(--text-primary);">'
                f'{_format_period_range(selected)}</div>',
                unsafe_allow_html=True,
            )
        with col_b:
            st.markdown(
                '<div style="font-size:12px; color:var(--text-secondary); text-transform:uppercase; '
                'letter-spacing:0.04em;">Compare Against</div>'
                f'<div style="font-size:16px; font-weight:700; color:var(--text-primary);">'
                f'{_format_period_range(comparison_period)}</div>',
                unsafe_allow_html=True,
            )
        st.caption(
            "Automatically computed: a complete calendar month compares against the preceding calendar "
            "month; any other range compares against the immediately preceding period of equal length."
        )


def _render_change_interpretation(gross_diff, credit_diff, net_diff, currency) -> str:
    """Pure formatting of numbers _render_period_summary already
    computed - no new arithmetic, just turning them into a sentence so
    the user isn't left to do the subtraction themselves."""
    if gross_diff is None or net_diff is None:
        return "Not enough data to describe what changed for this comparison."

    parts = []

    if gross_diff > 0:
        parts.append(f"Gross usage increased by {_format_money(gross_diff, currency)}.")
    elif gross_diff < 0:
        parts.append(f"Gross usage decreased by {_format_money(abs(gross_diff), currency)}.")
    else:
        parts.append("Gross usage was unchanged.")

    if credit_diff is not None:
        if credit_diff > 0:
            parts.append(f"AWS credits increased by {_format_money(credit_diff, currency)}.")
        elif credit_diff < 0:
            parts.append(f"AWS credits decreased by {_format_money(abs(credit_diff), currency)}.")

    if net_diff == 0:
        parts.append(f"Net cost remained unchanged at {_format_money(0, currency)}.")
    elif net_diff > 0:
        parts.append(f"Net cost increased by {_format_money(net_diff, currency)}.")
    else:
        parts.append(f"Net cost decreased by {_format_money(abs(net_diff), currency)}.")

    return " ".join(parts)


def _render_period_summary(selected: dict, comparison_period: dict, comparison: dict, currency) -> None:
    with card():
        card_title("Period Summary")

        col_prev, col_sel = st.columns(2)
        for col, label, period in ((col_prev, "Previous Period", comparison_period), (col_sel, "Selected Period", selected)):
            with col:
                st.markdown(
                    f'<div style="font-size:12px; color:var(--text-secondary); text-transform:uppercase; '
                    f'letter-spacing:0.04em; margin-bottom:0.3rem;">{label}</div>'
                    f'<div style="font-size:13px; color:var(--text-muted); margin-bottom:0.5rem;">{_format_period_range(period)}</div>',
                    unsafe_allow_html=True,
                )
                for row_label, value in (
                    ("Gross", _format_money(period.get("gross_cost"), currency)),
                    ("Credits", _format_signed_money((period.get("credits") or {}).get("total"), currency)),
                    ("Net", _format_money(period.get("net_cost"), currency)),
                ):
                    st.markdown(
                        f'<div style="display:flex; justify-content:space-between; padding:0.2rem 0; font-size:13px;">'
                        f'<span style="color:var(--text-secondary);">{row_label}</span>'
                        f'<span style="color:var(--text-primary); font-weight:600;">{value}</span></div>',
                        unsafe_allow_html=True,
                    )

        st.markdown("<div style='height:0.9rem'></div>", unsafe_allow_html=True)

        gross_diff = None
        if selected.get("gross_cost") is not None and comparison_period.get("gross_cost") is not None:
            gross_diff = round(selected["gross_cost"] - comparison_period["gross_cost"], 2)

        credit_comparison = comparison.get("credit_comparison") or {}
        credit_diff = credit_comparison.get("difference")
        net_diff = comparison.get("difference")
        pct = comparison.get("percentage_change")

        cols = st.columns(3)
        with cols[0]:
            kpi_card("Gross Cost Change", _format_signed_money(gross_diff, currency), icon="🧾", icon_kind="blue")
        with cols[1]:
            kpi_card("Credits Change", _format_signed_money(credit_diff, currency), icon="🏷️", icon_kind="green")
        with cols[2]:
            label = _format_signed_money(net_diff, currency)
            if pct is not None:
                label += f" ({_format_percent(pct)})"
            diff_kind = "red" if (net_diff or 0) > 0 else "green" if (net_diff or 0) < 0 else "accent"
            kpi_card("Net Cost Change", label, icon="📊", icon_kind=diff_kind)

        st.markdown("<div style='height:0.9rem'></div>", unsafe_allow_html=True)
        st.markdown(
            '<div style="font-size:13px; font-weight:600; color:var(--text-primary); margin-bottom:0.3rem;">What changed?</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="font-size:13.5px; color:var(--text-primary); line-height:1.6;">'
            f'{_render_change_interpretation(gross_diff, credit_diff, net_diff, currency)}</div>',
            unsafe_allow_html=True,
        )


def _render_mover_table(title: str, items: list, label_key: str, currency, show_all_key: str) -> None:
    with card():
        card_title(title)

        if not items:
            empty_state(f"No {label_key} data for either period", "", "🧾")
            return

        movers = [item for item in items if item.get("period_a_cost") or item.get("period_b_cost")]
        show_all = st.checkbox("Show all", key=show_all_key, value=False)
        rows_source = items if show_all else movers[:8]

        if not rows_source:
            empty_state(f"No {label_key} movers for this comparison.", "", "🧾")
            return

        rows = []
        for item in rows_source:
            previous_cost = item.get("period_b_cost")
            selected_cost = item.get("period_a_cost")
            pct = item.get("percentage_change")
            if not previous_cost and selected_cost:
                change_label = "New"
            else:
                change_label = _format_percent(pct)
            rows.append([
                item.get(label_key) or "Unknown",
                _format_money(previous_cost, currency),
                _format_money(selected_cost, currency),
                _format_signed_money(item.get("difference"), currency),
                change_label,
            ])

        render_table([label_key.title(), "Previous", "Selected", "Difference", "% Change"], rows, "No data", "", "🧾")

        if not show_all and len(movers) > 8:
            st.caption(f"Showing top 8 of {len(movers)} — check \"Show all\" to see everything.")


def _render_movers(comparison: dict, currency) -> None:
    col_svc, col_reg = st.columns(2)
    with col_svc:
        _render_mover_table("Service Changes", comparison.get("service_comparison") or [], "service", currency, _SHOW_ALL_MOVER_SERVICES_KEY)
    with col_reg:
        _render_mover_table("Regional Changes", comparison.get("region_comparison") or [], "region", currency, _SHOW_ALL_MOVER_REGIONS_KEY)


def _render_comparison_trend(comparison: dict) -> None:
    selected = comparison.get("selected_period") or {}
    comparison_period = comparison.get("comparison_period") or {}

    selected_daily = selected.get("daily_history") or []
    comparison_daily = comparison_period.get("daily_history") or []

    with card():
        card_title("Cost Comparison Over Time")

        if not selected_daily and not comparison_daily:
            empty_state("No daily cost data was returned by AWS for either period", "", "📈")
            return

        st.caption(
            f"Previous: {_format_period_range(comparison_period)}  vs  Selected: {_format_period_range(selected)}"
        )

        # The two periods have different real calendar dates but (per
        # _comparison_period()) the same duration - a shared x-axis needs
        # a relative "day of period" index rather than asserting one
        # period's actual dates apply to the other's values. The real
        # date ranges for each line are shown above, not hidden.
        length = max(len(selected_daily), len(comparison_daily))
        x = [f"Day {i + 1}" for i in range(length)]

        series = {}
        if comparison_daily:
            series[f"Previous ({_format_period_range(comparison_period)})"] = [p[1] for p in comparison_daily] + [None] * (length - len(comparison_daily))
        if selected_daily:
            series[f"Selected ({_format_period_range(selected)})"] = [p[1] for p in selected_daily] + [None] * (length - len(selected_daily))

        st.plotly_chart(multi_line_trend(x, series, height=280), config={"displayModeBar": False})
        if length != len(selected_daily) or length != len(comparison_daily):
            st.caption("The two periods have different lengths; days are aligned by position (Day 1, Day 2, …), not by calendar date.")


# ---------------------------------------------------------------------------
# AI Cost Analysis - Comparison tab only. Gemini's interpretation of the
# same selected_period/comparison_period data shown above, using the
# schema llm/cost_prompt_builder.py explicitly requires (comparison_
# analysis.{explanation, root_cause, evidence, recommendations,
# service_drivers, region_drivers, credits_impact, anomalies_summary}).
# ---------------------------------------------------------------------------

def _looks_like_parse_failure(report: dict) -> bool:
    """CostAnalyzer.run() stores {"raw_response": <text>} when Gemini's
    response couldn't be parsed as JSON (see analyzer/cost_analyzer.py) -
    no other report shape has raw_response without also having summary."""
    return bool(report.get("raw_response")) and report.get("summary") is None


def _render_comparison_drivers_card(title: str, driver_names: list, breakdown: list, comparison_list: list, label_key: str, currency) -> None:
    names = [n for n in (_driver_name(d) for d in (driver_names or [])) if n]

    with card():
        card_title(title)

        if not names:
            empty_state(f"Gemini did not identify any {label_key} drivers for this comparison.", "", "🧭")
            return

        by_name = {item.get(label_key): item for item in (breakdown or []) if item.get(label_key)}
        change_by_name = {item.get(label_key): item for item in (comparison_list or []) if item.get(label_key)}

        rows_html = []
        for i, name in enumerate(names, start=1):
            entry = by_name.get(name)
            change_entry = change_by_name.get(name)
            gross_text = _format_money(entry.get("gross_cost"), currency) if entry else "—"
            change_text = _format_signed_money(change_entry.get("difference"), currency) if change_entry else "—"
            rows_html.append(
                f'<div style="padding:0.35rem 0; border-bottom:1px solid var(--border-subtle);">'
                f'<div style="font-size:13.5px; font-weight:600; color:var(--text-primary);">{i}. {name}</div>'
                f'<div style="font-size:12.5px; color:var(--text-secondary); margin-top:1px;">'
                f'Gross: {gross_text} &middot; Change: {change_text}</div></div>'
            )

        st.markdown(f'<div>{"".join(rows_html)}</div>', unsafe_allow_html=True)


def _render_ai_anomaly_card(analysis: dict, anomaly_comparison: dict) -> None:
    with card():
        card_title("Anomaly Analysis")

        selected_status = ((anomaly_comparison or {}).get("selected_period") or {}).get("status")
        comparison_status = ((anomaly_comparison or {}).get("comparison_period") or {}).get("status")
        summary_text = (analysis or {}).get("anomalies_summary")

        if selected_status == "unavailable" or comparison_status == "unavailable":
            st.markdown("⚠ Unable to retrieve anomaly data.")
        elif selected_status == "unsupported_range" or comparison_status == "unsupported_range":
            st.markdown(
                "ℹ Anomaly analysis unavailable\n\n"
                "AWS Cost Anomaly Detection does not support the selected comparison range."
            )
        elif selected_status == "not_configured":
            st.markdown("ℹ Anomaly Detection Not Configured\n\nNo AWS Cost Anomaly monitors are configured for this account.")
        elif summary_text:
            st.markdown(f"✓ {summary_text}" if selected_status == "none_found" else summary_text)
        else:
            st.markdown("✓ No anomalies detected")


def _render_ai_cost_analysis(report: dict, comparison: dict, currency) -> None:
    st.markdown(
        '<div style="display:flex; align-items:center; gap:8px; margin:0.2rem 0 0.8rem;">'
        '<span style="font-size:18px;">✨</span>'
        '<span style="font-size:15px; font-weight:700; color:var(--text-primary);">AI Cost Analysis</span></div>',
        unsafe_allow_html=True,
    )

    selected = comparison.get("selected_period") or {}
    comparison_period = comparison.get("comparison_period") or {}
    st.caption(f"Selected: {_format_period_range(selected)}  ·  Previous: {_format_period_range(comparison_period)}")

    if not report:
        with card():
            card_title("AI Summary")
            empty_state("AI analysis is currently unavailable.", "AWS cost data loaded successfully.", "🤖")
        return

    if _looks_like_parse_failure(report):
        with card():
            card_title("AI Summary")
            st.warning("AWS cost data loaded successfully.\n\nAI analysis is currently unavailable — Gemini's response for this refresh could not be parsed.")
            with st.expander("Technical details"):
                st.code(str(report.get("raw_response")), language=None)
        return

    analysis = report.get("comparison_analysis")

    if not isinstance(analysis, dict):
        with card():
            card_title("AI Summary")
            st.info("AWS cost data loaded successfully.\n\nAI analysis is currently unavailable for this refresh. Try clicking Refresh again.")
        return

    with card():
        card_title("AI Summary")
        explanation = analysis.get("explanation")
        if explanation:
            st.markdown(f'<div style="font-size:14px; color:var(--text-primary); line-height:1.5;">{explanation}</div>', unsafe_allow_html=True)
        else:
            st.info("AWS cost data loaded successfully.\n\nAI analysis is currently unavailable — Gemini did not return an explanation for this refresh.")

    st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)

    col_svc, col_reg = st.columns(2)
    with col_svc:
        _render_comparison_drivers_card(
            "Main Cost Drivers", analysis.get("service_drivers"),
            selected.get("service_breakdown"), comparison.get("service_comparison"), "service", currency,
        )
    with col_reg:
        _render_comparison_drivers_card(
            "Regional Drivers", analysis.get("region_drivers"),
            selected.get("region_breakdown"), comparison.get("region_comparison"), "region", currency,
        )

    st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)

    with card():
        card_title("AWS Credit Impact")
        credits_impact = analysis.get("credits_impact")
        if credits_impact:
            st.markdown(f'<div style="font-size:14px; color:var(--text-primary); line-height:1.5;">{credits_impact}</div>', unsafe_allow_html=True)
        else:
            empty_state("No credit impact analysis available.", "", "🏷️")

    st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)

    _render_ai_anomaly_card(analysis, comparison.get("anomaly_comparison"))

    st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)

    col_root, col_evidence = st.columns(2)

    with col_root, card():
        card_title("Cost Explanation")
        root_cause = analysis.get("root_cause")
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
            empty_state("Cost explanation not yet determined", "", "🧩")

    with col_evidence, card():
        card_title("Evidence")
        evidence = analysis.get("evidence") or []
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
        recommendations = analysis.get("recommendations") or []
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
# Page entry point
# ---------------------------------------------------------------------------

def render(services, config) -> None:
    page_header("Cost Explorer", "AWS billing, usage, and cost comparison for the current account")

    summary = services.cost_explorer.get_summary()
    comparison = services.cost_explorer.get_comparison()

    _render_shared_controls(services, summary)

    st.markdown("<div style='height:0.9rem'></div>", unsafe_allow_html=True)

    tab_overview, tab_comparison = st.tabs(["💰 Cost Overview", "📊 Cost Comparison"])

    with tab_overview:
        period = _active_period(summary, comparison)
        anomalies_fallback = services.cost_explorer.get_anomalies()
        anomalies = _active_anomalies(comparison, anomalies_fallback)
        _render_overview_tab(period, anomalies)

    with tab_comparison:
        report = services.cost_explorer.get_report()
        _render_comparison_tab(comparison, report)

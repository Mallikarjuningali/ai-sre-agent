"""
=========================================================
AI SRE AGENT
Module : Cost Context Builder
Purpose:
    Read the Cost Explorer collector's raw output and produce one
    AI-ready cost context document. Completely separate from
    context/context_builder.py (EC2/ALB/ASG resource-index logic) - Cost
    Explorer has a single account-level scope, not a fan-out of
    per-resource contexts, so no resource index is needed here.

    This module does not compute severity, root cause, or
    recommendations - it only assembles the facts (real AWS values) that
    llm/cost_prompt_builder.py will hand to Gemini for interpretation.
    The only derived values it computes (gross_cost per period/service/
    region, and the various *_change/*_change_percent/difference/
    percentage_change figures) are arithmetic over real AWS totals -
    never an invented or hardcoded number.

    Data model (see collector/cost_explorer.py's get_period_data() for
    the raw per-period bundle this is built from):

        current_period / previous_period: {
            from, to, currency, gross_cost, credits, net_cost,
            daily_history, service_breakdown, region_breakdown
        }
        change: gross/credits/net_cost change (current vs previous_period)
        anomalies: AWS Cost Anomaly Detection findings for current_period
        comparison: None, or { selected_period, comparison_period,
            difference, percentage_change, service_comparison,
            region_comparison, credit_comparison, anomaly_comparison }
            for the optional user-selected Month/Period Comparison.

    service_breakdown/region_breakdown entries are
    {service|region, gross_cost, credits, net_cost, currency} - gross_cost
    is net_cost minus that dimension value's own credits, so a service
    fully offset by a credit still shows its real gross usage instead of
    disappearing at $0.
=========================================================
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class CostContextBuilder:

    def __init__(self):

        self.project_root = Path(__file__).resolve().parent.parent

        self.raw_directory = self.project_root / "output" / "cost" / "raw"

        self.context_directory = self.project_root / "output" / "cost" / "context"

        self.context_directory.mkdir(parents=True, exist_ok=True)

    def load_raw(self) -> Dict[str, Any]:

        path = self.raw_directory / "cost_explorer.json"

        if not path.exists():
            return {}

        try:
            with open(path, "r", encoding="utf-8") as file:
                return json.load(file)
        except (json.JSONDecodeError, OSError):
            return {}

    # =====================================================
    # Shared arithmetic - one implementation each, reused at every level
    # (period, service, region, comparison)
    # =====================================================

    @staticmethod
    def _percent_change(new_value, old_value):
        """(new - old) / old * 100, or None when either value is missing
        or old_value is exactly zero (division undefined). Checks
        "is not None", never truthiness: old_value can legitimately be a
        real AWS-reported 0.0, which must still be distinguished from
        "missing"."""

        if new_value is None or old_value is None or old_value == 0:
            return None

        return round(((new_value - old_value) / old_value) * 100, 2)

    @staticmethod
    def _difference(new_value, old_value):
        """new - old, or None when either value is missing. The single
        shared formula for every *_change/difference field below - one
        implementation, not one per field."""

        if new_value is None or old_value is None:
            return None

        return round(new_value - old_value, 2)

    @staticmethod
    def _derive_gross_cost(net_cost, credits):
        """net_cost (UnblendedCost, unfiltered) already includes credit
        records - AWS has no way to exclude a record type from it - so
        it IS the net-of-credits figure. gross_cost is simply the
        arithmetic inverse: net minus the (negative-or-zero) credits
        total recovers what the cost would have been before credits were
        applied - no separate AWS query, no invented number. Shared by
        every period (top-level, comparison) and, via
        _merge_breakdown_with_credits, every service/region entry too."""

        credits_total = (credits or {}).get("total")

        if net_cost is None or credits_total is None:
            return None

        return round(net_cost - credits_total, 2)

    @staticmethod
    def _merge_breakdown_with_credits(
        net_items: Optional[List[dict]], credit_items: Optional[List[dict]], label_key: str
    ) -> List[dict]:
        """Combines a net-cost breakdown (every record type, from
        collector.get_service_breakdown/get_region_breakdown) with a
        credit-only breakdown (RECORD_TYPE=Credit, from
        collector.get_service_credit_breakdown/get_region_credit_breakdown),
        both grouped by the same dimension, into one gross/credits/net
        entry per name:

            gross_cost = net_cost - credits   (credits is AWS's own
                                                negative-or-zero sign)

        Union of every name AWS returned in EITHER list - a name present
        in only one list gets 0 for the missing side, which is correct:
        AWS simply returned no group for it in that specific query (e.g.
        a service had usage but no credit that period, or vice versa).
        This is what lets a fully-credited service/region still show its
        real gross usage instead of disappearing at net $0."""

        net_map = {
            item.get(label_key): item.get("cost") or 0
            for item in (net_items or []) if item.get(label_key)
        }
        credit_map = {
            item.get(label_key): item.get("cost") or 0
            for item in (credit_items or []) if item.get(label_key)
        }

        currency = next(
            (item.get("currency") for item in (net_items or []) if item.get("currency")), None
        ) or next(
            (item.get("currency") for item in (credit_items or []) if item.get("currency")), None
        )

        names = sorted(set(net_map) | set(credit_map))

        merged = []
        for name in names:

            net_cost = round(net_map.get(name, 0), 2)
            credits = round(credit_map.get(name, 0), 2)
            gross_cost = round(net_cost - credits, 2)

            merged.append({
                label_key: name,
                "gross_cost": gross_cost,
                "credits": credits,
                "net_cost": net_cost,
                "currency": currency,
            })

        merged.sort(key=lambda item: abs(item["gross_cost"]), reverse=True)

        return merged

    @staticmethod
    def _compare_breakdown(period_a_items, period_b_items, label_key):
        """Union of every service/region AWS returned for EITHER period's
        merged (gross/credit/net) breakdown, comparing net_cost - the
        headline figure for period-over-period movers, consistent with
        the top-level change.net_cost_change convention. A name absent
        from one period's merged list is treated as 0 for that side -
        AWS reported no billing activity of any kind for it that
        period, not "unknown".

        Generic period_a/period_b naming since this one helper backs two
        different pairings: the top-level service_comparison/
        region_comparison (period_a=current_period, period_b=
        previous_period) and comparison.service_comparison/
        region_comparison (period_a=selected_period, period_b=
        comparison_period) - see build_context()/_build_comparison()."""

        a_map = {
            item.get(label_key): item.get("net_cost") or 0
            for item in (period_a_items or []) if item.get(label_key)
        }
        b_map = {
            item.get(label_key): item.get("net_cost") or 0
            for item in (period_b_items or []) if item.get(label_key)
        }

        names = sorted(set(a_map) | set(b_map))

        comparison = [
            {
                label_key: name,
                "period_a_cost": a_map.get(name, 0),
                "period_b_cost": b_map.get(name, 0),
                "difference": round(a_map.get(name, 0) - b_map.get(name, 0), 2),
                "percentage_change": CostContextBuilder._percent_change(
                    a_map.get(name, 0), b_map.get(name, 0)
                ),
            }
            for name in names
        ]

        comparison.sort(key=lambda item: abs(item["difference"]), reverse=True)

        return comparison

    # =====================================================
    # Per-period bundle - shared by current_period, previous_period, and
    # both sides of the optional Month/Period Comparison
    # =====================================================

    def _build_period(self, period_raw: Dict[str, Any]) -> Dict[str, Any]:

        credits = period_raw.get("credits") or {"total": None, "currency": None, "history": []}
        net_cost = period_raw.get("total_cost")

        return {

            "from": period_raw.get("from"),

            "to": period_raw.get("to"),

            "currency": period_raw.get("currency"),

            "gross_cost": self._derive_gross_cost(net_cost, credits),

            "credits": credits,

            "net_cost": net_cost,

            "daily_history": period_raw.get("daily_history") or [],

            "service_breakdown": self._merge_breakdown_with_credits(
                period_raw.get("service_breakdown"),
                period_raw.get("service_credit_breakdown"),
                "service",
            ),

            "region_breakdown": self._merge_breakdown_with_credits(
                period_raw.get("region_breakdown"),
                period_raw.get("region_credit_breakdown"),
                "region",
            ),

        }

    def _build_change(self, current_period: Dict[str, Any], previous_period: Dict[str, Any]) -> Dict[str, Any]:
        """current_period vs previous_period (the always-computed default
        lookback comparison, distinct from the optional user-selected
        Month/Period Comparison) - gross/credits/net, each with its own
        difference so the redesigned Cost Summary/Service/Region sections
        can show a real "Change" column without recomputing anything."""

        current_credits = (current_period.get("credits") or {}).get("total")
        previous_credits = (previous_period.get("credits") or {}).get("total")

        return {

            "gross_cost_change": self._difference(current_period.get("gross_cost"), previous_period.get("gross_cost")),

            "gross_cost_change_percent": self._percent_change(
                current_period.get("gross_cost"), previous_period.get("gross_cost")
            ),

            "credits_change": self._difference(current_credits, previous_credits),

            "net_cost_change": self._difference(current_period.get("net_cost"), previous_period.get("net_cost")),

            "net_cost_change_percent": self._percent_change(
                current_period.get("net_cost"), previous_period.get("net_cost")
            ),

        }

    _EMPTY_ANOMALIES = {"status": "unavailable", "reason": "No collector data", "anomalies": []}

    def _build_comparison(self, comparison_raw):
        """Builds the Month/Period Comparison block from the collector's
        raw period_a/period_b bundles, or returns None when no
        comparison was requested (raw.comparison is None/absent) -
        additive, honest "not run" state, never a fabricated comparison."""

        if not comparison_raw:
            return None

        selected_period = self._build_period(comparison_raw.get("period_a") or {})
        comparison_period = self._build_period(comparison_raw.get("period_b") or {})

        selected_credits = (selected_period.get("credits") or {}).get("total")
        comparison_credits = (comparison_period.get("credits") or {}).get("total")

        return {

            "selected_period": selected_period,

            "comparison_period": comparison_period,

            "difference": self._difference(selected_period.get("net_cost"), comparison_period.get("net_cost")),

            "percentage_change": self._percent_change(
                selected_period.get("net_cost"), comparison_period.get("net_cost")
            ),

            "service_comparison": self._compare_breakdown(
                selected_period.get("service_breakdown"), comparison_period.get("service_breakdown"), "service"
            ),

            "region_comparison": self._compare_breakdown(
                selected_period.get("region_breakdown"), comparison_period.get("region_breakdown"), "region"
            ),

            "credit_comparison": {

                "selected_period_credits": selected_credits,

                "comparison_period_credits": comparison_credits,

                "difference": self._difference(selected_credits, comparison_credits),

            },

            # GetAnomalies has its own supported-range constraint - a
            # comparison_period far in the past legitimately comes back
            # "unsupported_range"; that is a real AWS answer, passed
            # through as-is, never suppressed or replaced.
            "anomaly_comparison": {

                "selected_period": comparison_raw.get("period_a_anomalies") or dict(self._EMPTY_ANOMALIES),

                "comparison_period": comparison_raw.get("period_b_anomalies") or dict(self._EMPTY_ANOMALIES),

            },

        }

    def build_context(self, raw: Dict[str, Any]) -> Dict[str, Any]:

        current_period = self._build_period(raw.get("current_period") or {})
        previous_period = self._build_period(raw.get("previous_period") or {})

        return {

            "generated_by": "AI-SRE-Agent-CostExplorer",

            "currency": raw.get("currency"),

            "period": raw.get("period") or {},

            "current_period": current_period,

            "previous_period": previous_period,

            "change": self._build_change(current_period, previous_period),

            # current_period vs previous_period (the always-computed
            # default lookback comparison) - period_a=current_period,
            # period_b=previous_period. Distinct from
            # comparison.service_comparison/region_comparison below,
            # which is the optional user-selected Month/Period Comparison.
            "service_comparison": self._compare_breakdown(
                current_period.get("service_breakdown"), previous_period.get("service_breakdown"), "service"
            ),

            "region_comparison": self._compare_breakdown(
                current_period.get("region_breakdown"), previous_period.get("region_breakdown"), "region"
            ),

            "anomalies": raw.get("anomalies") or dict(self._EMPTY_ANOMALIES),

            "comparison": self._build_comparison(raw.get("comparison")),

        }

    def run(self) -> Dict[str, Any]:

        raw = self.load_raw()

        context = self.build_context(raw)

        path = self.context_directory / "cost_context.json"

        with open(path, "w", encoding="utf-8") as file:
            json.dump(context, file, indent=4)

        return context


if __name__ == "__main__":

    builder = CostContextBuilder()

    builder.run()

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
    The one derived value it does compute, change_percent, is arithmetic
    over two real AWS totals - never an invented or hardcoded number.
=========================================================
"""

import json
from pathlib import Path
from typing import Any, Dict


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

    @staticmethod
    def _percent_change(new_value, old_value):
        """(new - old) / old * 100, or None when either value is missing
        or old_value is exactly zero (division undefined). The single
        shared formula for both the top-level change_percent and the
        Month/Period Comparison feature's change_percent - one
        implementation, not two. Checks "is not None", never truthiness:
        old_value can legitimately be a real AWS-reported 0.0, which
        must still be distinguished from "missing"."""

        if new_value is None or old_value is None or old_value == 0:
            return None

        return round(((new_value - old_value) / old_value) * 100, 2)

    @staticmethod
    def _derive_gross_cost(total_cost, credits):
        """total_cost (UnblendedCost, unfiltered) already includes credit
        records - AWS has no way to exclude a record type from it - so
        it IS the net-of-credits figure. gross_cost is simply the
        arithmetic inverse: net minus the (negative) credits total
        recovers what the cost would have been before credits were
        applied - no separate AWS query, no invented number. Shared by
        the top-level context and each comparison period."""

        credits_total = (credits or {}).get("total")

        if total_cost is None or credits_total is None:
            return None

        return round(total_cost - credits_total, 2)

    @staticmethod
    def _compare_breakdown(period_a_items, period_b_items, label_key):
        """The union of every service/region AWS returned for EITHER
        period (never a hardcoded list) with each period's actual cost
        and the difference. service_breakdown/region_breakdown already
        omit zero-or-negative entries (see collector/cost_explorer.py's
        _grouped_cost), so a name absent from one period's list means
        AWS reported no positive cost for it that period - treated as 0
        for that side, not as "unknown", consistent with what the
        breakdown list itself already represents."""

        a_map = {
            item.get(label_key): item.get("cost") or 0
            for item in (period_a_items or []) if item.get(label_key)
        }
        b_map = {
            item.get(label_key): item.get("cost") or 0
            for item in (period_b_items or []) if item.get(label_key)
        }

        names = sorted(set(a_map) | set(b_map))

        comparison = [
            {
                label_key: name,
                "period_a_cost": a_map.get(name, 0),
                "period_b_cost": b_map.get(name, 0),
                "difference": round(a_map.get(name, 0) - b_map.get(name, 0), 2),
            }
            for name in names
        ]

        comparison.sort(key=lambda item: abs(item["difference"]), reverse=True)

        return comparison

    def _build_period(self, period_raw: Dict[str, Any]) -> Dict[str, Any]:
        """Adds the same derived gross_cost every top-level context gets
        to one comparison period's raw fetched bundle (collector's
        get_period_data()) - no new AWS call, same formula."""

        period = dict(period_raw)
        period["gross_cost"] = self._derive_gross_cost(period.get("total_cost"), period.get("credits"))
        return period

    def _build_comparison(self, comparison_raw):
        """Builds the Month/Period Comparison block from the collector's
        raw period_a/period_b bundles, or returns None when no
        comparison was requested (raw.comparison is None/absent) -
        additive, honest "not run" state, never a fabricated comparison."""

        if not comparison_raw:
            return None

        period_a = self._build_period(comparison_raw.get("period_a") or {})
        period_b = self._build_period(comparison_raw.get("period_b") or {})

        total_a = period_a.get("total_cost")
        total_b = period_b.get("total_cost")

        absolute_difference = None
        if total_a is not None and total_b is not None:
            absolute_difference = round(total_a - total_b, 2)

        return {

            "period_a": period_a,

            "period_b": period_b,

            "service_comparison": self._compare_breakdown(
                period_a.get("service_breakdown"), period_b.get("service_breakdown"), "service"
            ),

            "region_comparison": self._compare_breakdown(
                period_a.get("region_breakdown"), period_b.get("region_breakdown"), "region"
            ),

            "absolute_difference": absolute_difference,

            "change_percent": self._percent_change(total_a, total_b),

        }

    def build_context(self, raw: Dict[str, Any]) -> Dict[str, Any]:

        total_cost = raw.get("total_cost")
        previous_cost = raw.get("previous_cost")

        change_percent = self._percent_change(total_cost, previous_cost)

        credits = raw.get("credits") or {"total": None, "currency": None, "history": []}

        gross_cost = self._derive_gross_cost(total_cost, credits)

        return {

            "generated_by": "AI-SRE-Agent-CostExplorer",

            "currency": raw.get("currency"),

            "period": raw.get("period") or {},

            "total_cost": total_cost,

            "previous_cost": previous_cost,

            "change_percent": change_percent,

            "gross_cost": gross_cost,

            "credits": credits,

            "daily_history": raw.get("daily_history") or [],

            "service_breakdown": raw.get("service_breakdown") or [],

            "region_breakdown": raw.get("region_breakdown") or [],

            "anomalies": raw.get("anomalies") or {"status": "unavailable", "reason": "No collector data", "anomalies": []},

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

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

    def build_context(self, raw: Dict[str, Any]) -> Dict[str, Any]:

        total_cost = raw.get("total_cost")
        previous_cost = raw.get("previous_cost")

        # Arithmetic over two real AWS totals only - never computed when
        # either side is missing, and never a divide-by-zero guess when
        # the previous period genuinely had $0 of cost.
        change_percent = None
        if total_cost is not None and previous_cost:
            change_percent = round(((total_cost - previous_cost) / previous_cost) * 100, 2)

        return {

            "generated_by": "AI-SRE-Agent-CostExplorer",

            "currency": raw.get("currency"),

            "period": raw.get("period") or {},

            "total_cost": total_cost,

            "previous_cost": previous_cost,

            "change_percent": change_percent,

            "daily_history": raw.get("daily_history") or [],

            "service_breakdown": raw.get("service_breakdown") or [],

            "region_breakdown": raw.get("region_breakdown") or [],

            "anomalies": raw.get("anomalies") or {"status": "unavailable", "reason": "No collector data", "anomalies": []},

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

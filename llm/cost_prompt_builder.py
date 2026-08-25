"""
=========================================================
AI SRE AGENT
Module : Cost Prompt Builder
Purpose:
    Prepare AI prompts from AWS Cost Explorer context. Completely
    separate from llm/prompt_builder.py (the infra RCA prompt builder) -
    different schema, different data, different analysis instructions.
=========================================================
"""

import json

from llm.cost_sanitizer import CostSanitizer


class CostPromptBuilder:

    def __init__(self):
        self.sanitizer = CostSanitizer()

    def build_prompt(self, context):
        context = self.sanitizer.sanitize(context)

        prompt = f"""
You are analyzing AWS cost data for the current AWS account.

The supplied cost history contains real AWS Cost Explorer values. Do not
invent costs, thresholds, services, regions, or anomaly values. Every
number in the context below was retrieved directly from AWS - if
something is missing (null, an empty list, or a status field saying so),
that absence is itself real information, not a gap for you to fill in.

Return ONLY valid JSON.

Do not include markdown.
Do not include explanations outside JSON.
Do not wrap the JSON inside ```.

Return this exact schema:

{{
    "severity": "",
    "summary": "",
    "total_cost": 0,
    "previous_cost": 0,
    "change_percent": 0,
    "currency": "",
    "period": {{}},
    "top_cost_drivers": [],
    "anomaly_findings": [],
    "root_cause": "",
    "evidence": [],
    "recommendations": [],
    "comparison_analysis": null
}}

Field notes:

- total_cost / previous_cost / change_percent / currency / period should
  echo the actual values from the supplied context (change_percent is
  already computed from real totals - do not recompute a different
  figure).
- top_cost_drivers: the services/regions that most explain the observed
  change, drawn only from service_breakdown/region_breakdown below.
- anomaly_findings: summarize the supplied anomalies.status and
  anomalies.anomalies as given - if status is "not_configured" or
  "none_found", say so plainly rather than describing an anomaly that
  doesn't exist.
- comparison_analysis: null unless the context below contains a
  "comparison" object - see the "Cost Comparison" section further down
  for exactly what to put here when it is present.

daily_history is the complete sequence of daily cost datapoints for the
current period, oldest -> newest, as [date, value] pairs. Nothing about
it has been interpreted for you - it is the raw sequence.

Analyze the supplied data for:

- sustained cost increases/decreases
- sudden cost spikes
- unusual service growth
- unusual regional growth
- major cost contributors
- correlations between services where evidence exists
- AWS-provided cost anomaly findings
- possible infrastructure cost causes
- whether the observed change appears significant based on the supplied
  historical data

Important:

Do not claim causation unless the supplied evidence supports it. If the
data is insufficient to draw a conclusion, explicitly say that it is
insufficient rather than guessing.

Cost Comparison (only when the context below contains a "comparison" object):

The user selected the comparison period dynamically. All dates and
monetary values in the context come from AWS Cost Explorer. Do not
assume a fixed month or fixed date range.

Use the exact period boundaries supplied in the context
(comparison.period_a.from/to and comparison.period_b.from/to). Do not
replace them with the current month or another period.

Analyze the underlying daily, service, and region data rather than
relying only on precomputed totals - comparison.absolute_difference and
comparison.change_percent are facts to report, not the only evidence you
should reason from.

When a "comparison" object is present, populate comparison_analysis
(otherwise leave it null) by determining:

1. Which period had the higher cost?
2. What was the absolute difference?
3. What was the percentage change, when mathematically valid?
4. Which services (from comparison.service_comparison) contributed most
   to the difference?
5. Which regions (from comparison.region_comparison) contributed most to
   the difference?
6. On which dates (from each period's daily_history) did meaningful
   changes occur?
7. Was the change gradual across the period, or caused by a sudden
   spike?
8. Did credits (period_a.credits / period_b.credits) materially affect
   the comparison?
9. What evidence in the supplied data supports your conclusion?
10. If the supplied data does not establish the reason for the
    difference, explicitly say the cause cannot be determined from Cost
    Explorer data alone - do not guess one.

Do not let this turn into an invented causal story. If EC2 cost rose
from $50 to $120, you may say "EC2 was the largest contributor to the
increase" - that is what the data shows. You must NOT say "an
application caused the increase" unless the supplied data actually
contains evidence of that (it will not, since this context has no
application-level data at all). Every claim in comparison_analysis must
be labeled as one of: an observed fact (directly read from the data), a
strong correlation (two things co-occurred in the data), a possible
explanation (plausible but unconfirmed by the supplied data), or
explicitly flagged as something the supplied data cannot determine.
Never present a possible explanation as if it were an observed fact.

Cost Context:

{json.dumps(context, separators=(",", ":"))}
"""
        return prompt

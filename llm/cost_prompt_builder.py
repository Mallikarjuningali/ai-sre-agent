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

Gross Cost / Credits / Net Cost - read these terms precisely:

- gross_cost is what usage would have cost before any AWS credit was
  applied.
- credits is the AWS credit amount applied (reported by AWS as a
  negative-or-zero number - a credit reduces the bill).
- net_cost (= gross_cost + credits) is what the account was actually
  billed after credits - this is the figure to use for total_cost /
  previous_cost / change_percent below.
- If net_cost is 0 while gross_cost is greater than 0, say explicitly
  that credits fully offset the reported usage for that period - this is
  a real, normal outcome, never describe it as an error or as "no
  usage."

Field notes:

- total_cost = context.current_period.net_cost, previous_cost =
  context.previous_period.net_cost, change_percent =
  context.change.net_cost_change_percent, currency =
  context.currency, period = context.period - echo these actual values,
  do not recompute a different figure.
- top_cost_drivers: MUST be a JSON array of plain SERVICE OR REGION NAME
  STRINGS ONLY - for example ["Amazon EC2", "Amazon Virtual Private Cloud"].
  Never an object, never a nested structure, never the gross_cost/
  credits/net_cost figures themselves - the dashboard already has those
  real numbers from context.current_period.service_breakdown /
  region_breakdown and will look each one up by the exact name you give
  here. Pick the names that most explain the observed change (a service
  with net_cost 0 and gross_cost > 0 was still real usage fully offset
  by a credit and may still belong here even though it added nothing to
  the bill).
- anomaly_findings: summarize context.anomalies.status and
  context.anomalies.anomalies as given - if status is "not_configured"
  or "none_found", say so plainly rather than describing an anomaly that
  doesn't exist. When an anomaly is present, its fields (service, region,
  start_date, end_date, total_impact, total_actual_spend,
  total_expected_spend, anomaly_score) are all real AWS values - use only
  the fields actually present; a missing field (e.g. no region on a
  particular anomaly) means AWS did not attribute one, not that you
  should infer one.
- comparison_analysis: null unless the context below contains a
  "comparison" object. When "comparison" IS present, comparison_analysis
  is REQUIRED (never null) and MUST be an object with exactly this shape:

  {{
      "explanation": "",
      "root_cause": "",
      "evidence": [],
      "recommendations": [],
      "service_drivers": [],
      "region_drivers": [],
      "credits_impact": "",
      "anomalies_summary": ""
  }}

  See the "Cost Comparison" section further down for what belongs in
  each of these fields.

Credit attribution - critical constraint:

context.current_period.service_breakdown / region_breakdown already
reflect AWS's own service-level and region-level credit attribution
(AWS Cost Explorer can reliably tell you which service or region a
credit applied to). AWS Cost Explorer does NOT provide resource-level
credit attribution (e.g. which specific EC2 instance a credit applied
to) - this data was never fetched because AWS does not reliably support
it. You must NEVER claim, imply, or guess that a credit applied to a
specific resource, instance ID, or anything more granular than the
service/region level actually present in the supplied data. If asked
about resource-level credit attribution, or if you would otherwise be
tempted to name a specific resource as receiving a credit, explicitly
state that AWS Cost Explorer reports credits at the service/region
level and that resource-level attribution is not available from this
data.

context.current_period.daily_history is the complete sequence of daily
cost datapoints for the current period, oldest -> newest, as [date,
value] pairs. Nothing about it has been interpreted for you - it is the
raw sequence.

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
(comparison.selected_period.from/to and comparison.comparison_period.from/to).
Do not replace them with the current month or another period.

Analyze the underlying daily, service, and region data rather than
relying only on precomputed totals - comparison.difference and
comparison.percentage_change are facts to report, not the only evidence
you should reason from.

When a "comparison" object is present, comparison_analysis is REQUIRED
(see the exact shape in the field notes above) and every one of its
fields must be filled in - never leave it null, and never leave
"explanation" empty, when a comparison object exists. If you genuinely
cannot determine a cause from the supplied data, "explanation" must still
contain that honest conclusion as prose (e.g. "The supplied data does not
establish a clear cause for this change.") - an empty/missing
explanation when a comparison was requested is always wrong; a filled-in
explanation that says "the cause is undetermined" is correct and exactly
what is wanted in that case.

Populate comparison_analysis's fields using this reasoning, and answer
in prose inside "explanation" (not as a separate list of numbered
answers):

1. Which period had the higher net cost, and what was the absolute
   difference (comparison.difference) and, when mathematically valid,
   the percentage change (comparison.percentage_change)?
2. Which services (from comparison.service_comparison) contributed most
   to the difference? List their plain names (nothing else) in
   service_drivers - same string-only rule as top_cost_drivers above.
3. Which regions (from comparison.region_comparison) contributed most to
   the difference? List their plain names in region_drivers, same rule.
4. On which dates (from each period's daily_history, inside
   comparison.selected_period / comparison.comparison_period) did
   meaningful changes occur?
5. Was the change gradual across the period, or caused by a sudden
   spike?
6. Did credits (comparison.credit_comparison, and each period's own
   gross_cost vs net_cost) materially affect the comparison? If gross
   cost was similar between periods but net cost differs mainly because
   of a credit, say so explicitly in credits_impact rather than
   attributing the difference to usage.
7. Were any cost anomalies detected in either period
   (comparison.anomaly_comparison.selected_period /
   .comparison_period)? Summarize in anomalies_summary - if neither
   period had a "found" status, say so plainly rather than describing an
   anomaly that doesn't exist.
8. What evidence in the supplied data supports your conclusion? List it
   in evidence, and put your recommended actions (if any are actually
   supported by the data) in recommendations.
9. root_cause should be a short, direct statement of the cause (or of
   "undetermined" if the data doesn't establish one) - explanation is
   the fuller prose version of the same conclusion.

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

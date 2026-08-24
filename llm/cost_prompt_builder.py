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
    "recommendations": []
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

Cost Context:

{json.dumps(context, separators=(",", ":"))}
"""
        return prompt

"""
=========================================================
AI SRE AGENT

Module:
Prompt Builder

Purpose:
Prepare AI prompts from infrastructure context.
=========================================================
"""

import json
from pathlib import Path
from llm.sanitizer import Sanitizer


class PromptBuilder:

    def __init__(self):
        self.sanitizer = Sanitizer()

        self.project_root = Path(__file__).resolve().parent.parent

        self.context_directory = (
            self.project_root /
            "output" /
            "context"
        )

    def load_context(self, filename):

        file_path = self.context_directory / filename

        with open(
            file_path,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    def build_prompt(self, context):
        context = self.sanitizer.sanitize(context)
        prompt = f"""
You are an AWS Principal Site Reliability Engineer.

Analyze the following infrastructure.

Return ONLY valid JSON.

Do not include markdown.
Do not include explanations outside JSON.
Do not wrap the JSON inside ```.

Return this exact schema:

{{
    "severity": "",
    "confidence": 0,
    "root_cause": "",
    "summary": "",
    "evidence": [],
    "recommendations": []
}}

Every metric below (cpu, memory, disk, network_in, network_out, status_check,
request_count, target_response_time, http_4xx, http_5xx, healthy_hosts,
unhealthy_hosts, desired_capacity, in_service_instances, pending_instances)
is encoded in this compact format (illustrative shape only, not real
infrastructure data):

{{
    "U": "<unit>",
    "TH": {{ "V": "<threshold value>", "OP": "<GT|GTE|LT|LTE|EQ>" }},
    "H": [ ["<HH:MM>", "<value>"], ["<HH:MM>", "<value>"] ]
}}

U is the metric's unit (e.g. "%", "Bytes", "Count", "Seconds").

TH is OPTIONAL - present only when this exact metric, for this exact
resource, has a matching configured CloudWatch Alarm. When present, TH.V
is that alarm's actual configured threshold value and TH.OP is its actual
comparison operator (GT/GTE/LT/LTE/EQ) - both retrieved from AWS, never
invented or defaulted by the backend. A metric with no TH simply has no
CloudWatch Alarm configured for it right now - that absence is itself
meaningful and should not be treated as "no threshold exists" in any
permanent sense, only "none is configured today."

H is the complete history of samples for the previous one hour, sampled
every five minutes (up to 12 points; fewer if the metric hasn't existed for
a full hour - never padded). Each entry is [timestamp, value], where
timestamp is HH:MM and index 0 is the oldest sample, index 1 (the last
entry) is the newest. Nothing about H has been interpreted for you - it is
the raw sequence.

Analyze the complete historical sequence in H for every metric, not only
the most recent sample. Determine for each: sustained increases, sustained
decreases, transient spikes that already recovered, oscillation between
high and low values, general instability, gradual degradation, recovery
patterns, repeated crossings of TH.V, and periods of persistent violation
of TH (using TH.OP to know which direction constitutes a breach). Flag any
sample that stands out sharply against the rest of its own H as possibly
abnormal.

Do not treat metrics independently. Correlate historical behavior across
all available metrics - CPU, memory, disk, network, ALB metrics, and Auto
Scaling metrics - looking for sudden changes that line up in time across
more than one metric. Then correlate that behavior with the CloudTrail
events, Auto Scaling activities, Load Balancer health, and infrastructure
state elsewhere in this context. Reason across the complete timeline before
concluding a root cause, and base your conclusions on evidence from the
historical sequences, not solely on the final sample of any one metric.

Infrastructure Context:

{json.dumps(context, separators=(",", ":"))}
"""
        return prompt

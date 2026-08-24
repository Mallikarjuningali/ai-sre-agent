"""
=========================================================
AI SRE AGENT
Module : Cost Sanitizer
Purpose:
    Strip sensitive AWS account information from cost context before it
    reaches Gemini. Completely separate from llm/sanitizer.py (the infra
    sanitizer), which strips a different set of fields (DNS names, VPC
    IDs, private IPs) that don't apply to Cost Explorer data.

    Cost Explorer's own response shapes don't carry DNS/VPC/IP data at
    all - the fields that matter here are AWS account IDs and ARNs,
    which could appear if anomaly/monitor data or a future
    LINKED_ACCOUNT grouping is added. Everything else (dates, dollar
    amounts, service names, region names, currency, anomaly
    status/impact) is operationally necessary for Gemini's analysis and
    is left untouched.
=========================================================
"""

import re
from copy import deepcopy

# Matches a bare 12-digit AWS account ID (as a whole token, not a
# substring of a larger number) and any AWS ARN.
_ACCOUNT_ID_RE = re.compile(r"^\d{12}$")
_ARN_RE = re.compile(r"^arn:aws:")

_SENSITIVE_KEYS = {"account_id", "accountid", "linked_account", "monitor_arn", "arn"}


class CostSanitizer:

    def sanitize(self, context: dict) -> dict:

        return self._scrub(deepcopy(context))

    def _scrub(self, value):

        if isinstance(value, dict):
            return {
                key: self._scrub(val)
                for key, val in value.items()
                if key.lower().replace(" ", "_") not in _SENSITIVE_KEYS
            }

        if isinstance(value, list):
            return [self._scrub(item) for item in value]

        if isinstance(value, str):
            if _ACCOUNT_ID_RE.match(value) or _ARN_RE.match(value):
                return "[redacted]"
            return value

        return value

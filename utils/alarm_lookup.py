"""
=========================================================
AI SRE AGENT
Module : Alarm Lookup
Purpose:
    Single source of truth for CloudWatch alarm discovery and matching.

    Fetches every CloudWatch alarm in the account ONCE (via
    describe_alarms(), paginated) and builds an in-memory index keyed by
    the exact (Namespace, MetricName, Dimensions) triple - the same triple
    every collector already uses to fetch a metric's datapoints. A
    threshold is only ever returned when a real alarm matches that exact
    triple; there is no fallback, default, or invented value anywhere in
    this module. Absence of a match means the metric has no configured
    alarm - that is valid information, not an error.

    One AlarmLookup instance is built once per collector's own main() run
    (collector/cloudwatch.py, collector/alb.py, collector/autoscaling.py
    each build their own) and reused for every metric that collector
    fetches in that run - never one describe_alarms() call per metric.
=========================================================
"""

from utils.logger import get_logger

logger = get_logger("AlarmLookup")

# AWS's ComparisonOperator values that map cleanly onto the compact
# telemetry format's OP vocabulary (GT/GTE/LT/LTE/EQ). AWS has no
# "equal to" alarm operator for standard threshold alarms, so EQ is never
# actually produced here - it stays part of the vocabulary for
# completeness/future use, not because this map ever emits it.
COMPARISON_OPERATOR_MAP = {
    "GreaterThanThreshold": "GT",
    "GreaterThanOrEqualToThreshold": "GTE",
    "LessThanThreshold": "LT",
    "LessThanOrEqualToThreshold": "LTE",
}

# Anomaly-detection-band alarms (LessThanLowerOrGreaterThanUpperThreshold,
# LessThanLowerThreshold, GreaterThanUpperThreshold) don't have a single
# scalar Threshold - they compare against a computed band via
# ThresholdMetricId instead. There is no safe GT/GTE/LT/LTE/EQ
# representation for that, so alarms using them are indexed with
# comparison_operator=None and never surface a TH (same as "no alarm").


class AlarmLookup:

    def __init__(self, cloudwatch_client):

        self.alarms = []
        self._index = {}

        self._load(cloudwatch_client)

    # -----------------------------------------------------
    # Loading - one describe_alarms() call (paginated), ever
    # -----------------------------------------------------

    def _load(self, cloudwatch_client):

        try:

            paginator = cloudwatch_client.get_paginator("describe_alarms")

            for page in paginator.paginate(AlarmTypes=["MetricAlarm"]):

                for raw_alarm in page.get("MetricAlarms", []):

                    parsed = self._parse(raw_alarm)

                    if parsed is None:
                        continue

                    self.alarms.append(parsed)

                    key = self._key(parsed["namespace"], parsed["metric_name"], parsed["dimensions"])

                    self._index[key] = parsed

            logger.info(f"Loaded {len(self.alarms)} CloudWatch metric alarm(s).")

        except Exception as exc:

            logger.error(f"Failed to load CloudWatch alarms: {exc}")

    def _parse(self, raw_alarm):

        namespace = raw_alarm.get("Namespace")
        metric_name = raw_alarm.get("MetricName")

        # Composite/math-expression alarms have no single Namespace/
        # MetricName of their own - they can't be matched to one metric.
        if not namespace or not metric_name:
            return None

        raw_operator = raw_alarm.get("ComparisonOperator")
        operator = COMPARISON_OPERATOR_MAP.get(raw_operator)

        if operator is None and raw_operator is not None:
            logger.warning(
                f"Alarm '{raw_alarm.get('AlarmName')}' uses ComparisonOperator "
                f"'{raw_operator}', which has no safe GT/GTE/LT/LTE mapping - "
                f"its threshold will be omitted, not guessed."
            )

        dimensions = [
            {"Name": dim["Name"], "Value": dim["Value"]}
            for dim in raw_alarm.get("Dimensions", [])
        ]

        return {
            "alarm_name": raw_alarm.get("AlarmName"),
            "namespace": namespace,
            "metric_name": metric_name,
            "dimensions": dimensions,
            "threshold": raw_alarm.get("Threshold"),
            "comparison_operator_raw": raw_operator,
            "comparison_operator": operator,
            "state_value": raw_alarm.get("StateValue"),
            "state_reason": raw_alarm.get("StateReason"),
        }

    # -----------------------------------------------------
    # Matching key - Namespace + MetricName + Dimensions (order-independent),
    # never MetricName alone, so two resources with alarms on the same
    # metric never collide.
    # -----------------------------------------------------

    @staticmethod
    def _dimension_key(dimensions):
        return frozenset((dim["Name"], dim["Value"]) for dim in (dimensions or []))

    def _key(self, namespace, metric_name, dimensions):
        return (namespace, metric_name, self._dimension_key(dimensions))

    # -----------------------------------------------------
    # Public API
    # -----------------------------------------------------

    def find_threshold(self, namespace, metric_name, dimensions):
        """
        Returns {"V": <actual configured threshold>, "OP": <GT|GTE|LT|LTE>}
        for the alarm matching this exact namespace + metric_name +
        dimensions, or None if there is no matching alarm, or its
        threshold/operator can't be safely determined. Never fabricates a
        value - the caller must treat None as "omit TH entirely".
        """

        alarm = self._index.get(self._key(namespace, metric_name, dimensions))

        if alarm is None:
            return None

        if alarm["threshold"] is None or alarm["comparison_operator"] is None:
            return None

        return {"V": alarm["threshold"], "OP": alarm["comparison_operator"]}

    def list_alarms(self):
        """Legacy summary shape - backs collector/cloudwatch.py's
        get_cloudwatch_alarms(), which used to make its own independent
        describe_alarms() call. Now backed by the single fetch this class
        already did - one source of truth for alarm discovery."""

        return [
            {
                "AlarmName": alarm["alarm_name"],
                "Metric": alarm["metric_name"],
                "Namespace": alarm["namespace"],
                "State": alarm["state_value"],
                "Reason": alarm["state_reason"],
            }
            for alarm in self.alarms
        ]

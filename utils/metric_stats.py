"""
=========================================================
AI SRE AGENT
Module : Metric Statistics
Purpose:
    Format a window of raw CloudWatch datapoints into the compact
    telemetry payload Gemini reasons over:

        {"U": "%", "TH": {"V": 80, "OP": "GT"}, "H": [["13:00", 18], ...]}

    No computation happens here or anywhere else in this codebase - not
    latest/minimum/maximum/average, not trend, not anomaly, not breach
    duration. Unit (U) and threshold (TH) are facts the caller already
    knows about the metric (what it measures, what its configured
    threshold is) - never derived from the datapoints. H is the complete
    ordered history, unmodified beyond formatting; Gemini performs every
    interpretation over it.

    Purely a formatting function - holds no state and writes nothing to
    disk. The datapoints handed to build_metric_payload() exist only for
    the duration of one collector call, exactly like everything else in
    output/raw/ - only the payload this module returns is ever persisted.
=========================================================
"""

# =========================================================
# Public API
# =========================================================


def build_metric_payload(datapoints, unit, threshold_value=None, threshold_operator=None, round_digits=2):
    """
    datapoints : list of {"timestamp": datetime, "value": float}, any order.
    unit : the metric's unit ("%", "Bytes", "Count", "Seconds", ...) - a
        fact about the metric, not computed.
    threshold_value / threshold_operator : the metric's configured
        threshold, if it has one. threshold_operator is one of
        "GT" | "GTE" | "LT" | "LTE" | "EQ". Also a fact, not computed
        from the data - "TH" is omitted from the payload entirely when
        the metric has no meaningful fixed threshold (e.g. NetworkIn).

    Returns {"U", "TH"?, "H"} - H is every datapoint in the window,
    ordered oldest -> newest, each as [HH:MM, value], never padded when
    fewer than a full window exist.

    Returns None if datapoints is empty - callers should treat that the
    same as "metric unavailable" (e.g. CloudWatch Agent not installed),
    per "continue gracefully, include only what's available".
    """

    if not datapoints:
        return None

    ordered = sorted(datapoints, key=lambda point: point["timestamp"])

    payload = {"U": unit}

    if threshold_value is not None:
        payload["TH"] = {"V": threshold_value, "OP": threshold_operator}

    payload["H"] = [
        [point["timestamp"].strftime("%H:%M"), round(point["value"], round_digits)]
        for point in ordered
    ]

    return payload

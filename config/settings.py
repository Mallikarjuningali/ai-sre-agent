"""
=========================================================
AI SRE AGENT
Module : Settings
Author : Mallikarjun
Purpose:
    Store project configuration.
=========================================================
"""

# AWS Region

REGION = "us-east-1"

# CloudWatch Time Window

METRIC_LOOKBACK_MINUTES = 10

# Trend window for the CPU/Memory/Disk/Network/ALB/ASG metric statistics
# fed to Gemini (see utils/metric_stats.py) - deliberately separate from
# METRIC_LOOKBACK_MINUTES above, which CloudTrail's event lookback also
# depends on and which must not change.

METRIC_TREND_LOOKBACK_MINUTES = 60

METRIC_TREND_PERIOD_SECONDS = 300
# =========================================================
# Gemini AI Configuration
# =========================================================

GEMINI_MODEL = "gemini-3.5-flash-lite"

MAX_RETRIES = 4

INITIAL_RETRY_DELAY = 5

REQUEST_DELAY = 3

MAX_RUN_HISTORY = 5

# =========================================================
# Cost Explorer Configuration
# =========================================================
# Number of days of daily cost history to fetch, and the width of the
# "current period" window compared against an equal-length "previous
# period" immediately before it - a single knob so the collector never
# hardcodes a lookback.

COST_LOOKBACK_DAYS = 14


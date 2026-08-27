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

# Bounds a single generate_content() call - without this, a hung/slow
# Gemini request blocks the calling thread indefinitely (confirmed: no
# timeout existed anywhere in this codebase before). A bounded timeout
# turns a hang into a raised exception, which the existing MAX_RETRIES/
# backoff loop in analyzer.py already knows how to handle.
GEMINI_REQUEST_TIMEOUT_SECONDS = 60

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

# =========================================================
# Follow-Up Question Configuration
# =========================================================
# Total conversation turns kept on disk per investigation (oldest trimmed
# first) - a running log, not an unbounded one.
FOLLOW_UP_MAX_CONVERSATION_MESSAGES = 40

# Of that stored history, how many of the most recent turns are actually
# sent to Gemini on each new question - keeps prompt size (and cost)
# bounded independently of how long the conversation has gotten.
FOLLOW_UP_PROMPT_HISTORY_MESSAGES = 6

# Reject a follow-up question longer than this rather than silently
# truncating it (truncation could change what's actually being asked).
FOLLOW_UP_MAX_QUESTION_LENGTH = 2000

# Maximum CloudTrail events included in the deterministic timeline built
# for a single follow-up prompt - the investigation's own context file
# may hold more; this bounds what gets sent to Gemini, not what was
# collected.
FOLLOW_UP_TIMELINE_MAX_EVENTS = 20


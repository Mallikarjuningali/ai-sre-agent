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

# AWS Cost Anomaly Detection's GetAnomalies operation only returns results
# within a rolling window ending "today" and starting this many days
# before it - AWS rejects any DateInterval.StartDate earlier than that
# floor with a ValidationException (e.g. "Earliest supported detectionDate
# for GetRecentAnomalies is 2026-06-03"). AWS exposes no discovery API for
# this boundary, so it is derived here as a rolling window rather than a
# fixed calendar date (a fixed date would silently go stale as time
# passes) - see collector/cost_explorer.py::get_anomalies(), which also
# self-corrects from AWS's own error text if this value ever drifts from
# AWS's real constraint.

COST_ANOMALY_MAX_LOOKBACK_DAYS = 90

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

# =========================================================
# Log Investigation Configuration
# =========================================================
# Optional, explicitly user-triggered log enrichment stage for EC2/ALB/ASG
# investigations (see api/log_investigation_manager.py). Never runs as
# part of a normal Full/Single Resource Investigation - no constant below
# is ever read by collector/cloudwatch.py, collector/alb.py,
# collector/autoscaling.py, collector/cloudtrail.py, or
# api/investigation_manager.py.

# Buffer added before/after the derived incident window (see
# utils/incident_window.py) when actually querying a log source -
# configurable so the window can never silently balloon to a hardcoded
# large range like 24 hours.
LOG_INCIDENT_WINDOW_BEFORE_MINUTES = 10
LOG_INCIDENT_WINDOW_AFTER_MINUTES = 10

# When no metric in the existing context has both a configured threshold
# (TH) and a point that actually breaches it, there is no confident
# incident window to derive - this small, clearly-labeled fallback window
# (centered on "now") is used instead, and the evidence package/prompt
# both carry window_confidence="inferred" so Gemini and the dashboard
# never treat it as a precise window.
LOG_FALLBACK_WINDOW_MINUTES = 30

# EC2 log-group discovery (see collector/logs.py::discover_ec2_log_source)
# pages through describe_log_groups looking for a log stream named after
# the instance ID (CloudWatch Agent's documented default log_stream_name
# template) - bounded so an account with many unrelated log groups can't
# turn a single investigation into an unbounded account-wide scan.
LOG_MAX_GROUPS_SCANNED = 20

# ALB access logs are S3-delivered (see
# collector/logs.py::fetch_alb_access_logs) - bounds how many S3 objects
# (one per delivery interval) are listed/downloaded for a single bounded
# incident window, so a long-running high-traffic ALB can't turn one
# investigation into an unbounded S3 scan.
LOG_MAX_S3_OBJECTS_SCANNED = 50

# An ASG's own scaling activities are always fetched, but its member
# instances' own CloudWatch Logs (if any) are also worth attempting - this
# bounds how many member instances get that per-instance EC2-style
# discovery attempt, so a very large ASG can't turn one investigation
# into a discovery call per instance with no ceiling.
LOG_MAX_ASG_MEMBER_INSTANCES_SCANNED = 5

# Hard cap on raw log/event lines actually pulled from AWS for a single
# Log Investigation, before any local filtering - protects against a
# noisy log stream returning far more data than one investigation should
# ever need to read.
LOG_MAX_RAW_EVENTS = 5000

# Of those raw events, how many can survive the relevance filter before
# local processing (normalization/dedup/aggregation) itself gets bounded -
# a second, independent cap so a relevance filter that matches too broadly
# still can't blow up downstream processing.
LOG_MAX_RELEVANT_EVENTS = 500

# Maximum distinct normalized patterns kept in the Log Evidence Package -
# a real incident rarely has more than a handful of genuinely distinct
# failure signatures; this stops runaway pattern explosion from a
# normalizer that under-matches on a particular log format.
LOG_MAX_PATTERNS = 25

# Representative example lines kept per pattern (first few + a few middle
# + last few, never the full occurrence count) - see
# context/log_evidence_builder.py::deduplicate_and_aggregate.
LOG_MAX_EXAMPLES_PER_PATTERN = 5

# Small bounded global sample of representative events, independent of
# the per-pattern examples above - gives Gemini a handful of raw-shaped
# examples to ground its answer without ever approaching "send everything".
LOG_MAX_REPRESENTATIVE_EVENTS = 15

# Maximum chronological entries in the constructed timeline - mirrors
# FOLLOW_UP_TIMELINE_MAX_EVENTS's bounding rationale for a different
# evidence source.
LOG_MAX_TIMELINE_EVENTS = 20

# Final hard cap on the serialized Log Evidence Package's size in bytes,
# checked right before sanitization/prompting - a last safety net after
# every other bound above, in case an unexpected shape still produced a
# package too large to reasonably hand to Gemini. Exceeding it truncates
# patterns further (dropping the lowest-count ones first) and records the
# truncation in the package's own "limitations" list - it never silently
# drops the cap and sends an oversized package anyway.
LOG_MAX_EVIDENCE_PACKAGE_BYTES = 50000


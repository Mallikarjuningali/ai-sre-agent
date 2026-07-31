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
# =========================================================
# Gemini AI Configuration
# =========================================================

GEMINI_MODEL = "gemini-3.5-flash-lite"

MAX_RETRIES = 4

INITIAL_RETRY_DELAY = 5

REQUEST_DELAY = 3

MAX_RUN_HISTORY = 5


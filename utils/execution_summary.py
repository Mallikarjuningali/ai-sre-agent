"""
=========================================================
AI SRE AGENT
Module : Execution Summary
Author : Mallikarjun

Purpose:
    Generates execution summary for dashboard and history.
=========================================================
"""

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from config.settings import GEMINI_MODEL

SUMMARY_DIR = "output/summary"

os.makedirs(SUMMARY_DIR, exist_ok=True)


class ExecutionSummary:

    def __init__(self, run_id=None):

        self.run_id = run_id

        self.start_time = datetime.now(
            ZoneInfo("Asia/Kolkata")
        )

        self.end_time = None

        self.instances_discovered = 0
        self.instances_analyzed = 0

        self.successful = 0
        self.failed = 0

        self.retries = 0

        self.reports_generated = 0

        self.archive_created = False

        self.status = "SUCCESS"

        self.collectors = {
            "cloudwatch": "SUCCESS",
            "alb": "SUCCESS",
            "autoscaling": "SUCCESS",
            "cloudtrail": "SUCCESS"
        }

    def save(self):

        self.end_time = datetime.now(
            ZoneInfo("Asia/Kolkata")
        )

        execution_time = round(
            (self.end_time - self.start_time).total_seconds(),
            2
        )

        if self.failed > 0 and self.successful > 0:
            self.status = "PARTIAL_SUCCESS"

        elif self.failed == self.instances_discovered:
            self.status = "FAILED"

        else:
            self.status = "SUCCESS"

        timestamp = self.run_id or self.start_time.strftime(
            "%d-%m-%Y_%H-%M-%S"
        )

        summary = {

            "run_id": timestamp,

            "status": self.status,

            "start_time":
                self.start_time.strftime(
                    "%d-%m-%Y %H:%M:%S IST"
                ),

            "end_time":
                self.end_time.strftime(
                    "%d-%m-%Y %H:%M:%S IST"
                ),

            "execution_time_seconds":
                execution_time,

            "instances_discovered":
                self.instances_discovered,

            "instances_analyzed":
                self.instances_analyzed,

            "reports_generated":
                self.reports_generated,

            "successful":
                self.successful,

            "failed":
                self.failed,

            "retries":
                self.retries,

            "llm": {

                "provider": "Google Gemini",

                "model": GEMINI_MODEL

            },

            "collectors": self.collectors,

            "archive_created": self.archive_created

        }

        file_path = os.path.join(
            SUMMARY_DIR,
            f"{timestamp}.json"
        )

        with open(file_path, "w") as file:

            json.dump(
                summary,
                file,
                indent=4
            )

        return file_path

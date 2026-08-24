"""
=========================================================
AI SRE AGENT
Module : Cost Report Writer
Purpose:
    Persist the Gemini cost-analysis report. Mirrors
    analyzer/report_writer.py's shape exactly, but writes to a
    completely separate location - a single account-level report, not
    one file per resource.
=========================================================
"""

import json
from pathlib import Path


class CostReportWriter:

    def __init__(self):

        self.output_dir = Path("output/cost/reports")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save(self, report):

        file_path = self.output_dir / "cost_report.json"

        with open(file_path, "w") as f:
            json.dump(report, f, indent=4)

        return file_path

"""
=========================================================
AI SRE AGENT
Module : Cost Analyzer
Purpose:
    Orchestrate the Cost Explorer context -> sanitize -> prompt ->
    Gemini -> report pipeline. Completely separate from
    analyzer/analyzer.py (the infra Analyzer) - single account-level
    scope, no per-resource eligibility/retry loop needed.
=========================================================
"""

import json

from context.cost_context_builder import CostContextBuilder
from llm.cost_prompt_builder import CostPromptBuilder
from llm.llm_engine import LLMEngine
from analyzer.cost_report_writer import CostReportWriter
from utils.logger import get_logger

logger = get_logger("CostAnalyzer")


class CostAnalyzer:

    def __init__(self):

        self.builder = CostContextBuilder()
        self.prompt = CostPromptBuilder()
        self.llm = LLMEngine()
        self.report = CostReportWriter()

    def run(self) -> dict:

        logger.info("Building cost context...")

        context = self.builder.run()

        prompt = self.prompt.build_prompt(context)

        logger.info("Requesting Gemini cost analysis...")

        response = self.llm.analyze(prompt)

        try:
            report = json.loads(response)
        except Exception:
            report = {"raw_response": response}

        self.report.save(report)

        logger.info("Cost analysis complete.")

        return report

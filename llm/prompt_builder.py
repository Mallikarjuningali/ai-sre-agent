"""
=========================================================
AI SRE AGENT

Module:
Prompt Builder

Purpose:
Prepare AI prompts from infrastructure context.
=========================================================
"""

import json
from pathlib import Path
from llm.sanitizer import Sanitizer


class PromptBuilder:

    def __init__(self):
        self.sanitizer = Sanitizer()

        self.project_root = Path(__file__).resolve().parent.parent

        self.context_directory = (
            self.project_root /
            "output" /
            "context"
        )

    def load_context(self, filename):

        file_path = self.context_directory / filename

        with open(
            file_path,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    def build_prompt(self, context):
        context = self.sanitizer.sanitize(context)
        prompt = f"""
You are an AWS Principal Site Reliability Engineer.

Analyze the following infrastructure.

Return ONLY valid JSON.

Do not include markdown.
Do not include explanations outside JSON.
Do not wrap the JSON inside ```.

Return this exact schema:

{{
    "severity": "",
    "confidence": 0,
    "root_cause": "",
    "summary": "",
    "evidence": [],
    "recommendations": []
}}

Infrastructure Context:

{json.dumps(context, indent=4)}
"""
        return prompt

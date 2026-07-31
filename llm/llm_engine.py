"""
=========================================================
AI SRE AGENT

LLM Engine
=========================================================
"""

import os

from google import genai

from config.settings import GEMINI_MODEL


class LLMEngine:

    def __init__(self):

        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not found.")

        self.client = genai.Client(api_key=api_key)

    def analyze(self, prompt):

        response = self.client.models.generate_content(

            model=GEMINI_MODEL,

            contents=prompt

        )

        return response.text

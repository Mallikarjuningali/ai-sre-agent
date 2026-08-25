"""
=========================================================
AI SRE AGENT

LLM Engine
=========================================================
"""

import os

from google import genai
from google.genai import types

from config.settings import GEMINI_MODEL, GEMINI_REQUEST_TIMEOUT_SECONDS


class LLMEngine:

    def __init__(self):

        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not found.")

        self.client = genai.Client(api_key=api_key)

    def analyze(self, prompt):

        # http_options.timeout is milliseconds (google-genai 1.47.0's own
        # field definition) - bounds this one call so a hung/slow Gemini
        # request raises instead of blocking the caller forever. The
        # raised exception (httpx.ConnectTimeout/ReadTimeout, both plain
        # Exception subclasses) flows into whatever try/except already
        # wraps analyze() - analyzer.py's existing MAX_RETRIES/backoff
        # loop for Full Investigation, and the ordinary try/except around
        # Analyzer.run() for Single Resource - unchanged, no new retry
        # logic added here.
        response = self.client.models.generate_content(

            model=GEMINI_MODEL,

            contents=prompt,

            config=types.GenerateContentConfig(
                http_options=types.HttpOptions(
                    timeout=GEMINI_REQUEST_TIMEOUT_SECONDS * 1000
                )
            ),

        )

        return response.text

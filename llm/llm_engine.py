"""
=========================================================
AI SRE AGENT

LLM Engine

Single shared Gemini client + two call shapes:
  analyze(prompt)                              - stateless, whole prompt in
                                                 one string (unchanged for
                                                 every existing caller).
  analyze_with_cache(contents, cached_content) - issue #5. Sends the small
                                                 per-question contents and
                                                 references a server-side
                                                 cached_contents block
                                                 (paid tier) instead of
                                                 re-sending the full
                                                 evidence every call.
=========================================================
"""

import os
import time

from google import genai
from google.genai import types

from config.settings import GEMINI_MODEL, GEMINI_REQUEST_TIMEOUT_SECONDS
from utils.logger import get_logger

logger = get_logger("LLMEngine")


class LLMEngine:

    def __init__(self):

        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not found.")

        self.client = genai.Client(api_key=api_key)

    @staticmethod
    def _config(cached_content=None) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            http_options=types.HttpOptions(
                timeout=GEMINI_REQUEST_TIMEOUT_SECONDS * 1000
            ),
            cached_content=cached_content,
        )

    @staticmethod
    def _log(call_shape: str, started: float, response, cached_content) -> None:
        """Every Gemini call is logged with latency + token usage (issue #5
        / #13: llm_engine previously logged nothing, so neither spend nor
        cache behaviour was observable)."""
        usage = getattr(response, "usage_metadata", None)
        total = getattr(usage, "total_token_count", None) if usage else None
        cached = getattr(usage, "cached_content_token_count", None) if usage else None
        prompt_tokens = getattr(usage, "prompt_token_count", None) if usage else None
        logger.info(
            f"gemini_call shape={call_shape} "
            f"model={GEMINI_MODEL} "
            f"latency_seconds={time.monotonic() - started:.2f} "
            f"cache={'ref:' + cached_content if cached_content else 'none'} "
            f"prompt_tokens={prompt_tokens} cached_tokens={cached} total_tokens={total}"
        )

    def analyze(self, prompt):

        started = time.monotonic()
        response = self.client.models.generate_content(

            model=GEMINI_MODEL,

            contents=prompt,

            config=self._config(),

        )
        self._log("full_prompt", started, response, cached_content=None)

        return response.text

    def analyze_with_cache(self, contents: str, cached_content: str) -> str:
        """Generate using a server-side cached_contents block as the
        immutable evidence; only `contents` (recent conversation + question)
        travels. Falls back is the CALLER's job - this raises the same
        exception analyze() would on a cache expiry/miss so the caller can
        catch, recreate-or-fall-back."""
        started = time.monotonic()
        response = self.client.models.generate_content(

            model=GEMINI_MODEL,

            contents=contents,

            config=self._config(cached_content=cached_content),

        )
        self._log("cached", started, response, cached_content=cached_content)

        return response.text

    # --- server-side context caching (issue #5, paid tier) ---------------
    def create_cache(self, *, model: str, contents: str, ttl_seconds: int, display_name: str):
        """Create a Gemini cached_contents block holding the immutable
        evidence. Returns the cache object (.name is the reference). Raises
        on free tier (storage quota 0) / below the 1024-token floor - the
        caller treats ANY exception as "caching unavailable, go uncached"."""
        return self.client.caches.create(
            model=model,
            config=types.CreateCachedContentConfig(
                display_name=display_name,
                contents=contents,
                ttl=f"{ttl_seconds}s",
            ),
        )

    def delete_cache(self, name: str) -> None:
        """Best-effort cache release; never raises."""
        try:
            self.client.caches.delete(name=name)
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup
            logger.warning(f"delete_cache failed for {name}: {exc}")

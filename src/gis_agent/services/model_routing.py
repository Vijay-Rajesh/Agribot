import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from agents import Runner


logger = logging.getLogger(__name__)


def model_http_status(error: BaseException) -> int | None:
    visited: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        status = getattr(current, "status_code", None)
        if isinstance(status, int):
            return status
        response_status = getattr(getattr(current, "response", None), "status_code", None)
        if isinstance(response_status, int):
            return response_status
        current = current.__cause__ or current.__context__
    return None


def _parse_duration(value: str | None) -> float | None:
    if not value:
        return None
    match = re.fullmatch(
        r"\s*(?:(\d+(?:\.\d+)?)h)?\s*(?:(\d+(?:\.\d+)?)m)?\s*(?:(\d+(?:\.\d+)?)s)?\s*",
        value,
        re.IGNORECASE,
    )
    if not match or not any(match.groups()):
        try:
            return max(0.0, float(value))
        except ValueError:
            return None
    hours, minutes, seconds = (float(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _quota_cooldown_seconds(error: BaseException) -> float:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return 60.0

    remaining_requests = headers.get("x-ratelimit-remaining-requests")
    remaining_tokens = headers.get("x-ratelimit-remaining-tokens")
    reset_values = []
    if remaining_requests is not None and remaining_requests.strip() == "0":
        reset_values.append(_parse_duration(headers.get("x-ratelimit-reset-requests")))
    if remaining_tokens is not None and remaining_tokens.strip() == "0":
        reset_values.append(_parse_duration(headers.get("x-ratelimit-reset-tokens")))
    reset_values = [value for value in reset_values if value is not None]
    if reset_values:
        return max(reset_values)

    return _parse_duration(headers.get("retry-after")) or 60.0


class ModelProviderRouter:
    """Routes model calls to Groq first and bypasses exhausted Groq quota until reset."""

    def __init__(self, groq_config, gemini_config):
        if groq_config is None and gemini_config is None:
            raise RuntimeError("Configure at least one of GROQ_API_KEY or GEMINI_API_KEY.")
        if groq_config is None:
            logger.warning("GROQ_API_KEY is missing; Gemini will be used as the primary model.")
        if gemini_config is None:
            logger.warning("GEMINI_API_KEY is missing; Groq quota fallback is unavailable.")
        self.groq_config = groq_config
        self.gemini_config = gemini_config
        self._groq_disabled_until = 0.0

    @property
    def groq_quota_available(self) -> bool:
        return self.groq_config is not None and time.monotonic() >= self._groq_disabled_until

    def _primary_config(self):
        if self.groq_quota_available:
            return self.groq_config, True
        if self.gemini_config is None:
            return self.groq_config, True
        return self.gemini_config, False

    def _mark_groq_quota_exhausted(self, error: BaseException) -> None:
        cooldown = _quota_cooldown_seconds(error)
        self._groq_disabled_until = time.monotonic() + cooldown
        logger.warning(
            "Groq returned HTTP 429; routing model calls to Gemini for %.1f seconds.",
            cooldown,
        )

    def run_streamed(
        self,
        *,
        input: Any,
        starting_agent,
        on_fallback: Callable[[], Awaitable[None]] | None = None,
    ):
        return _RoutedStream(self, input, starting_agent, on_fallback)

    async def run(
        self,
        *,
        input: Any,
        starting_agent,
    ):
        run_config, using_groq = self._primary_config()
        try:
            return await Runner.run(
                input=input,
                run_config=run_config,
                starting_agent=starting_agent,
            )
        except Exception as exc:
            if not using_groq or model_http_status(exc) != 429 or self.gemini_config is None:
                raise
            self._mark_groq_quota_exhausted(exc)
            logger.info("Retrying the current model request with Gemini after Groq quota exhaustion.")
            return await Runner.run(
                input=input,
                run_config=self.gemini_config,
                starting_agent=starting_agent,
            )


class _RoutedStream:
    def __init__(
        self,
        router: ModelProviderRouter,
        input: Any,
        starting_agent,
        on_fallback: Callable[[], Awaitable[None]] | None,
    ):
        self.router = router
        self.input = input
        self.starting_agent = starting_agent
        self.on_fallback = on_fallback
        self.final_output = None

    async def stream_events(self) -> AsyncIterator[Any]:
        run_config, using_groq = self.router._primary_config()
        try:
            result = Runner.run_streamed(
                input=self.input,
                run_config=run_config,
                starting_agent=self.starting_agent,
            )
            async for event in result.stream_events():
                yield event
            self.final_output = result.final_output
            return
        except Exception as exc:
            if not using_groq or model_http_status(exc) != 429 or self.router.gemini_config is None:
                raise
            self.router._mark_groq_quota_exhausted(exc)
            logger.info("Retrying the current model request with Gemini after Groq quota exhaustion.")

        fallback_callback = self.on_fallback
        fallback_announced = fallback_callback is None
        result = Runner.run_streamed(
            input=self.input,
            run_config=self.router.gemini_config,
            starting_agent=self.starting_agent,
        )
        async for event in result.stream_events():
            if fallback_callback is not None and not fallback_announced:
                await fallback_callback()
                fallback_announced = True
            yield event
        if fallback_callback is not None and not fallback_announced:
            await fallback_callback()
        self.final_output = result.final_output

import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import httpx
from openai import BadRequestError, InternalServerError, RateLimitError

from services.model_routing import ModelProviderRouter


def rate_limit_error(headers=None):
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(429, headers=headers or {}, request=request)
    return RateLimitError("rate limit reached", response=response, body={"error": "rate limit"})


class FakeStream:
    def __init__(self, events=None, error=None, final_output="response"):
        self.events = events or []
        self.error = error
        self.final_output = final_output

    async def stream_events(self):
        if self.error:
            raise self.error
        for event in self.events:
            yield event


class ModelProviderRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.groq = object()
        self.gemini = object()
        self.router = ModelProviderRouter(self.groq, self.gemini)

    async def test_streamed_quota_error_falls_back_and_routes_next_request_to_gemini(self):
        assistant_event = SimpleNamespace(
            type="raw_response_event",
            data=SimpleNamespace(delta="Gemini response"),
        )
        with patch(
            "services.model_routing.Runner.run_streamed",
            side_effect=[
                FakeStream(error=rate_limit_error()),
                FakeStream([assistant_event], final_output="Gemini response"),
                FakeStream([assistant_event], final_output="Gemini again"),
            ],
        ) as run_streamed:
            streamed = self.router.run_streamed(input="hello", starting_agent=object())
            events = [event async for event in streamed.stream_events()]
            self.assertEqual(streamed.final_output, "Gemini response")
            self.assertEqual(len(events), 1)

            next_stream = self.router.run_streamed(input="again", starting_agent=object())
            [event async for event in next_stream.stream_events()]

        self.assertEqual(
            [call.kwargs["run_config"] for call in run_streamed.call_args_list],
            [self.groq, self.gemini, self.gemini],
        )

    async def test_reset_header_controls_groq_cooldown(self):
        self.router._mark_groq_quota_exhausted(
            rate_limit_error(
                {
                    "x-ratelimit-remaining-requests": "0",
                    "x-ratelimit-reset-requests": "2m30s",
                }
            )
        )

        self.assertFalse(self.router.groq_quota_available)

    async def test_failed_gemini_fallback_does_not_announce_success(self):
        on_fallback = AsyncMock()
        with patch(
            "services.model_routing.Runner.run_streamed",
            side_effect=[
                FakeStream(error=rate_limit_error()),
                FakeStream(
                    error=BadRequestError(
                        "invalid API key",
                        response=httpx.Response(
                            400,
                            request=httpx.Request("POST", "https://generativelanguage.googleapis.com"),
                        ),
                        body={"error": "invalid key"},
                    )
                ),
            ],
        ):
            streamed = self.router.run_streamed(
                input="hello",
                starting_agent=object(),
                on_fallback=on_fallback,
            )
            with self.assertRaises(BadRequestError):
                [event async for event in streamed.stream_events()]

        on_fallback.assert_not_awaited()

    async def test_server_error_does_not_switch_provider(self):
        with patch(
            "services.model_routing.Runner.run",
            new_callable=AsyncMock,
            side_effect=InternalServerError(
                "temporarily unavailable",
                response=httpx.Response(
                    503,
                    request=httpx.Request("POST", "https://api.groq.com"),
                ),
                body={"error": "unavailable"},
            ),
        ) as run:
            with self.assertRaises(InternalServerError):
                await self.router.run(input="hello", starting_agent=object())

        run.assert_awaited_once()
        self.assertTrue(self.router.groq_quota_available)

    async def test_nonstreamed_quota_error_falls_back(self):
        fallback_result = object()
        with patch(
            "services.model_routing.Runner.run",
            new_callable=AsyncMock,
            side_effect=[rate_limit_error(), fallback_result],
        ) as run:
            result = await self.router.run(input="report", starting_agent=object())

        self.assertIs(result, fallback_result)
        self.assertEqual(
            [call.kwargs["run_config"] for call in run.await_args_list],
            [self.groq, self.gemini],
        )


if __name__ == "__main__":
    unittest.main()

"""The one place jevreel talks to Jev. Everything else builds state and questions and reads answers."""

import asyncio
from collections.abc import Mapping
from typing import Any, Protocol

from typesafe_sdk import AsyncTypeSafeClient, Question, SystemOneResponse, TypeSafeBadRequestError

# Opt-in headers for Jev's experimental features. The SDK does not set them itself.
IMAGES_HEADER = "TypeSafe-Danger-Zone-Images"
LONG_CONTEXT_HEADER = "TypeSafe-Danger-Zone-Long-Context"


class Judge(Protocol):
    input_tokens: int
    requests: int

    async def ask(
        self, state: Any, questions: Mapping[str, Question], *, long_context: bool = False
    ) -> SystemOneResponse: ...

    async def aclose(self) -> None: ...


class JevJudge:
    """Jev through the official async SDK, with a cap on parallel requests and a running token count."""

    def __init__(self, *, model: str, concurrency: int, api_key: str | None = None) -> None:
        self._client = AsyncTypeSafeClient(api_key=api_key, model=model, headers={IMAGES_HEADER: "true"})
        self._limit = asyncio.Semaphore(concurrency)
        self.input_tokens = 0
        self.requests = 0

    async def ask(
        self, state: Any, questions: Mapping[str, Question], *, long_context: bool = False
    ) -> SystemOneResponse:
        headers = {LONG_CONTEXT_HEADER: "true"} if long_context else None
        async with self._limit:
            result = await self._client.system_one(state=state, questions=questions, extra_headers=headers)
        self.input_tokens += result.usage.input_tokens or 0
        self.requests += 1
        return result

    async def aclose(self) -> None:
        await self._client.aclose()


def is_context_overflow(error: Exception) -> bool:
    """Jev answers 400 `max_tokens_exceeded` when state and questions exceed the context length."""
    if not isinstance(error, TypeSafeBadRequestError):
        return False
    # The code may sit in the JSON body rather than the message, so look in both.
    return "max_tokens" in f"{error} {error.body}"

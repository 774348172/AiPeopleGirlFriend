from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Sequence

from runtime._context import ReplyContext
from runtime._model import ReplyRequest
from runtime._prompt import build_reply_messages


RequestHook = Callable[[ReplyRequest], Awaitable[None] | None]


class FakeReplyModel:
    def __init__(
        self,
        chunks: Sequence[str] = ("这是", "一条", "测试回复。"),
        *,
        delay_seconds: float = 0.0,
        fail_after_chunks: int | None = None,
        wait_event: asyncio.Event | None = None,
        on_request: RequestHook | None = None,
        context_size: int = 4096,
        prompt_token_overhead: int = 3,
    ) -> None:
        if delay_seconds < 0:
            raise ValueError("delay_seconds cannot be negative")
        if fail_after_chunks is not None and fail_after_chunks < 0:
            raise ValueError("fail_after_chunks cannot be negative")
        if context_size <= 0 or prompt_token_overhead < 0:
            raise ValueError("invalid fake prompt measurement configuration")
        self.chunks = list(chunks)
        self.delay_seconds = delay_seconds
        self.fail_after_chunks = fail_after_chunks
        self.wait_event = wait_event
        self.on_request = on_request
        self.context_size = context_size
        self.prompt_token_overhead = prompt_token_overhead
        self.calls = 0
        self.start_calls = 0
        self.close_calls = 0
        self.requests: list[ReplyRequest] = []
        self.measured_contexts: list[ReplyContext] = []
        self.started = asyncio.Event()

    async def start(self) -> None:
        self.start_calls += 1

    async def close(self) -> None:
        self.close_calls += 1

    async def measure_prompt(self, context: ReplyContext) -> int:
        self.measured_contexts.append(context)
        messages = build_reply_messages(context)
        return self.prompt_token_overhead + sum(
            len(message["content"]) + 1 for message in messages
        )

    async def stream_reply(self, request: ReplyRequest):
        self.calls += 1
        self.requests.append(request)
        self.started.set()

        if self.on_request is not None:
            result = self.on_request(request)
            if inspect.isawaitable(result):
                await result

        if self.wait_event is not None:
            await self.wait_event.wait()

        if self.fail_after_chunks == 0:
            raise RuntimeError("configured fake model failure")

        emitted = 0
        for chunk in self.chunks:
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            yield chunk
            emitted += 1
            if self.fail_after_chunks == emitted:
                raise RuntimeError("configured fake model failure")

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from ._context import ReplyContext


@dataclass(frozen=True, slots=True)
class ReplyRequest:
    request_id: str
    conversation_id: str
    user_event_id: str
    text: str
    context: ReplyContext

    def __post_init__(self) -> None:
        for value, name in (
            (self.request_id, "request_id"),
            (self.conversation_id, "conversation_id"),
            (self.user_event_id, "user_event_id"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} cannot be empty")
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        if self.context.current_user_event_id != self.user_event_id:
            raise ValueError("context current user event does not match request")
        if self.context.current_user_text != self.text:
            raise ValueError("context current user text does not match request")


class ReplyModel(Protocol):
    async def start(self) -> None: ...

    async def measure_prompt(self, context: ReplyContext) -> int: ...

    def stream_reply(self, request: ReplyRequest) -> AsyncIterator[str]: ...

    async def close(self) -> None: ...

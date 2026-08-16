from __future__ import annotations

from dataclasses import dataclass

from runtime.contracts import MessageSource, UserMessage

from .contracts import RuntimeSessionIdentity


@dataclass(frozen=True, slots=True)
class TurnRequest:
    request_id: str
    session: RuntimeSessionIdentity
    text: str
    source: MessageSource = "typed"

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("request_id cannot be empty")
        if not isinstance(self.session, RuntimeSessionIdentity):
            raise TypeError("session must be a RuntimeSessionIdentity")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("text cannot be empty")
        if self.source not in ("typed", "stt"):
            raise ValueError("source must be typed or stt")


@dataclass(frozen=True, slots=True)
class LegacyTurnRequestAdapter:
    session: RuntimeSessionIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.session, RuntimeSessionIdentity):
            raise TypeError("session must be a RuntimeSessionIdentity")

    def adapt(self, message: UserMessage) -> TurnRequest:
        if not isinstance(message, UserMessage):
            raise TypeError("message must be a UserMessage")
        if message.conversation_id != self.session.conversation_id:
            raise ValueError("legacy message conversation_id must match session")
        return TurnRequest(
            request_id=message.request_id,
            session=self.session,
            text=message.text,
            source=message.source,
        )

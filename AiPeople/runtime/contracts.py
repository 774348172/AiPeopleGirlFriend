from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeAlias
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MessageSource: TypeAlias = Literal["typed", "stt"]


@dataclass(frozen=True, slots=True)
class UserMessage:
    request_id: str
    conversation_id: str
    text: str
    occurred_at: datetime
    timezone: str
    source: MessageSource = "typed"


@dataclass(frozen=True, slots=True)
class TurnMetrics:
    ledger_user_commit_ms: float
    model_first_delta_ms: float | None
    model_total_ms: float
    ledger_reply_commit_ms: float
    total_ms: float
    output_chars: int
    delta_count: int
    replayed: bool = False
    epoch_prepare_ms: float = 0.0
    working_activation_ms: float = 0.0
    recall_ms: float = 0.0
    prompt_measure_ms: float = 0.0
    history_event_count: int = 0
    recall_evidence_count: int = 0
    prompt_tokens: int = 0
    epoch_rolled_over: bool = False


@dataclass(frozen=True, slots=True)
class TextDelta:
    request_id: str
    text: str


@dataclass(frozen=True, slots=True)
class Completed:
    request_id: str
    user_event_id: str
    assistant_event_id: str
    text: str
    metrics: TurnMetrics


@dataclass(frozen=True, slots=True)
class Failed:
    request_id: str
    user_event_id: str | None
    code: str
    retryable: bool


ReplyEvent: TypeAlias = TextDelta | Completed | Failed


def _validation_error(message: UserMessage, max_input_chars: int) -> str | None:
    if not isinstance(message.request_id, str) or not message.request_id.strip():
        return "request_id is required"
    if not isinstance(message.conversation_id, str) or not message.conversation_id.strip():
        return "conversation_id is required"
    if not isinstance(message.text, str) or not message.text.strip():
        return "text is required"
    if len(message.text) > max_input_chars:
        return f"text exceeds {max_input_chars} characters"
    if not isinstance(message.occurred_at, datetime):
        return "occurred_at must be a datetime"
    if message.occurred_at.utcoffset() is None:
        return "occurred_at must include a timezone"
    if not isinstance(message.timezone, str) or not message.timezone.strip():
        return "timezone is required"
    if len(message.timezone) > 128:
        return "timezone is too long"
    try:
        ZoneInfo(message.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return "timezone must be a valid IANA timezone"
    if message.source not in ("typed", "stt"):
        return "source must be typed or stt"
    return None

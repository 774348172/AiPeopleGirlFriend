from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from runtime._context import ContextMessage, WorkingActivation, epoch_reply_context
from runtime._memory_vectors import BGE_DIMENSION, BGE_MODEL_ID, EncoderIdentity


NOW = datetime(2026, 8, 8, 14, 5, 6, tzinfo=ZoneInfo("Asia/Shanghai"))
IDENTITY = EncoderIdentity(
    model_id=BGE_MODEL_ID,
    revision="test-revision",
    artifact_sha256="a" * 64,
    dimension=BGE_DIMENSION,
)


def make_context(
    *,
    current_text: str = "你还记得我们说过的那家店吗？",
    history_texts: tuple[str, ...] = (),
    now: datetime = NOW,
    timezone: str = "Asia/Shanghai",
):
    history = tuple(
        ContextMessage(
            event_id=f"history-{index}",
            role="user" if index % 2 else "assistant",
            text=text,
            sequence_no=index,
            occurred_at=now,
        )
        for index, text in enumerate(history_texts, start=1)
    )
    current_id = "current-user-event"
    activation = WorkingActivation(
        epoch_id="epoch-1",
        recent_verbatim_event_ids=tuple(message.event_id for message in history)
        + (current_id,),
        last_completed_user_event_id=("history-1" if len(history) >= 2 else None),
        last_completed_character_event_id=("history-2" if len(history) >= 2 else None),
        unresolved_user_event_ids=("prior-unresolved", current_id),
        explicit_recall_cues=(),
        current_time=now,
        current_timezone=timezone,
    )
    return epoch_reply_context(
        epoch_id="epoch-1",
        history_messages=history,
        user_event_id=current_id,
        text=current_text,
        occurred_at=now,
        timezone=timezone,
        context_size=4096,
        reply_reserve_tokens=512,
        safety_margin_tokens=128,
        working_activation=activation,
    )


class FakeBge:
    def __init__(self, *, identity: EncoderIdentity = IDENTITY, token_count: int = 64):
        self._identity = identity
        self._token_count = token_count
        self.encode_calls: list[tuple[str, ...]] = []
        self.token_calls: list[str] = []

    @property
    def identity(self) -> EncoderIdentity:
        return self._identity

    def count_tokens(self, text: str) -> int:
        self.token_calls.append(text)
        return self._token_count

    def encode(self, texts):
        batch = tuple(texts)
        self.encode_calls.append(batch)
        return [[float(index + 1) for index in range(self.identity.dimension)] for _ in batch]


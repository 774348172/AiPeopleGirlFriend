from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from runtime.contracts import UserMessage
from runtime.world_mind import (
    LegacyTurnRequestAdapter,
    RuntimeSessionIdentity,
    TurnRequest,
)


def _session(conversation_id: str = "conversation-a") -> RuntimeSessionIdentity:
    return RuntimeSessionIdentity(
        save_id="save_001",
        world_id="songjiangfu",
        protagonist_id="protagonist",
        active_character_id="baiweixi",
        conversation_id=conversation_id,
    )


def test_turn_request_carries_complete_session_identity() -> None:
    request = TurnRequest(
        request_id="req_001",
        session=_session(),
        text="今天的雨好像小了一些。",
    )

    assert request.session.save_id == "save_001"
    assert request.session.world_id == "songjiangfu"
    assert request.session.protagonist_id == "protagonist"
    assert request.session.active_character_id == "baiweixi"
    assert request.session.conversation_id == "conversation-a"
    assert request.source == "typed"


def test_turn_request_preserves_player_utterance_without_classification() -> None:
    text = "我现实中今天上班好累。"

    request = TurnRequest(request_id="req_002", session=_session(), text=text)

    assert request.text == text
    assert not hasattr(request, "occurred_at")
    assert not hasattr(request, "timezone")
    assert not hasattr(request, "input_type")


@pytest.mark.parametrize(
    ("field", "value", "error"),
    (
        ("request_id", "", "request_id cannot be empty"),
        ("text", " ", "text cannot be empty"),
        ("source", "command", "source must be typed or stt"),
    ),
)
def test_turn_request_rejects_invalid_fields(
    field: str, value: object, error: str
) -> None:
    values: dict[str, object] = {
        "request_id": "req_003",
        "session": _session(),
        "text": "你好。",
        "source": "typed",
    }
    values[field] = value

    with pytest.raises(ValueError, match=error):
        TurnRequest(**values)  # type: ignore[arg-type]


def test_turn_request_requires_runtime_session_identity() -> None:
    with pytest.raises(TypeError, match="session must be a RuntimeSessionIdentity"):
        TurnRequest(
            request_id="req_004",
            session=object(),  # type: ignore[arg-type]
            text="你好。",
        )


def test_turn_request_is_immutable() -> None:
    request = TurnRequest(request_id="req_005", session=_session(), text="你好。")

    with pytest.raises(FrozenInstanceError):
        request.text = "修改后的文字"  # type: ignore[misc]


def test_legacy_adapter_requires_matching_explicit_session() -> None:
    adapter = LegacyTurnRequestAdapter(session=_session())
    message = UserMessage(
        request_id="req_006",
        conversation_id="conversation-a",
        text="我现实中今天上班好累。",
        occurred_at=datetime(2026, 8, 9, 4, 0, tzinfo=timezone.utc),
        timezone="UTC",
    )

    request = adapter.adapt(message)

    assert request.session == adapter.session
    assert request.text == message.text
    assert request.source == message.source
    assert not hasattr(request, "occurred_at")
    assert not hasattr(request, "timezone")


def test_legacy_adapter_rejects_conversation_mismatch() -> None:
    adapter = LegacyTurnRequestAdapter(session=_session("conversation-a"))
    message = UserMessage(
        request_id="req_007",
        conversation_id="conversation-b",
        text="你好。",
        occurred_at=datetime(2026, 8, 9, 4, 0, tzinfo=timezone.utc),
        timezone="UTC",
    )

    with pytest.raises(
        ValueError, match="legacy message conversation_id must match session"
    ):
        adapter.adapt(message)

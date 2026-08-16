from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from runtime._context import initial_reply_context
from runtime._model import ReplyRequest
from runtime.adapters import FakeReplyModel
from runtime.contracts import UserMessage, _validation_error


NOW = datetime(2026, 8, 4, 4, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (UserMessage("", "c1", "你好", NOW, "Asia/Shanghai"), "request_id"),
        (UserMessage("r1", "", "你好", NOW, "Asia/Shanghai"), "conversation_id"),
        (UserMessage("r1", "c1", "  \n", NOW, "Asia/Shanghai"), "text"),
        (
            UserMessage("r1", "c1", "你好", datetime(2026, 8, 4), "Asia/Shanghai"),
            "timezone",
        ),
        (UserMessage("r1", "c1", "你好", NOW, ""), "timezone"),
        (UserMessage("r1", "c1", "你好", NOW, "Not/A-Timezone"), "timezone"),
        (UserMessage("r1", "c1", "你好", NOW, "Asia/Shanghai", "cloud"), "source"),
    ],
)
def test_invalid_messages_are_rejected(message, expected):
    assert expected in (_validation_error(message, 8192) or "")


def test_overlong_message_is_rejected_without_truncation():
    text = "秦" * 8193
    message = UserMessage("r1", "c1", text, NOW, "Asia/Shanghai")
    assert "exceeds" in (_validation_error(message, 8192) or "")
    assert message.text == text


def test_valid_message_preserves_all_text_characters():
    text = "  第一行\n第二行。  "
    message = UserMessage("r1", "c1", text, NOW, "Asia/Shanghai")
    assert _validation_error(message, 8192) is None
    assert message.text == text


async def _collect(model: FakeReplyModel):
    context = initial_reply_context(
        user_event_id="e1",
        text="你好",
        occurred_at=NOW,
        timezone="Asia/Shanghai",
        context_size=4096,
        reply_reserve_tokens=256,
        safety_margin_tokens=256,
    )
    request = ReplyRequest("r1", "c1", "e1", "你好", context)
    return [chunk async for chunk in model.stream_reply(request)]


async def test_fake_model_streams_configured_chunks():
    model = FakeReplyModel(["甲", "乙", "丙"])
    await model.start()
    assert await _collect(model) == ["甲", "乙", "丙"]
    await model.close()
    assert model.calls == 1
    assert model.start_calls == 1
    assert model.close_calls == 1


async def test_fake_model_can_return_empty_output():
    assert await _collect(FakeReplyModel([])) == []


async def test_fake_model_can_fail_after_a_chunk():
    model = FakeReplyModel(["甲", "乙"], fail_after_chunks=1)
    with pytest.raises(RuntimeError, match="configured fake model failure"):
        await _collect(model)


async def test_fake_model_wait_can_be_cancelled():
    model = FakeReplyModel(wait_event=asyncio.Event())
    task = asyncio.create_task(_collect(model))
    await model.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

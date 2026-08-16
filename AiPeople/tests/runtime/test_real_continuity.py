from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from runtime import Completed, Failed, RelationshipRuntime, RuntimeConfig, TextDelta, UserMessage
from runtime._ledger import EventLedger
from runtime.adapters import LlamaCppConfig, LlamaCppReplyModel


RUN_REAL = os.environ.get("AIPEOPLE_RUN_REAL_CONTINUITY") == "1"
MANIFEST = Path(
    os.environ.get(
        "AIPEOPLE_MODEL_MANIFEST",
        "F:/ai-girlfriend/AiPeople/local_runtime/model_runtime_manifest.json",
    )
)
pytestmark = pytest.mark.skipif(
    not RUN_REAL,
    reason="set AIPEOPLE_RUN_REAL_CONTINUITY=1 to run Stage 5 real continuity",
)


class RecordingLlamaModel(LlamaCppReplyModel):
    def __init__(self, config: LlamaCppConfig) -> None:
        super().__init__(config)
        self.requests = []
        self.generation_stats = []

    async def stream_reply(self, request):
        self.requests.append(request)
        async for chunk in super().stream_reply(request):
            yield chunk
        if self.last_generation_stats is not None:
            self.generation_stats.append(self.last_generation_stats)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _message(request_id: str, text: str) -> UserMessage:
    return UserMessage(
        request_id,
        "stage5-real-continuity",
        text,
        datetime.now(ZoneInfo("Asia/Shanghai")),
        "Asia/Shanghai",
    )


async def _turn(runtime, request_id: str, text: str):
    events = [event async for event in runtime.handle_turn(_message(request_id, text))]
    failure = next((event for event in events if isinstance(event, Failed)), None)
    assert failure is None, failure
    completed = next(event for event in events if isinstance(event, Completed))
    output = "".join(event.text for event in events if isinstance(event, TextDelta))
    assert output == completed.text
    return output, completed


async def _wait_for_exit(pid: int, timeout: float = 10.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                f"Get-Process -Id {pid} -ErrorAction SilentlyContinue",
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return True
        await asyncio.sleep(0.1)
    return False


def _runtime_config(data_dir: Path) -> RuntimeConfig:
    return RuntimeConfig(
        data_dir,
        context_size=4096,
        reply_reserve_tokens=1700,
        context_safety_margin_tokens=1700,
        recall_target_tokens=300,
        recent_verbatim_target_tokens=256,
    )


def _model_config() -> LlamaCppConfig:
    return LlamaCppConfig.from_manifest(
        MANIFEST,
        port=_free_port(),
        max_tokens=32,
        seed=42,
        collect_usage=True,
    )


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, int(len(ordered) * 0.95) - 1)]


async def test_real_stage5_continuity(tmp_path: Path) -> None:
    config = _runtime_config(tmp_path)
    completed_turns: list[Completed] = []
    content_checks: dict[str, bool] = {}

    first_model = RecordingLlamaModel(_model_config())
    first_pid = None
    async with RelationshipRuntime.open(config, first_model) as runtime:
        first_pid = first_model.process_id
        _fact_output, fact_completed = await _turn(
            runtime,
            "fact",
            "请记住一个临时代号：琥珀舟。只需简短回应。",
        )
        follow_output, follow_completed = await _turn(
            runtime,
            "follow",
            "我刚才说的临时代号是什么？",
        )
        completed_turns.extend((fact_completed, follow_completed))
        follow_context = first_model.requests[-1].context
        assert any("琥珀舟" in item.text for item in follow_context.history_messages)
        content_checks["followup_used_context"] = "琥珀舟" in follow_output
    assert first_pid is not None and await _wait_for_exit(first_pid)

    restarted_model = RecordingLlamaModel(_model_config())
    restarted_pid = None
    async with RelationshipRuntime.open(config, restarted_model) as runtime:
        restarted_pid = restarted_model.process_id
        restart_output, restart_completed = await _turn(
            runtime,
            "restart-follow",
            "重启后再说一次，那个临时代号是什么？",
        )
        completed_turns.append(restart_completed)
        restart_context = restarted_model.requests[-1].context
        assert any("琥珀舟" in item.text for item in restart_context.history_messages)
        assert restart_completed.metrics.epoch_rolled_over is False
        content_checks["restart_used_context"] = "琥珀舟" in restart_output
    assert restarted_pid is not None and await _wait_for_exit(restarted_pid)

    ledger = EventLedger.open(config.database_path)
    try:
        seed = ledger.append_user_message(
            _message(
                "synthetic-long-history",
                "".join(f"填充历史{index:04d}；" for index in range(300)),
            )
        )
        ledger.append_character_message(
            request_id="synthetic-long-history",
            conversation_id="stage5-real-continuity",
            user_event_id=seed.event.event_id,
            text="填充完成",
        )
    finally:
        ledger.close()

    final_model = RecordingLlamaModel(_model_config())
    initial_recovery_pid = None
    recovered_pid = None
    cancelled_partial = ""
    async with RelationshipRuntime.open(config, final_model) as runtime:
        initial_recovery_pid = final_model.process_id
        recall_output, recall_completed = await _turn(
            runtime,
            "recall",
            "你还记得琥珀舟吗？",
        )
        completed_turns.append(recall_completed)
        recall_context = final_model.requests[-1].context
        assert recall_completed.metrics.epoch_rolled_over is True
        assert recall_context.history_messages == ()
        assert recall_context.recall_frame.evidence
        assert "琥珀舟" in recall_context.recall_frame.evidence[0].verbatim_excerpt
        content_checks["rollover_recall_used_context"] = "琥珀舟" in recall_output

        stream = runtime.handle_turn(
            _message("cancel", "请讲一个较长的故事，不要立刻结束。")
        )
        async for event in stream:
            if isinstance(event, TextDelta):
                cancelled_partial += event.text
                await stream.aclose()
                break
        assert cancelled_partial

        _after_cancel_output, after_cancel_completed = await _turn(
            runtime,
            "after-cancel",
            "取消后只简短回复：继续。",
        )
        completed_turns.append(after_cancel_completed)
        after_cancel_context = final_model.requests[-1].context
        assert all(
            item.text != cancelled_partial
            for item in after_cancel_context.history_messages
            if item.role == "assistant"
        )

        assert initial_recovery_pid is not None
        subprocess.run(
            ["taskkill", "/PID", str(initial_recovery_pid), "/F"],
            check=True,
            capture_output=True,
        )
        assert await _wait_for_exit(initial_recovery_pid)
        await asyncio.sleep(0.2)
        _recovered_output, recovered_completed = await _turn(
            runtime,
            "after-crash",
            "进程恢复测试，只需简短回复。",
        )
        completed_turns.append(recovered_completed)
        recovered_pid = final_model.process_id
        assert recovered_pid is not None and recovered_pid != initial_recovery_pid

    assert recovered_pid is not None and await _wait_for_exit(recovered_pid)

    with sqlite3.connect(config.database_path) as connection:
        cancel_rows = connection.execute(
            "SELECT actor, event_type FROM events WHERE request_id='cancel' ORDER BY sequence_no"
        ).fetchall()
        recovered_users = connection.execute(
            """
            SELECT COUNT(*) FROM events
            WHERE request_id='after-crash' AND actor='user' AND event_type='message'
            """
        ).fetchone()[0]
    assert cancel_rows == [("user", "message"), ("system", "generation_cancelled")]
    assert recovered_users == 1
    assert all(
        item.metrics.prompt_tokens <= config.maximum_prompt_tokens
        for item in completed_turns
    )
    generation_stats = (
        first_model.generation_stats
        + restarted_model.generation_stats
        + final_model.generation_stats
    )
    assert len(generation_stats) == len(completed_turns)
    assert all(
        completed.metrics.prompt_tokens == stats.prompt_tokens
        for completed, stats in zip(completed_turns, generation_stats, strict=True)
    )

    prompt_measure_values = [
        item.metrics.prompt_measure_ms for item in completed_turns
    ]
    context_values = [
        item.metrics.working_activation_ms + item.metrics.recall_ms
        for item in completed_turns
    ]

    technical = {
        "followup_context_delivered": True,
        "restart_context_delivered": True,
        "rollover_recall_delivered": True,
        "cancel_partial_excluded": True,
        "server_recovered_without_duplicate_user": True,
        "prompt_overflow_count": 0,
        "max_prompt_tokens": max(
            item.metrics.prompt_tokens for item in completed_turns
        ),
        "configured_maximum_prompt_tokens": config.maximum_prompt_tokens,
        "generation_usage_matches_runtime": True,
        "prompt_measure_p95_ms": round(_p95(prompt_measure_values), 3),
        "context_processing_p95_ms": round(_p95(context_values), 3),
    }
    result = {
        "context_delivery_passed": all(
            value is True
            for key, value in technical.items()
            if key
            not in {
                "prompt_overflow_count",
                "max_prompt_tokens",
                "configured_maximum_prompt_tokens",
                "prompt_measure_p95_ms",
                "context_processing_p95_ms",
            }
        )
        and technical["prompt_overflow_count"] == 0,
        "model_used_context_correctly": content_checks,
        "technical": technical,
    }
    print("STAGE5_REAL_RESULT=" + json.dumps(result, ensure_ascii=False))
    assert result["context_delivery_passed"] is True

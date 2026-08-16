from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime._context import initial_reply_context
from runtime._model import ReplyRequest
from runtime._prompt import build_reply_messages
from runtime.adapters.llama_cpp import (
    GenerationOptions,
    LlamaCppAssetError,
    LlamaCppConfig,
    LlamaCppNotReadyError,
    LlamaCppProtocolError,
    LlamaCppReplyModel,
    LlamaCppStartupError,
)


FAKE_SERVER = Path(__file__).with_name("fake_llama_server.py").resolve()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _config(tmp_path: Path, scenario: str = "normal", **overrides: object) -> LlamaCppConfig:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fake-model")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "llama_cpp": {"server_sha256": _digest(Path(sys.executable))},
                "model": {"sha256": _digest(model), "bytes": model.stat().st_size},
            }
        ),
        encoding="utf-8",
    )
    values: dict[str, object] = {
        "server_executable": Path(sys.executable),
        "model_path": model,
        "manifest_path": manifest,
        "port": _free_port(),
        "startup_timeout_seconds": 2.0,
        "connect_timeout_seconds": 0.2,
        "read_idle_timeout_seconds": 0.2,
        "generation_timeout_seconds": 1.0,
        "shutdown_timeout_seconds": 1.0,
        "chat_template": "chatml",
        "stop_sequences": ("<|im_end|>", "<|im_start|>"),
        "server_prefix_args": (
            str(FAKE_SERVER),
            "--fake-scenario",
            scenario,
            "--fake-state-file",
            str(tmp_path / "launches.txt"),
            "--fake-observation-file",
            str(tmp_path / "observations.jsonl"),
        ),
    }
    values.update(overrides)
    return LlamaCppConfig(**values)  # type: ignore[arg-type]


def _request(text: str = "玩家私密输入") -> ReplyRequest:
    context = initial_reply_context(
        user_event_id="event-1",
        text=text,
        occurred_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        timezone="Asia/Shanghai",
        context_size=4096,
        reply_reserve_tokens=256,
        safety_margin_tokens=256,
    )
    return ReplyRequest(
        "request-1", "conversation-1", "event-1", text, context
    )


async def _collect(model: LlamaCppReplyModel, request: ReplyRequest | None = None) -> list[str]:
    return [chunk async for chunk in model.stream_reply(request or _request())]


@pytest.mark.parametrize("scenario", ["normal", "loading"])
async def test_start_warmup_stream_and_close_are_idempotent(tmp_path: Path, scenario: str) -> None:
    model = LlamaCppReplyModel(_config(tmp_path, scenario))
    await model.start()
    await model.start()
    assert model.state == "ready"
    assert await _collect(model) == ["你", "好"]
    await model.close()
    await model.close()
    assert model.state == "stopped"
    assert (tmp_path / "launches.txt").read_text(encoding="ascii") == "1"


async def test_request_has_prompt_sampling_and_non_thinking_fields(tmp_path: Path) -> None:
    model = LlamaCppReplyModel(_config(tmp_path))
    await model.start()
    try:
        assert await _collect(model, _request("  原文\n不变  ")) == ["你", "好"]
    finally:
        await model.close()
    observations = [json.loads(line) for line in (tmp_path / "observations.jsonl").read_text(encoding="utf-8").splitlines()]
    real = next(item for item in observations if not item["warmup"])
    assert real["accept"] == "text/event-stream"
    assert real["authorization"].startswith("Bearer ")
    assert real["body"]["messages"][-1] == {
        "role": "user",
        "content": "  原文\n不变  ",
    }
    assert real["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert real["body"]["stop"] == ["<|im_end|>", "<|im_start|>"]
    assert real["body"]["stream"] is True


def test_server_args_bind_declared_chat_template(tmp_path: Path) -> None:
    model = LlamaCppReplyModel(_config(tmp_path))
    args = model._server_args()
    template_index = args.index("--chat-template")
    assert args[template_index + 1] == "chatml"


async def test_length_finish_is_rejected_as_truncated_output(tmp_path: Path) -> None:
    model = LlamaCppReplyModel(_config(tmp_path, "length_finish"))
    await model.start()
    try:
        with pytest.raises(LlamaCppProtocolError, match="token limit"):
            await model.complete_chat(
                request_id="length-finish",
                messages=({"role": "user", "content": "继续"},),
                options=GenerationOptions(8, 0.2, 0.8, 1.05),
            )
        assert model.state == "ready"
    finally:
        await model.close()


async def test_per_request_generation_options_are_sent_and_timed(tmp_path: Path) -> None:
    model = LlamaCppReplyModel(_config(tmp_path))
    await model.start()
    try:
        options = GenerationOptions(
            max_tokens=37,
            temperature=0.0,
            top_p=1.0,
            repeat_penalty=1.05,
            seed=29,
        )
        chunks = [
            chunk
            async for chunk in model.stream_reply_with_options(_request(), options)
        ]
        assert chunks == ["你", "好"]
        stats = model.last_generation_stats
        assert stats is not None
        assert stats.first_token_seconds is not None
        assert stats.first_token_seconds >= 0
    finally:
        await model.close()
    observations = [
        json.loads(line)
        for line in (tmp_path / "observations.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    real = next(item for item in observations if not item["warmup"])
    assert real["body"]["max_tokens"] == 37
    assert real["body"]["temperature"] == 0.0
    assert real["body"]["top_p"] == 1.0
    assert real["body"]["repeat_penalty"] == 1.05
    assert real["body"]["seed"] == 29


async def test_generic_chat_completion_sends_json_schema_response_format(
    tmp_path: Path,
) -> None:
    model = LlamaCppReplyModel(_config(tmp_path))
    await model.start()
    try:
        text = await model.complete_chat(
            request_id="structured-request",
            messages=(
                {"role": "system", "content": "只输出 JSON。"},
                {"role": "user", "content": '{"mode":"TURN_MIND_ADVANCE"}'},
            ),
            options=GenerationOptions(
                max_tokens=64,
                temperature=0.2,
                top_p=0.8,
                repeat_penalty=1.05,
            ),
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "test_schema",
                    "strict": True,
                    "schema": {"type": "object"},
                },
            },
        )
        assert text == "你好"
    finally:
        await model.close()
    observations = [
        json.loads(line)
        for line in (tmp_path / "observations.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    request = next(
        item
        for item in observations
        if not item["warmup"]
        and item["body"].get("messages", [{}])[-1].get("content")
        == '{"mode":"TURN_MIND_ADVANCE"}'
    )
    assert request["body"]["response_format"]["type"] == "json_schema"
    assert request["body"]["stream"] is True


async def test_measure_prompt_applies_template_then_tokenizes_exact_rendering(
    tmp_path: Path,
) -> None:
    model = LlamaCppReplyModel(_config(tmp_path))
    await model.start()
    request = _request("逐字符\n保留")
    try:
        measured = await model.measure_prompt(request.context)
    finally:
        await model.close()

    messages = build_reply_messages(request.context)
    rendered = "".join(
        f"<|{item['role']}|>{item['content']}" for item in messages
    ) + "<|assistant|>"
    assert measured == len(rendered)
    observations = [
        json.loads(line)
        for line in (tmp_path / "observations.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    applied = next(
        item for item in observations if item["body"].get("add_generation_prompt")
    )
    tokenized = next(item for item in observations if "content" in item["body"])
    assert applied["body"]["messages"] == messages
    assert applied["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert tokenized["body"]["content"] == rendered
    assert tokenized["body"]["add_special"] is False
    assert tokenized["body"]["parse_special"] is True


@pytest.mark.parametrize(
    "scenario",
    [
        "measurement_unsupported",
        "measurement_timeout",
        "invalid_template",
        "invalid_tokens",
    ],
)
async def test_measure_prompt_fails_clearly_without_generating(
    tmp_path: Path, scenario: str
) -> None:
    model = LlamaCppReplyModel(_config(tmp_path, scenario))
    await model.start()
    try:
        with pytest.raises(LlamaCppProtocolError, match="prompt"):
            await model.measure_prompt(_request().context)
        assert model.state == "ready"
    finally:
        await model.close()


async def test_sse_protocol_edges_ignore_reasoning_and_empty_content(tmp_path: Path) -> None:
    model = LlamaCppReplyModel(_config(tmp_path, "sse_edges"))
    await model.start()
    try:
        assert await _collect(model) == ["边缘"]
    finally:
        await model.close()


async def test_thinking_tags_split_across_events_never_leak(tmp_path: Path) -> None:
    model = LlamaCppReplyModel(_config(tmp_path, "thinking"))
    await model.start()
    try:
        assert await _collect(model) == ["正文"]
    finally:
        await model.close()


@pytest.mark.parametrize("scenario", ["malformed", "invalid_schema", "invalid_event", "http_400"])
async def test_protocol_failures_do_not_restart_server(tmp_path: Path, scenario: str) -> None:
    model = LlamaCppReplyModel(_config(tmp_path, scenario))
    await model.start()
    try:
        with pytest.raises(LlamaCppProtocolError):
            await _collect(model)
        assert model.state == "ready"
        assert (tmp_path / "launches.txt").read_text(encoding="ascii") == "1"
    finally:
        await model.close()


@pytest.mark.parametrize("scenario", ["disconnect_before", "http_500"])
async def test_recoverable_failure_restarts_only_once(tmp_path: Path, scenario: str) -> None:
    model = LlamaCppReplyModel(_config(tmp_path, scenario))
    await model.start()
    try:
        with pytest.raises(Exception):
            await _collect(model)
        assert model.state == "failed"
        assert (tmp_path / "launches.txt").read_text(encoding="ascii") == "2"
    finally:
        await model.close()


async def test_read_timeout_restarts_once_then_fails(tmp_path: Path) -> None:
    model = LlamaCppReplyModel(_config(tmp_path, "read_timeout"))
    await model.start()
    try:
        with pytest.raises(Exception):
            await _collect(model)
        assert model.state == "failed"
        assert (tmp_path / "launches.txt").read_text(encoding="ascii") == "2"
    finally:
        await model.close()


async def test_total_generation_timeout_is_distinct_from_read_idle_timeout(tmp_path: Path) -> None:
    model = LlamaCppReplyModel(
        _config(
            tmp_path,
            "total_timeout",
            read_idle_timeout_seconds=0.5,
            generation_timeout_seconds=0.2,
        )
    )
    await model.start()
    chunks: list[str] = []
    try:
        with pytest.raises(Exception):
            async for chunk in model.stream_reply(_request()):
                chunks.append(chunk)
        assert chunks
        assert model.state == "failed"
        assert (tmp_path / "launches.txt").read_text(encoding="ascii") == "1"
    finally:
        await model.close()


async def test_crash_before_visible_text_restarts_and_replays_once(tmp_path: Path) -> None:
    model = LlamaCppReplyModel(_config(tmp_path, "crash_before"))
    await model.start()
    try:
        assert await _collect(model) == ["你", "好"]
        assert model.state == "ready"
        assert (tmp_path / "launches.txt").read_text(encoding="ascii") == "2"
    finally:
        await model.close()


async def test_disconnect_after_visible_text_never_replays(tmp_path: Path) -> None:
    model = LlamaCppReplyModel(_config(tmp_path, "disconnect_after"))
    await model.start()
    chunks: list[str] = []
    try:
        with pytest.raises(Exception):
            async for chunk in model.stream_reply(_request()):
                chunks.append(chunk)
        assert chunks == ["visible"]
        assert model.state == "failed"
        assert (tmp_path / "launches.txt").read_text(encoding="ascii") == "1"
    finally:
        await model.close()


async def test_permanent_crash_stops_after_one_recovery(tmp_path: Path) -> None:
    model = LlamaCppReplyModel(_config(tmp_path, "always_crash"))
    await model.start()
    try:
        with pytest.raises(Exception):
            await _collect(model)
        assert model.state == "failed"
        assert (tmp_path / "launches.txt").read_text(encoding="ascii") == "2"
    finally:
        await model.close()


async def test_concurrent_requests_are_serialized(tmp_path: Path) -> None:
    model = LlamaCppReplyModel(_config(tmp_path, "serialized"))
    await model.start()
    try:
        first, second = await asyncio.gather(_collect(model), _collect(model, _request("second")))
        assert first == second == ["顺序"]
    finally:
        await model.close()
    observations = [json.loads(line) for line in (tmp_path / "observations.jsonl").read_text(encoding="utf-8").splitlines()]
    phases = [item["phase"] for item in observations if "phase" in item]
    assert phases == ["start", "end", "start", "end"]


async def test_stream_before_start_fails_immediately(tmp_path: Path) -> None:
    model = LlamaCppReplyModel(_config(tmp_path))
    with pytest.raises(LlamaCppNotReadyError):
        await _collect(model)
    assert not (tmp_path / "launches.txt").exists()


async def test_cancelling_stream_closes_response_without_restart(tmp_path: Path) -> None:
    model = LlamaCppReplyModel(_config(tmp_path, "cancel"))
    await model.start()
    stream = model.stream_reply(_request())
    assert await anext(stream) == "start"
    await stream.aclose()
    await asyncio.sleep(0.25)
    try:
        assert model.state == "ready"
        assert (tmp_path / "launches.txt").read_text(encoding="ascii") == "1"
        observations = (tmp_path / "observations.jsonl").read_text(encoding="utf-8")
        assert '"client_disconnected": true' in observations
    finally:
        await model.close()


async def test_cancelled_chat_keeps_server_ready_for_next_request(tmp_path: Path) -> None:
    model = LlamaCppReplyModel(_config(tmp_path, "cancel_once"))
    await model.start()
    task = asyncio.create_task(
        model.complete_chat(
            request_id="background",
            messages=({"role": "user", "content": "持续输出"},),
            options=GenerationOptions(64, 0.2, 0.8, 1.05),
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert model.state == "ready"
    assert await model.complete_chat(
        request_id="foreground",
        messages=({"role": "user", "content": "前台回复"},),
        options=GenerationOptions(64, 0.2, 0.8, 1.05),
    ) == "你好"
    assert (tmp_path / "launches.txt").read_text(encoding="ascii") == "1"
    await model.close()


async def test_cancelled_recovery_remains_available_for_next_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = LlamaCppReplyModel(_config(tmp_path))
    model._state = "failed"
    model._recovery_available = True
    recovery_started = asyncio.Event()

    async def blocked_launch() -> None:
        recovery_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(model, "_launch_once", blocked_launch)
    recovery = asyncio.create_task(model._ensure_ready_for_request())
    await recovery_started.wait()
    recovery.cancel()
    with pytest.raises(asyncio.CancelledError):
        await recovery

    assert model.state == "failed"
    assert model._recovery_available is True


async def test_close_during_active_generation_never_restarts(tmp_path: Path) -> None:
    model = LlamaCppReplyModel(_config(tmp_path, "cancel"))
    await model.start()
    stream = model.stream_reply(_request())
    assert await anext(stream) == "start"
    await model.close()
    with pytest.raises(Exception):
        await anext(stream)
    await stream.aclose()
    assert model.state == "stopped"
    assert (tmp_path / "launches.txt").read_text(encoding="ascii") == "1"


async def test_wrong_model_alias_fails_start_and_cleans_up(tmp_path: Path) -> None:
    model = LlamaCppReplyModel(_config(tmp_path, "wrong_alias"))
    with pytest.raises(LlamaCppStartupError):
        await model.start()
    assert model.state == "failed"
    assert (tmp_path / "launches.txt").read_text(encoding="ascii") == "2"
    await model.close()


async def test_startup_failure_is_retried_once_then_succeeds(tmp_path: Path) -> None:
    model = LlamaCppReplyModel(_config(tmp_path, "startup_crash"))
    await model.start()
    try:
        assert model.state == "ready"
        assert (tmp_path / "launches.txt").read_text(encoding="ascii") == "2"
    finally:
        await model.close()


async def test_filtered_thinking_only_response_is_empty(tmp_path: Path) -> None:
    model = LlamaCppReplyModel(_config(tmp_path, "thinking_only"))
    await model.start()
    try:
        assert await _collect(model) == []
    finally:
        await model.close()


async def test_port_collision_fails_without_connecting_unknown_process(tmp_path: Path) -> None:
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        model = LlamaCppReplyModel(_config(tmp_path, port=occupied.getsockname()[1]))
        with pytest.raises(LlamaCppStartupError):
            await model.start()
        assert not (tmp_path / "launches.txt").exists()


@pytest.mark.parametrize("damage", ["server", "model", "size", "manifest"])
async def test_asset_validation_rejects_tampering(tmp_path: Path, damage: str) -> None:
    config = _config(tmp_path)
    manifest = json.loads(config.manifest_path.read_text(encoding="utf-8"))
    if damage == "server":
        manifest["llama_cpp"]["server_sha256"] = "0" * 64
    elif damage == "model":
        manifest["model"]["sha256"] = "0" * 64
    elif damage == "size":
        manifest["model"]["bytes"] += 1
    else:
        manifest = {}
    config.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(LlamaCppAssetError):
        await LlamaCppReplyModel(config).start()


async def test_errors_and_logs_do_not_contain_prompt_or_response(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    secret = "private-user-secret"
    model = LlamaCppReplyModel(_config(tmp_path, "http_400"))
    caplog.set_level(logging.DEBUG)
    await model.start()
    try:
        with pytest.raises(LlamaCppProtocolError) as caught:
            await _collect(model, _request(secret))
    finally:
        await model.close()
    output = caplog.text + str(caught.value)
    assert secret not in output
    assert "private-response-secret" not in output
    assert "你是秦未晞" not in output

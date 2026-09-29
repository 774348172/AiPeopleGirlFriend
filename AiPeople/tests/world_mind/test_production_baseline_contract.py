from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from runtime.gemma_nf4_assets import (
    GEMMA4_FULL_MIXED_MODEL_ID,
    GEMMA4_TARGETED_MODEL_ID,
    GEMMA4_V3_2611_MODEL_ID,
    GEMMA4_MODEL_ID,
)
from runtime.world_mind.model_gateway import GAME_REPLY
from tools import baiweixi_chat_app
from tools.run_gemma_nf4_runtime_smoke import _plain_gate, _plain_messages


def test_production_defaults_to_promoted_v3_adapter() -> None:
    assert baiweixi_chat_app.APP_MODEL_PROFILE == "primary"
    assert baiweixi_chat_app.GEMMA_MODEL_MANIFESTS["primary"].name == (
        "gemma4_nf4_runtime_manifest.json"
    )
    assert baiweixi_chat_app.GEMMA_MODEL_MANIFESTS["fallback"].name == (
        "gemma4_nf4_fallback_manifest.json"
    )
    assert GEMMA4_MODEL_ID == GEMMA4_V3_2611_MODEL_ID
    assert GEMMA4_FULL_MIXED_MODEL_ID != GEMMA4_TARGETED_MODEL_ID


def test_production_runtime_uses_dialogue_only_foreground() -> None:
    config = baiweixi_chat_app._runtime_config()
    assert config.foreground_protocol == "plain_reply_v1"
    assert GAME_REPLY in baiweixi_chat_app._gemma_profiles()
    assert baiweixi_chat_app.INTERACTIVE_BACKGROUND_EXECUTION_ENABLED is False


def test_smoke_contract_matches_training_reply_json() -> None:
    messages = _plain_messages()
    assert messages[-1] == {
        "role": "user",
        "content": "所以我还是星期五下午去，对吗？",
    }
    assert '{"reply":"中文对白"}' in messages[0]["content"]
    assert _plain_gate('{"reply":"不是，应该星期六上午十点去。"}')['passed'] is True
    assert _plain_gate('{"reply":"我不知道诊所为什么停诊。"}', "unknown")['passed'] is True


def _shutdown_request(host: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/launcher/shutdown",
            "headers": [],
            "client": (host, 49152),
            "server": ("127.0.0.1", 8767),
            "scheme": "http",
            "app": baiweixi_chat_app.app,
        }
    )


def test_launcher_shutdown_requires_loopback_and_random_token(monkeypatch) -> None:
    token = "a" * 64
    monkeypatch.setenv(baiweixi_chat_app.LAUNCHER_SHUTDOWN_TOKEN_ENV, token)
    server = SimpleNamespace(should_exit=False)
    baiweixi_chat_app.app.state.uvicorn_server = server
    try:
        with pytest.raises(HTTPException, match="not found") as wrong_token:
            asyncio.run(
                baiweixi_chat_app.launcher_shutdown(
                    _shutdown_request("127.0.0.1"),
                    "b" * 64,
                )
            )
        assert wrong_token.value.status_code == 404
        assert server.should_exit is False

        with pytest.raises(HTTPException, match="forbidden") as remote_client:
            asyncio.run(
                baiweixi_chat_app.launcher_shutdown(
                    _shutdown_request("192.0.2.5"),
                    token,
                )
            )
        assert remote_client.value.status_code == 403
        assert server.should_exit is False

        result = asyncio.run(
            baiweixi_chat_app.launcher_shutdown(
                _shutdown_request("127.0.0.1"),
                token,
            )
        )
        assert result == {"status": "stopping"}
        assert server.should_exit is True
    finally:
        baiweixi_chat_app.app.state.uvicorn_server = None

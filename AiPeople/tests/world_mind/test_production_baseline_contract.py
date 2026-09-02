from __future__ import annotations

from runtime.gemma_nf4_assets import (
    GEMMA4_FULL_MIXED_MODEL_ID,
    GEMMA4_TARGETED_MODEL_ID,
)
from runtime.world_mind.model_gateway import GAME_REPLY
from tools import baiweixi_chat_app
from tools.run_gemma_nf4_runtime_smoke import _plain_gate, _plain_messages


def test_production_defaults_to_full_mixed_adapter() -> None:
    assert baiweixi_chat_app.APP_MODEL_PROFILE == "primary"
    assert baiweixi_chat_app.GEMMA_MODEL_MANIFESTS["primary"].name == (
        "gemma4_nf4_runtime_manifest.json"
    )
    assert baiweixi_chat_app.GEMMA_MODEL_MANIFESTS["fallback"].name == (
        "gemma4_nf4_fallback_manifest.json"
    )
    assert GEMMA4_FULL_MIXED_MODEL_ID != GEMMA4_TARGETED_MODEL_ID


def test_production_runtime_uses_dialogue_only_foreground() -> None:
    config = baiweixi_chat_app._runtime_config()
    assert config.foreground_protocol == "plain_reply_v1"
    assert GAME_REPLY in baiweixi_chat_app._gemma_profiles()


def test_smoke_contract_has_no_structured_foreground_payload() -> None:
    messages = _plain_messages()
    assert messages[-1] == {
        "role": "user",
        "content": "所以我还是星期五下午去，对吗？",
    }
    assert all("json_schema" not in item["content"] for item in messages)
    assert _plain_gate("不是，应该星期六上午十点去。")['passed'] is True
    assert _plain_gate("我不知道诊所为什么停诊。", "unknown")['passed'] is True

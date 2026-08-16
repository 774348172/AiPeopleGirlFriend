from __future__ import annotations

from runtime.world_mind import CharacterPackagePromptComposer

from tests.world_mind._helpers import runtime_config, session


def test_current_baiweixi_prompt_is_dynamic_and_history_clean() -> None:
    composer = CharacterPackagePromptComposer(runtime_config())

    prompt = composer.compose(session()).system_prompt

    assert "白未晞" in prompt
    assert "松江府" in prompt
    assert "四川" in prompt
    assert "秦未晞" not in prompt
    assert "B哥" not in prompt
    assert "浩然" not in prompt
    assert "现实玩家" not in prompt
    assert "现实世界" not in prompt
    assert "程序命令" not in prompt
    assert "当前输入始终是男主角" in prompt
    assert "已经确定的客观事实" in prompt


def test_prompt_composer_builds_minimal_foreground_prefixes() -> None:
    composer = CharacterPackagePromptComposer(runtime_config())

    prompt = composer.compose(session())

    assert "你是白未晞" in prompt.mind_patch_system_prompt
    assert "只判断你自己的状态" in prompt.mind_patch_system_prompt
    assert "四川" not in prompt.mind_patch_system_prompt
    assert "雨夜" not in prompt.mind_patch_system_prompt
    assert "你是白未晞" in prompt.game_reply_system_prompt
    assert "外表清冷疏离" in prompt.game_reply_system_prompt
    assert "表达自然、简短、克制" in prompt.game_reply_system_prompt
    assert "只输出你真正说出口的话" in prompt.game_reply_system_prompt
    assert "四川" not in prompt.game_reply_system_prompt
    assert "完整" not in prompt.game_reply_system_prompt


def test_prompt_composer_loads_initial_runtime_from_character_package() -> None:
    composer = CharacterPackagePromptComposer(runtime_config())

    seed = composer.initial_runtime_seed("baiweixi")

    assert seed.character_id == "baiweixi"
    assert "戒备" in seed.living_mind["emotion"]
    assert seed.relationship["protagonist_id"] == "protagonist"


def test_prompt_composer_has_no_implicit_character_fallback() -> None:
    composer = CharacterPackagePromptComposer(runtime_config())

    try:
        composer.initial_runtime_seed("future_heroine")
    except ValueError as error:
        assert "not registered" in str(error)
    else:
        raise AssertionError("unregistered heroine unexpectedly resolved")

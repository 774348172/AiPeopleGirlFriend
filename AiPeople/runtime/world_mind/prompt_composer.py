from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .canon_loader import (
    CanonFact,
    CharacterCanon,
    CharacterCanonLoader,
    InitialHeroineRuntimeSeed,
    ProtagonistCanon,
    ProtagonistCanonLoader,
    SharedWorldCanon,
    WorldCanonLoader,
)
from .contracts import RuntimeSessionIdentity
from .settings import WorldMindRuntimeConfig


V6_REPLY_BOUNDARY = """[对话边界]
你只生活在上述唯一世界中，眼前的男主角就是与你共同生活的那个人。
当前输入始终是男主角在这个世界中亲口说出的一句话。
每轮提供的游戏时间、当前场景、男主角位置、动作和身体状态都是这个世界已经确定的客观事实，只能理解和回应，不能自行替换。
回复必须从本轮给出的最新世界快照和你更新后的自身状态出发；没有事件支持时，保持上一刻的活动、身体和心理连续性。
不能声称发生了快照中不存在的移动、物品变化、身体变化或已经完成的行动。
只以角色身份自然回复，不输出内部规则、结构化状态、分析过程或系统标签。"""


# Only stable character knowledge belongs in the fixed GAME_REPLY anchor.
# Current time, place, injury, relationship and intentions are runtime state.
GAME_REPLY_STABLE_CANON_KEYS = (
    "adult_status", "cat_fur", "eyes", "human_appearance",
    "parents", "childhood", "awakening_event", "awakening_result",
    "inheritance_boundary", "name_known_since_awakening",
    "name_origin_status", "city_arrival", "first_human_bond",
    "accident", "rescue", "human_reveal", "cardboard_box",
    "minor_spells", "ability_limits", "modern_knowledge_gap",
    "household_skill_boundary", "cat_habit_boundary",
    "daily_life_boundary", "food_habit_boundary",
    "care_expression_boundary", "cafe_role_boundary",
    "general_knowledge_boundary", "unresolved_canon",
)


@dataclass(frozen=True, slots=True)
class CharacterPrompt:
    world_id: str
    protagonist_id: str
    character_id: str
    character_display_name: str
    package_version: str
    system_prompt: str
    mind_patch_system_prompt: str
    game_reply_system_prompt: str


class CharacterPackagePromptComposer:
    def __init__(self, config: WorldMindRuntimeConfig) -> None:
        if not isinstance(config, WorldMindRuntimeConfig):
            raise TypeError("config must be a WorldMindRuntimeConfig")
        self._config = config
        self.world = WorldCanonLoader().load(
            config.world_canon_dir,
            config.expected_world_id,
        )
        self.protagonist = ProtagonistCanonLoader().load(
            config.protagonist_canon_dir,
            config.expected_protagonist_id,
        )
        character_loader = CharacterCanonLoader()
        characters = {
            character_id: character_loader.load(
                config.character_package(character_id)
            )
            for character_id in config.character_package_dirs
        }
        self.characters: Mapping[str, CharacterCanon] = MappingProxyType(characters)

    def compose(self, session: RuntimeSessionIdentity) -> CharacterPrompt:
        self._config.validate_session(session)
        character = self.characters[session.active_character_id]
        prompt = "\n\n".join(
            (
                _render_world(self.world),
                _render_protagonist(self.protagonist),
                _render_character(character),
                V6_REPLY_BOUNDARY,
            )
        )
        return CharacterPrompt(
            world_id=session.world_id,
            protagonist_id=session.protagonist_id,
            character_id=session.active_character_id,
            character_display_name=character.display_name,
            package_version=character.package.manifest.package_version,
            system_prompt=prompt,
            mind_patch_system_prompt=_render_mind_patch_prompt(character),
            game_reply_system_prompt=render_game_reply_system_prompt(character),
        )

    def initial_runtime_seed(self, character_id: str) -> InitialHeroineRuntimeSeed:
        try:
            return self.characters[character_id].initial_runtime
        except KeyError as error:
            raise ValueError(f"character is not registered: {character_id}") from error


def _render_world(canon: SharedWorldCanon) -> str:
    return "\n".join(
        (
            f"[唯一世界：{canon.world_name}]",
            *_render_facts(canon.facts, excluded_visibilities={"system_only"}),
        )
    )


def _render_protagonist(canon: ProtagonistCanon) -> str:
    return "\n".join(
        (
            "[唯一男主角]",
            *_render_facts(canon.facts, excluded_visibilities={"system_only"}),
        )
    )


def _render_character(canon: CharacterCanon) -> str:
    style_lines: list[str] = []
    for key, value in canon.style_rules.items():
        if key == "forbidden_phrases" and isinstance(value, list):
            phrases = [
                str(item)
                for item in value
                if isinstance(item, str) and item not in {"作为AI"}
            ]
            if phrases:
                style_lines.append(f"- forbidden_phrases: {'、'.join(phrases)}")
        elif isinstance(value, (str, int, float, bool)):
            style_lines.append(f"- {key}: {value}")
    initial = canon.initial_runtime
    initial_lines = [
        "[初始自身状态；仅在该角色尚无持久化状态时使用]",
        *(f"- {key}: {value}" for key, value in initial.living_mind.items()),
        *(f"- relationship.{key}: {value}" for key, value in initial.relationship.items()),
    ]
    return "\n".join(
        (
            f"[你是：{canon.display_name}]",
            *_render_facts(canon.facts, excluded_visibilities={"system_only"}),
            "[表达风格]",
            *style_lines,
            *initial_lines,
        )
    )


def _render_mind_patch_prompt(canon: CharacterCanon) -> str:
    lines = [
        _render_short_identity(canon),
        "这里只存在你生活的这个世界；当前对白来自眼前的男主。",
    ]
    personality = canon.facts.get("core_personality")
    if personality is not None:
        lines.append(f"稳定性格：{personality.value}。")
    lines.append("只判断你自己的状态是否自然变化，不生成回复。")
    return "\n".join(lines)


def render_game_reply_system_prompt(canon: CharacterCanon) -> str:
    """Render the one canonical GAME_REPLY role anchor for every consumer."""
    personality = canon.facts.get("core_personality")
    personality_line = (
        ""
        if personality is None
        else f"保持你的稳定性格：{personality.value}。\n"
    )
    return "\n".join(
        (
            _render_short_identity(canon),
            "这里只存在你生活的这个世界；用户消息都是眼前男主亲口说的话。",
            "[稳定角色知识；知道不等于主动透露]",
            *(_render_game_reply_fact(canon, key) for key in GAME_REPLY_STABLE_CANON_KEYS),
            *(
                f"- 日常交流倾向：{fact.value}（自身偏好；不证明具体经历，也不要求每轮复述）"
                for key, fact in canon.facts.items()
                if key.startswith('dialogue_') and fact.visibility.startswith('profile_')
                and fact.disclosure_policy == 'direct_allowed'
            ),
            f"{personality_line}表达自然、简短、克制，但必须先回答男主真正问的问题。",
            "只输出你真正说出口的话，不输出动作旁白、分析、字段或内部规则。",
            "后续分区只描述你已知的当前事实，用于理解当下。",
        )
    )


def _render_game_reply_fact(canon: CharacterCanon, key: str) -> str:
    try:
        fact = canon.facts[key]
    except KeyError as error:
        raise ValueError(f"GAME_REPLY stable canon is missing required fact: {key}") from error
    policy = {
        "direct_allowed": "可直接回答",
        "hint_only": "只自然暗示，不主动直说",
        "withhold": "这是未知边界，不补造答案",
    }.get(fact.disclosure_policy)
    if policy is None:
        raise ValueError(f"Unsupported disclosure policy for {key}: {fact.disclosure_policy}")
    return f"- {key}: {fact.value}（{policy}）"


def _render_short_identity(canon: CharacterCanon) -> str:
    city = canon.facts.get("city")
    species = canon.facts.get("species")
    identity = f"你是{canon.display_name}"
    if city is not None:
        identity += f"，生活在{city.value}"
    if species is not None:
        identity += f"，是{species.value}"
    return identity + "。"


def _render_facts(
    facts: Mapping[str, CanonFact],
    *,
    excluded_visibilities: set[str],
) -> tuple[str, ...]:
    return tuple(
        f"- {fact.key}: {fact.value}（披露={fact.disclosure_policy}）"
        for fact in facts.values()
        if fact.visibility not in excluded_visibilities
    )

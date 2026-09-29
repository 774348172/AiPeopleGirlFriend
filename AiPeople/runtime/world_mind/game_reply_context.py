"""Shared rendering contract for the player-visible GAME_REPLY path.

The renderer is deliberately data-only: it does not infer facts or decide
whether a player report is authoritative. Those decisions belong to the game
state boundary before this function is called.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any


GAME_REPLY_CONTEXT_V1 = "GAME_REPLY_CONTEXT_V1"

GAME_REPLY_EVIDENCE_BOUNDARY_V1 = """[回答依据约束]
回答具体事实时，优先依据[当前权威事实]；[相关记忆]只能作为带有明确主体和时间关系的辅助证据。
当前游戏事实和男主本轮已经确认的更新优先于冲突的旧记忆；玩家的提问、猜测或反问不能单独覆盖客观事实。
男主本轮明确告知或纠正的新事实优先于冲突的旧记忆；男主的提问、猜测或反问不能覆盖已有事实。
已标记为stale、已取消或已失效的事实不得复活为当前事实；标记为unknown或没有直接证据时，回答不知道。
不得用常识、猜测、角色设定或看似合理的细节补全答案，不得新增未授权事实、替男主行动或输出动作旁白。
不得用常识、猜测、角色设定或看似合理的细节补全答案，也不得声称自己查询、查看或确认过。"""

_PROTAGONIST_DIALOGUE_ACTORS = frozenset({"protagonist", "男主", "玩家", "user"})
_HEROINE_DIALOGUE_ACTORS = frozenset({"heroine", "assistant"})


def parse_game_reply_dialogue_lines(
    lines: Sequence[str],
) -> tuple[tuple[str, str], ...]:
    """Turn persisted ``说话人：原话`` lines into typed dialogue turns.

    Legacy generator scenarios persist dialogue in one text field, while the
    runtime already has actor/text pairs.  Parsing it here ensures both paths
    use the same participant boundary before rendering a prompt.
    """

    turns: list[tuple[str, str]] = []
    for raw in lines:
        value = str(raw).strip()
        if not value:
            continue
        actor, separator, content = value.partition("：")
        if not separator:
            actor, content = "protagonist", value
        actor, content = actor.strip(), content.strip()
        if not actor or not content:
            raise ValueError("dialogue lines require a speaker and non-empty text")
        turns.append((actor, content))
    return tuple(turns)


def partition_game_reply_dialogue(
    turns: Sequence[tuple[str, str]],
    *,
    character_display_name: str,
) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    """Keep player/heroine chat roles and project others as named evidence.

    The chat template only has ``user`` and ``assistant`` roles.  Assigning a
    third party to ``assistant`` falsely attributes their words to the
    heroine, and also discards the speaker name.  Third-party speech is
    therefore kept in the system context as a quoted, sourced evidence line.
    """

    heroine_actors = _HEROINE_DIALOGUE_ACTORS | {character_display_name.strip()}
    chat_messages: list[tuple[str, str]] = []
    third_party_evidence: list[str] = []
    for actor, text in turns:
        speaker = str(actor).strip()
        utterance = str(text).strip()
        if not speaker or not utterance:
            raise ValueError("dialogue turns require a speaker and non-empty text")
        if speaker in _PROTAGONIST_DIALOGUE_ACTORS:
            chat_messages.append(("user", utterance))
        elif speaker in heroine_actors:
            chat_messages.append(("assistant", utterance))
        else:
            third_party_evidence.append(
                f"说话人：{speaker}；原话：{utterance}；来源：近期对话"
            )
    return tuple(chat_messages), tuple(third_party_evidence)


def build_game_reply_chat_messages(
    turns: Sequence[tuple[str, str]],
    *,
    character_display_name: str,
    current_protagonist_utterance: str,
) -> tuple[tuple[str, str], ...]:
    """Produce alternating template messages without impersonating outsiders.

    A third-party turn can sit between two protagonist turns.  Once that turn
    has correctly moved to evidence, the surrounding user turns become
    adjacent, which Gemma's chat template rejects.  Merge only such adjacent
    messages, retaining a visible boundary for the current player utterance.
    """

    current = current_protagonist_utterance.strip()
    if not current:
        raise ValueError("current_protagonist_utterance must be non-empty")
    dialogue_messages, _ = partition_game_reply_dialogue(
        turns,
        character_display_name=character_display_name,
    )
    messages: list[tuple[str, str]] = []
    for role, text in dialogue_messages:
        if messages and messages[-1][0] == role:
            previous_role, previous_text = messages[-1]
            messages[-1] = (previous_role, f"{previous_text}\n\n[同一角色继续]\n{text}")
        else:
            messages.append((role, text))
    if messages and messages[-1][0] == "user":
        previous_role, previous_text = messages[-1]
        messages[-1] = (previous_role, f"{previous_text}\n\n[本轮男主]\n{current}")
    else:
        messages.append(("user", current))
    return tuple(messages)


def render_game_reply_context_v1(
    *,
    character_display_name: str,
    game_time: datetime | str,
    location: str,
    scene_text: str,
    protagonist_location: str,
    protagonist_activity: str,
    protagonist_body: str,
    heroine: Mapping[str, Any],
    relationship: Mapping[str, Any],
    item_states: Mapping[str, Any] | None = None,
    authoritative_facts: Sequence[str] = (),
    unknown_facts: Sequence[str] = (),
    player_fact_reports: Sequence[str] = (),
    memories: Sequence[str] = (),
    third_party_dialogue_evidence: Sequence[str] = (),
    pending_actions: Sequence[str] = (),
    game_feedback: Sequence[str] = (),
    approved_actions: Sequence[str] = (),
    evidence_boundary: str = GAME_REPLY_EVIDENCE_BOUNDARY_V1,
) -> str:
    """Render the canonical current-context blocks in a stable order."""

    if not isinstance(character_display_name, str) or not character_display_name.strip():
        raise ValueError("character_display_name must be non-empty")
    fact_lines = list(authoritative_facts)
    for key, value in (item_states or {}).items():
        fact_lines.append(f"{key}：{value}（当前有效，来源：游戏状态）")
    for value in game_feedback:
        fact_lines.append(f"{value}（当前有效，来源：游戏反馈）")
    if not fact_lines:
        fact_lines.append("无已登记的当前权威事实")

    return "\n".join(
        (
            "[角色锚]",
            f"你是{character_display_name}。",
            "",
            "[当前权威事实]",
            *_bullet_lines(fact_lines),
            "",
            "[本轮玩家事实报告]",
            *_bullet_lines(player_fact_reports, empty="无") ,
            "",
            "[明确未知]",
            *_bullet_lines(unknown_facts, empty="无"),
            "",
            "[当前世界]",
            f"时间：{_format_time(game_time)}",
            f"地点：{location}",
            f"场景：{scene_text}",
            (
                f"男主：在{protagonist_location}，正在{protagonist_activity}；"
                f"身体：{protagonist_body or '无额外身体状态'}"
            ),
            "",
            "[你此刻的状态]",
            f"形态：{heroine.get('form', '')}",
            f"身体：{heroine.get('body', '')}",
            f"情绪：{heroine.get('emotion', '')}",
            f"注意：{heroine.get('attention', '')}",
            f"活动：{heroine.get('current_activity', '')}",
            f"意图：{heroine.get('immediate_intent', '')}",
            (
                f"关系：{relationship.get('stage', '')}；"
                f"{relationship.get('trust', '')}；"
                f"{relationship.get('unresolved_tension', '')}"
            ),
            "",
            "[进行中的游戏动作]",
            *_bullet_lines(pending_actions, empty="无"),
            "",
            "[相关记忆]",
            *_bullet_lines(memories, empty="无"),
            "",
            "[第三方对话证据]",
            *_bullet_lines(third_party_dialogue_evidence, empty="无"),
            "",
            "[允许表达的动作]",
            *_bullet_lines(approved_actions, empty="无"),
            "",
            evidence_boundary.strip(),
        )
    )


def _bullet_lines(values: Sequence[str], *, empty: str = "无已登记事实") -> tuple[str, ...]:
    cleaned = tuple(str(value).strip() for value in values if str(value).strip())
    return tuple(f"- {value}" for value in cleaned) if cleaned else (empty,)


def _format_time(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return f"第{value.day}天 {value:%H:%M}"
    return str(value)

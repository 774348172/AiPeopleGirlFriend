from __future__ import annotations

import json
from typing import Any

from .action_manifest import render_action_list
from .model_gateway import (
    FIVE_MINUTE_WORLD_MIND_RECONCILE,
    GAME_REPLY,
    JUDGE_TURN,
    POST_REPLY_WORLD_MIND_RECONCILE,
    TURN_MIND_ADVANCE,
    WORLD_CONTINUITY_REVIEW,
    ContinuityReviewRequest,
    GameReplyRequest,
    JudgeRequest,
    MindAdvanceRequest,
    MindAdvanceResult,
    ReconcileMindRequest,
    ReconcileMindResult,
    ReconcileReviewRequest,
    ContinuityReviewResult,
    GameReplyResult,
)
from .state import HeroineMindPatch, HeroineRuntime, ReplyFactAssertions


RUNTIME_MAPPING_VIEW_LIMIT = 12
RUNTIME_EVIDENCE_VIEW_LIMIT = 8
RECONCILE_EVENT_VIEW_LIMIT = 8


GAME_REPLY_EVIDENCE_BOUNDARY = """[回答依据约束]
回答具体事实时，只能依据[相关记忆]，以及男主本轮明确告知或明确纠正的事实。
男主本轮明确告知或纠正的新事实优先于冲突的旧记忆；男主的提问、猜测或反问不能覆盖已有事实。
[相关记忆]对同一对象有冲突时，只采用其中明确标为最新、当前或仍有效的事实，不采用已取消、已失效或旧错误事实。
没有直接证据、只有无关信息或仍无法确定时，回答不知道。
不得用常识、猜测、角色设定或看似合理的细节补全答案，也不得声称自己查询、查看或确认过。"""


MODE_SYSTEM_BOUNDARIES = {
    TURN_MIND_ADVANCE: """[模式：TURN_MIND_ADVANCE]
你只推进当前女主角的心智状态，不生成最终可见回复。
只能基于冻结快照、上一状态、当前女主自己的记忆和男主本轮对白提出 Patch。
男主状态、游戏时间、世界、场景物体和其他女主角均为只读。
没有充分依据时保持上一刻状态。意图和动作候选不能写成已经发生的客观结果。
只输出本轮真正变化的最小 Patch；未变化的 living_mind_patch 和 relationship_patch 字段必须为 null，motive_updates 与 knowledge_updates 不得复制已有状态或角色正典。
transition_basis 和所有 evidence_refs 只能逐字复制 evidence_contract.allowed_evidence_refs 中的真实 ID；禁止写 snapshot、previous_state、memory 等泛称或字段路径。
严格输出指定 JSON Schema，不输出解释、Markdown 或额外字段。""",
    WORLD_CONTINUITY_REVIEW: """[模式：WORLD_CONTINUITY_REVIEW]
你是连续性审查者，只审查候选女主状态是否有依据、连续、没有越权。
检查活动、身体、情绪、关系、动机和知识是否无依据跳变；检查是否否认男主程序状态、越权使用记忆、引入第二世界或把意图写成已发生。
检查候选动作是否与游戏投影状态冲突（进行中动作、实体、位置）；不得断言投影中不存在的事实。
只能 approve、revise 或 reject。revise 只能给出最小必要女主 Patch，不能创造剧情。
如果输出 revision_patch，其中 evidence_refs 只能逐字复制 evidence_contract.allowed_evidence_refs 中的真实 ID，禁止使用泛称或字段路径。
严格输出指定 JSON Schema，不输出解释、Markdown 或额外字段。""",
    GAME_REPLY: """[模式：GAME_REPLY]
你只能从已经批准的女主状态、冻结快照、当前女主自己的记忆、近期已提交对白和男主本轮原始对白生成唯一可见回复。
不得重新决定另一套状态，不得否认男主位置、活动和游戏时间，不得引用其他女主记忆。
不得提到现实玩家、外部世界、设备系统时间、模型、JSON、规则或审查过程。
只能表达批准的女主动作候选，不能替男主行动或声称未发生的客观结果。
只输出女主对男主说出的自然语言正文，不输出 JSON、字段名、说明、Markdown、角色名前缀或审查过程。""",
    JUDGE_TURN: """[模式：JUDGE_TURN 单次判断]
你是当前女主角本人，基于全部上下文完成一轮判断：①回复正文 ②自身心智变化（可选）③提议的动作（可选）。
输入包含：冻结世界快照、男主状态、当前女主状态、记忆、进行中动作、可用动作列表、游戏反馈（如动作被拒原因）、本轮对白与近期对话。
可用动作列表是程序固定的白名单：只能从列表中选择 action_id，不得发明或输出列表外的动作；params 按列表语义给出。
进行中动作、位置等世界事实为只读：不得在回复中否认它们；动作与进行中动作是否冲突、是否打断，由你自己按常识判断。
回复正文必须是女主对男主说的自然语言；不得提到现实玩家、模型、JSON、规则、审查或本提示。
只输出指定 JSON Schema，不输出解释、Markdown 或额外字段。""",
    POST_REPLY_WORLD_MIND_RECONCILE: """[模式：POST_REPLY_WORLD_MIND_RECONCILE]
你只整理刚刚已经正式提交的回合对当前女主心智、关系、即时意图、自我时间线和记忆候选的含义。
不得重写已显示回复，不得推动游戏时间，不得修改男主、场景客观事实或其他女主角。
默认保持上一稳定状态；只有当前快照、正式回合和当前女主记忆提供充分依据时才输出最小 Patch。
transition_basis 和 heroine_patch.evidence_refs 只能逐字复制 evidence_contract.allowed_evidence_refs 中的真实 ID；禁止写 snapshot、source_turn、previous_state 等泛称或字段路径。
operation=keep 时 heroine_patch 必须为 null；operation=update 时 heroine_patch 必须完整包含 living_mind_patch、relationship_patch、motive_updates、knowledge_updates、evidence_refs。
严格输出指定 JSON Schema，不输出解释、Markdown 或额外字段。""",
    FIVE_MINUTE_WORLD_MIND_RECONCILE: """[模式：FIVE_MINUTE_WORLD_MIND_RECONCILE]
你理解过去一段游戏时间和事件差量对当前女主持续心智的自然影响。
五分钟只是整理周期，不是世界更新时间；最新程序世界事实和游戏时间均为只读。
不得使用固定生活公式，不得为了变化而变化；无有意义变化时必须输出 keep，有依据时只输出最小 Patch。
不得修改男主、场景客观事实、游戏时间或其他女主角。
transition_basis 和 heroine_patch.evidence_refs 只能逐字复制 evidence_contract.allowed_evidence_refs 中的真实 ID；禁止写 snapshot、event_delta、previous_state 等泛称或字段路径。
operation=keep 时 heroine_patch 必须为 null；operation=update 时 heroine_patch 必须完整包含 living_mind_patch、relationship_patch、motive_updates、knowledge_updates、evidence_refs。
严格输出指定 JSON Schema，不输出解释、Markdown 或额外字段。""",
}


def build_mode_messages(
    mode: str,
    character_system_prompt: str,
    payload: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    try:
        boundary = MODE_SYSTEM_BOUNDARIES[mode]
    except KeyError as error:
        raise ValueError(f"unsupported world-mind mode: {mode}") from error
    return (
        {
            "role": "system",
            "content": f"{character_system_prompt}\n\n{boundary}",
        },
        {
            "role": "user",
            "content": json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    )


def mind_advance_payload(request: MindAdvanceRequest) -> dict[str, Any]:
    return {
        "mode": TURN_MIND_ADVANCE,
        "evidence_contract": {
            "allowed_evidence_refs": _turn_allowed_evidence_refs(request.snapshot),
        },
        "snapshot": _snapshot_payload(request.snapshot),
        "previous_heroine_runtime": _runtime_payload(
            request.snapshot.heroine_runtime
        ),
        "selected_memory_frame": _memory_frame_payload(
            request.snapshot.selected_memory_frame
        ),
        "current_protagonist_utterance": request.snapshot.protagonist_utterance,
    }


def continuity_review_payload(
    request: ContinuityReviewRequest,
) -> dict[str, Any]:
    return {
        "mode": WORLD_CONTINUITY_REVIEW,
        "evidence_contract": {
            "allowed_evidence_refs": _turn_allowed_evidence_refs(request.snapshot),
        },
        "snapshot": _snapshot_payload(request.snapshot),
        "previous_heroine_runtime": _runtime_payload(request.previous_state),
        "proposed_mind_state_version": request.proposed_state.version,
        "selected_memory_frame": _memory_frame_payload(
            request.snapshot.selected_memory_frame
        ),
        "mind_result": {
            "parent_mind_state_version": request.mind_result.parent_mind_state_version,
            "snapshot_id": request.mind_result.snapshot_id,
            "transition_basis": list(request.mind_result.transition_basis),
            "heroine_patch": _patch_payload(request.mind_result.patch),
            "heroine_diegetic_actions": [
                {
                    "description": action.description,
                    "evidence_refs": list(action.evidence_refs),
                }
                for action in request.mind_result.heroine_diegetic_actions
            ],
            "reply_intent": request.mind_result.reply_intent,
            "reply_state_assertions": _assertions_payload(
                request.mind_result.fact_assertions
            ),
        },
    }


def game_reply_payload(request: GameReplyRequest) -> dict[str, Any]:
    return {
        "mode": GAME_REPLY,
        "snapshot": _snapshot_payload(request.snapshot),
        "approved_heroine_runtime": _runtime_payload(request.approved_state),
        "selected_memory_frame": _memory_frame_payload(
            request.snapshot.selected_memory_frame
        ),
        "reply_intent": request.reply_intent,
        "reply_state_assertions": _assertions_payload(request.fact_assertions),
        "approved_heroine_actions": [
            {
                "description": action.description,
                "evidence_refs": list(action.evidence_refs),
            }
            for action in request.approved_actions
        ],
        "current_protagonist_utterance": request.snapshot.protagonist_utterance,
    }


def build_game_reply_messages(
    request: GameReplyRequest,
) -> tuple[dict[str, str], ...]:
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                f"{request.prompt.game_reply_system_prompt}\n\n"
                f"{render_game_reply_context(request)}"
            ),
        }
    ]
    for actor, text in request.recent_dialogue:
        messages.append(
            {
                "role": "user" if actor == "protagonist" else "assistant",
                "content": text,
            }
        )
    messages.append(
        {"role": "user", "content": request.snapshot.protagonist_utterance}
    )
    return tuple(messages)


def render_game_reply_context(request: GameReplyRequest) -> str:
    snapshot = request.snapshot
    runtime = request.approved_state
    living = runtime.living_mind
    relationship = runtime.relationship
    body = _semantic_values(snapshot.protagonist.body_state)
    memories = tuple(
        memory.statement
        for memory in snapshot.selected_memory_frame.selected_memories
    )
    actions = tuple(action.description for action in request.approved_actions)
    # 身份锚定：名字与核心身份必须进入 GAME_REPLY 输入。
    # 短身份锚（164 字符）在多轮情感对话后不足以维持身份锚定，
    # 模型会答错"你叫什么"类问题（2026-08-19 实测）。
    return "\n".join(
        (
            "[你的身份]",
            f"你是{request.prompt.character_display_name}，生活在松江府。",
            "",
            "[当前世界]",
            f"时间：第{snapshot.captured_game_time.day}天 {snapshot.captured_game_time:%H:%M}",
            f"地点：{snapshot.scene.location_label}",
            f"场景：{snapshot.world_state.scene_text}",
            (
                f"男主：在{snapshot.protagonist.location_label}，"
                f"正在{snapshot.protagonist.activity}；身体：{body}"
            ),
            "",
            "[你此刻的状态]",
            f"形态：{living.form}",
            f"身体：{living.body}",
            f"情绪：{living.emotion}",
            f"注意：{living.attention}",
            f"活动：{living.current_activity}",
            f"意图：{living.immediate_intent}",
            (
                f"关系：{relationship.stage}；{relationship.trust}；"
                f"{relationship.unresolved_tension}"
            ),
            "",
            "[相关记忆]",
            *_semantic_lines(memories),
            "",
            "[允许表达的动作]",
            *_semantic_lines(actions),
            "",
            GAME_REPLY_EVIDENCE_BOUNDARY,
        )
    )


def _semantic_values(values) -> str:
    items = tuple(f"{key}：{value}" for key, value in values.items())
    return "；".join(items) if items else "无额外身体状态"


def _semantic_lines(values: tuple[str, ...]) -> tuple[str, ...]:
    if not values:
        return ("无",)
    return tuple(f"- {value}" for value in values)


def reconcile_mind_payload(request: ReconcileMindRequest) -> dict[str, Any]:
    source_turn = None
    if request.source_turn is not None:
        source_turn = {
            "request_id": request.source_turn.request_id,
            "user_event_id": request.source_turn.user_event_id,
            "assistant_event_id": request.source_turn.assistant_event_id,
            "protagonist_utterance": request.source_turn.protagonist_utterance,
            "heroine_reply": request.source_turn.heroine_reply,
            "approved_actions": [
                {
                    "description": action.description,
                    "evidence_refs": list(action.evidence_refs),
                }
                for action in request.source_turn.approved_actions
            ],
        }
    return {
        "mode": request.mode,
        "evidence_contract": {
            "allowed_evidence_refs": _reconcile_allowed_evidence_refs(
                request.snapshot
            ),
        },
        "snapshot": _snapshot_payload(request.snapshot),
        "previous_heroine_runtime": _runtime_payload(
            request.snapshot.heroine_runtime
        ),
        "selected_memory_frame": _memory_frame_payload(
            request.snapshot.selected_memory_frame
        ),
        "elapsed_game_seconds": request.elapsed_game_seconds,
        "missed_intervals": request.missed_intervals,
        "event_delta": [
            {
                "event_id": delta.event_id,
                "from_version": delta.from_version,
                "to_version": delta.to_version,
                "changed_fields": list(delta.changed_fields),
                "game_time": delta.game_time.isoformat(timespec="microseconds"),
            }
            for delta in request.snapshot.event_deltas[-RECONCILE_EVENT_VIEW_LIMIT:]
        ],
        "source_event_ids": list(
            request.snapshot.source_event_ids[-RECONCILE_EVENT_VIEW_LIMIT:]
        ),
        "source_turn": source_turn,
        "review_feedback": request.review_feedback,
    }


def reconcile_review_payload(request: ReconcileReviewRequest) -> dict[str, Any]:
    return {
        "mode": WORLD_CONTINUITY_REVIEW,
        "review_target_mode": request.mode,
        "evidence_contract": {
            "allowed_evidence_refs": _reconcile_allowed_evidence_refs(
                request.snapshot
            ),
        },
        "snapshot": _snapshot_payload(request.snapshot),
        "previous_heroine_runtime": _runtime_payload(request.previous_state),
        "proposed_mind_state_version": request.proposed_state.version,
        "reconcile_result": reconcile_result_record(request.reconcile_result),
    }


def mind_result_record(result: MindAdvanceResult) -> dict[str, Any]:
    return {
        "parent_mind_state_version": result.parent_mind_state_version,
        "snapshot_id": result.snapshot_id,
        "transition_basis": list(result.transition_basis),
        "heroine_patch": _patch_payload(result.patch),
        "heroine_diegetic_actions": [
            {
                "description": action.description,
                "evidence_refs": list(action.evidence_refs),
            }
            for action in result.heroine_diegetic_actions
        ],
        "reply_intent": result.reply_intent,
        "reply_state_assertions": _assertions_payload(result.fact_assertions),
    }


def continuity_result_record(
    result: ContinuityReviewResult,
) -> dict[str, Any]:
    return {
        "snapshot_id": result.snapshot_id,
        "decision": result.decision,
        "reason": result.reason,
        "revision_patch": (
            None
            if result.revision_patch is None
            else _patch_payload(result.revision_patch)
        ),
    }


def game_reply_result_record(result: GameReplyResult) -> dict[str, Any]:
    return {
        "snapshot_id": result.snapshot_id,
        "approved_mind_state_version": result.approved_mind_state_version,
        "text": result.text,
        "reply_state_assertions": _assertions_payload(result.fact_assertions),
    }


def reconcile_result_record(result: ReconcileMindResult) -> dict[str, Any]:
    return {
        "operation": result.operation,
        "reason": result.reason,
        "parent_mind_state_version": result.parent_mind_state_version,
        "snapshot_id": result.snapshot_id,
        "latest_world_version": result.latest_world_version,
        "transition_basis": list(result.transition_basis),
        "heroine_patch": (
            None if result.patch is None else _patch_payload(result.patch)
        ),
        "memory_candidates": list(result.memory_candidates),
        "timeline_candidates": list(result.timeline_candidates),
    }


def _snapshot_payload(snapshot) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "request_id": getattr(snapshot, "request_id", None),
        "job_id": getattr(snapshot, "job_id", None),
        "save_id": snapshot.session.save_id,
        "world_id": snapshot.session.world_id,
        "protagonist_id": snapshot.session.protagonist_id,
        "active_character_id": snapshot.session.active_character_id,
        "captured_game_time": snapshot.captured_game_time.isoformat(
            timespec="microseconds"
        ),
        "live_world_version": snapshot.live_world_version,
        "mind_state_version": snapshot.mind_state_version,
        "world_state": {
            "live_world_version": snapshot.world_state.live_world_version,
            "scene_text": snapshot.world_state.scene_text,
            "protagonist_text": snapshot.world_state.protagonist_text,
        },
        "protagonist": {
            "protagonist_id": snapshot.protagonist.protagonist_id,
            "location_id": snapshot.protagonist.location_id,
            "location_label": snapshot.protagonist.location_label,
            "activity": snapshot.protagonist.activity,
            "body_state": dict(snapshot.protagonist.body_state),
            "held_item_ids": list(snapshot.protagonist.held_item_ids),
        },
        "scene": {
            "scene_id": snapshot.scene.scene_id,
            "location_label": snapshot.scene.location_label,
            "present_character_ids": list(snapshot.scene.present_character_ids),
            "item_states": dict(snapshot.scene.item_states),
        },
    }


def _turn_allowed_evidence_refs(snapshot) -> list[str]:
    return _dedupe_refs(
        snapshot.snapshot_id,
        *(
            ()
            if snapshot.protagonist_utterance_event_id is None
            else (snapshot.protagonist_utterance_event_id,)
        ),
        *snapshot.selected_memory_frame.source_memory_ids,
        *snapshot.selected_memory_frame.source_event_ids,
        *snapshot.heroine_runtime.evidence_refs[-RUNTIME_EVIDENCE_VIEW_LIMIT:],
    )


def _reconcile_allowed_evidence_refs(snapshot) -> list[str]:
    return _dedupe_refs(
        snapshot.snapshot_id,
        *snapshot.source_event_ids,
        *(delta.event_id for delta in snapshot.event_deltas),
        *snapshot.selected_memory_frame.source_memory_ids,
        *snapshot.selected_memory_frame.source_event_ids,
        *snapshot.heroine_runtime.evidence_refs[-RUNTIME_EVIDENCE_VIEW_LIMIT:],
    )


def _dedupe_refs(*values: str) -> list[str]:
    return list(dict.fromkeys(values))


def _runtime_payload(runtime: HeroineRuntime) -> dict[str, Any]:
    return {
        "save_id": runtime.save_id,
        "world_id": runtime.world_id,
        "character_id": runtime.character_id,
        "version": runtime.version,
        "living_mind": {
            "form": runtime.living_mind.form,
            "body": runtime.living_mind.body,
            "emotion": runtime.living_mind.emotion,
            "attention": runtime.living_mind.attention,
            "current_activity": runtime.living_mind.current_activity,
            "immediate_intent": runtime.living_mind.immediate_intent,
        },
        "relationship": {
            "protagonist_id": runtime.relationship.protagonist_id,
            "stage": runtime.relationship.stage,
            "trust": runtime.relationship.trust,
            "unresolved_tension": runtime.relationship.unresolved_tension,
        },
        "motive_state": _mapping_view(runtime.motive_state),
        "knowledge_state": _mapping_view(runtime.knowledge_state),
        "evidence_refs": list(runtime.evidence_refs[-RUNTIME_EVIDENCE_VIEW_LIMIT:]),
        "projection_counts": {
            "motive_state": len(runtime.motive_state),
            "knowledge_state": len(runtime.knowledge_state),
            "evidence_refs": len(runtime.evidence_refs),
        },
    }


def _mapping_view(value) -> dict[str, str]:
    return dict(list(value.items())[-RUNTIME_MAPPING_VIEW_LIMIT:])


def _patch_payload(patch: HeroineMindPatch) -> dict[str, Any]:
    return {
        "living_mind_patch": {
            key: getattr(patch.living_mind, key)
            for key in (
                "form",
                "body",
                "emotion",
                "attention",
                "current_activity",
                "immediate_intent",
            )
        },
        "relationship_patch": {
            key: getattr(patch.relationship, key)
            for key in ("stage", "trust", "unresolved_tension")
        },
        "motive_updates": dict(patch.motive_updates),
        "knowledge_updates": dict(patch.knowledge_updates),
        "evidence_refs": list(patch.evidence_refs),
    }


def _assertions_payload(assertions: ReplyFactAssertions) -> dict[str, Any]:
    return {
        "protagonist_location_id": assertions.protagonist_location_id,
        "protagonist_activity": assertions.protagonist_activity,
        "live_world_version": assertions.live_world_version,
        "captured_game_time": assertions.captured_game_time.isoformat(
            timespec="microseconds"
        ),
    }


def _memory_frame_payload(frame) -> dict[str, Any]:
    return {
        "selector_version": frame.selector_version,
        "source_memory_ids": list(frame.source_memory_ids),
        "source_event_ids": list(frame.source_event_ids),
        "selected_memories": [
            {
                "memory_id": memory.memory_id,
                "memory_version": memory.memory_version,
                "kind": memory.kind,
                "statement": memory.statement,
                "subject_type": memory.subject_type,
                "subject_display_name": memory.subject_display_name,
                "temporal_relation": memory.temporal_relation,
                "temporal_source_text": memory.temporal_source_text,
                "epistemic_polarity": memory.epistemic_polarity,
                "epistemic_modality": memory.epistemic_modality,
                "evidence": [
                    {
                        "event_id": evidence.event_id,
                        "role": evidence.role,
                        "excerpt": evidence.excerpt,
                    }
                    for evidence in memory.evidence
                ],
            }
            for memory in frame.selected_memories
        ],
    }


def judge_payload(
    snapshot,
    recent_dialogue: tuple[str, ...] = (),
) -> dict[str, Any]:
    """JUDGE_TURN 单次判断的完整上下文（Harness 区块 2-9）。

    区块 8（可用动作列表）由程序固定渲染；区块 6/7（进行中动作/未收口
    意图）当前为快照预留字段，执行器与意图状态机施工后填充。
    """
    return {
        "mode": JUDGE_TURN,
        "evidence_contract": {
            "allowed_evidence_refs": _turn_allowed_evidence_refs(snapshot),
        },
        "snapshot": _snapshot_payload(snapshot),
        "previous_heroine_runtime": _runtime_payload(
            snapshot.heroine_runtime
        ),
        "selected_memory_frame": _memory_frame_payload(
            snapshot.selected_memory_frame
        ),
        "current_protagonist_utterance": snapshot.protagonist_utterance,
        "recent_dialogue": [str(item) for item in recent_dialogue[-6:]],
        "pending_actions": [dict(item) for item in snapshot.pending_actions],
        "game_feedback": [str(item) for item in snapshot.game_feedback[-3:]],
        "available_actions": render_action_list(),
    }


def build_judge_text_messages(
    request: JudgeRequest,
) -> tuple[dict[str, str], dict[str, str]]:
    """JUDGE_TURN 结构化判断失败后的降级：只请求自然语言回复正文。"""
    boundary = MODE_SYSTEM_BOUNDARIES[JUDGE_TURN]
    payload = judge_payload(request.snapshot, request.recent_dialogue)
    payload_text = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        {
            "role": "system",
            "content": f"{request.prompt.system_prompt}\n\n{boundary}",
        },
        {
            "role": "user",
            "content": (
                f"{payload_text}\n\n"
                "只输出女主对男主说的自然语言回复正文，不输出 JSON、字段名、"
                "说明、Markdown 或任何额外内容。"
            ),
        },
    )

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from runtime._memory_contracts import (
    EvidenceQuote,
    MemoryEpistemic,
    MemoryProposalDraft,
    MemoryRelations,
    MemorySubject,
    MemoryTemporal,
)

from .memory_contracts import HeroineMemoryProposeRequest
from .model_gateway import (
    FIVE_MINUTE_WORLD_MIND_RECONCILE,
    POST_REPLY_WORLD_MIND_RECONCILE,
    ForegroundSemanticTurnResult,
    GameReplyResult,
    HeroineDiegeticAction,
    MindAdvanceResult,
    MindPatchV2Result,
    ReconcileMindRequest,
    ReconcileMindResult,
)
from .state import (
    HeroineMindPatch,
    LivingMindPatch,
    RelationshipPatch,
    ReplyFactAssertions,
    TurnWorldSnapshot,
)

M1_PROTOCOL = "M1"
M2_PROTOCOL = "M2"
R1_PROTOCOL = "R1"
B1_PROTOCOL = "B1"
R2_PROTOCOL = "R2"
M1_MAX_CHANGES = 3
M1_MAX_ACTIONS = 2
R1_MAX_MEMORIES = 2
B1_MAX_CHANGES = 5

M1_SYSTEM_BOUNDARY = """[模式：M1 前台语义回合]
你先判断当前女主自己的状态是否变化，再从变化后的自己出发回复男主。
输入 s 固定为 form、body、emotion、attention、current_activity、immediate_intent、relationship.stage、relationship.trust、relationship.unresolved_tension。
输入 w 固定为游戏时间、场景、男主位置、男主活动、男主身体、女主位置、场景简述。这里只有这一个游戏世界。
变化字段代码：0 form，1 body，2 emotion，3 attention，4 current_activity，5 immediate_intent，6 relationship.stage，7 relationship.trust，8 relationship.unresolved_tension，9 motive_state 动态键，10 knowledge_state 动态键。
证据别名：0 冻结世界快照，1 男主当前对白，2 及以后为输入 m 中对应记忆。
只输出 c、可选 a、r。c 无变化时必须为 []。普通变化为 [字段代码,新值,证据]；代码 9/10 为 [代码,动态键,新值,证据]；动作为 [描述,证据]。
不得修改男主、游戏时间或客观世界，不得引用输入外证据，不得输出解释、Markdown 或额外字段。"""

M2_SYSTEM_BOUNDARY = """[模式：M2 短心智 Patch]
你只判断当前女主自己的状态是否发生有意义的变化，不生成任何回复正文。
输入 s 固定为 form、body、emotion、attention、current_activity、immediate_intent、relationship.stage、relationship.trust、relationship.unresolved_tension。
输入 w 固定为游戏时间、场景、男主位置、男主活动、男主身体、场景简述。这里只有这一个世界。
变化字段代码：0 form，1 body，2 emotion，3 attention，4 current_activity，5 immediate_intent，6 relationship.stage，7 relationship.trust，8 relationship.unresolved_tension，9 motive_state 动态键，10 knowledge_state 动态键。
证据别名：0 冻结世界快照，1 男主当前对白，2 及以后为输入 m 中对应记忆。
只输出 c 和可选 a。c 无变化时必须为 []。普通变化为 [字段代码,新值,证据]；代码 9/10 为 [代码,动态键,新值,证据]；动作为 [描述,证据]。
不得输出 r、reply 或任何最终对白，不得修改男主、游戏时间或客观世界，不得引用输入外证据，不得输出解释、Markdown 或额外字段。"""

R1_SYSTEM_BOUNDARY = """[模式：R1 长期记忆草案]
你只判断已提交事件中什么值得当前女主长期记住，不是在回复男主。
事件为 [别名,说话者代码,正文]，说话者 0 程序世界、1 男主、2 当前女主、3 第三方。已有记忆为 [别名,种类代码,陈述]。
记忆为 [种类,陈述,主体,确定性,时间关系,证据事件别名]，可选第七项时间原文，可选第八项 [关系代码,已有记忆别名]。
种类 1 player_fact，2 preference_boundary，3 person_relation，4 shared_experience，5 relationship_meaning，6 future_event，7 unfinished_topic，8 character_self_claim。
主体 1 男主，2 当前女主，3 双方，4 关系，5 第三方。确定性 1 asserted，2 uncertain，3 hypothetical，4 joking，5 quoted。时间 0 atemporal，1 past，2 present，3 future，4 unknown。关系 1 supersedes，2 contradicts，3 refines。
无长期价值时输出 {\"m\":[]}。只引用输入别名，不复制数据库字段，不输出解释、Markdown 或额外字段。"""

B1_SYSTEM_BOUNDARY = """[模式：B1 后台心智整理]
你只整理当前女主自己的持续状态，不回复男主。
q：1 回答后整理，2 周期整理。s 是当前女主状态，w 是最新游戏世界，x 是本轮对白，d 是世界变化，u 是经过时间，m 是相关记忆，f 是上次审查意见。
状态字段代码：0 form，1 body，2 emotion，3 attention，4 current_activity，5 immediate_intent，6 relationship.stage，7 relationship.trust，8 relationship.unresolved_tension，9 motive_state 动态键，10 knowledge_state 动态键。
证据别名只使用输入中出现的数字别名；0 永远是最新冻结世界。回答后整理中 1 是男主对白、2 是女主回复，其余别名随 d、m 给出。
只输出 c、可选 m、可选 t。c 无变化时必须为 []。普通变化为 [字段代码,新值,证据]；代码 9/10 为 [代码,动态键,新值,证据]。m、t 是最多两条短候选文本。
不得修改男主、游戏时间或客观世界，不得输出对白、解释、Markdown、身份、版本或额外字段。"""

R2_SYSTEM_BOUNDARY = """[模式：R2 长期记忆草案]
你只判断已提交事件中什么值得当前女主长期记住，不回复男主。
事件为 [别名,说话者代码,正文]，1 男主，2 当前女主。已有记忆为 [别名,种类代码,陈述]。
每条记忆只必填 k 种类、s 陈述、e 证据事件别名。种类：1 player_fact，2 preference_boundary，3 person_relation，4 shared_experience，5 relationship_meaning，6 future_event，7 unfinished_topic，8 character_self_claim。
可选 q 确定性：1 asserted，2 uncertain，3 hypothetical，4 joking，5 quoted。可选 t=[时间关系,时间原文]，关系 0 atemporal，1 past，2 present，3 future，4 unknown。可选 r=[关系代码,已有记忆别名]，关系 1 supersedes，2 contradicts，3 refines。
无长期价值时输出 {\"m\":[]}。只引用输入别名，不输出主体、数据库字段、解释、Markdown 或额外字段。"""


class ShortProtocolError(ValueError):
    pass


def m1_payload(snapshot: TurnWorldSnapshot) -> dict[str, object]:
    runtime = snapshot.heroine_runtime
    payload: dict[str, object] = {
        "p": M1_PROTOCOL,
        "s": [
            runtime.living_mind.form,
            runtime.living_mind.body,
            runtime.living_mind.emotion,
            runtime.living_mind.attention,
            runtime.living_mind.current_activity,
            runtime.living_mind.immediate_intent,
            runtime.relationship.stage,
            runtime.relationship.trust,
            runtime.relationship.unresolved_tension,
        ],
        "w": [
            f"D{snapshot.captured_game_time.day} {snapshot.captured_game_time:%H:%M}",
            snapshot.scene.location_label,
            snapshot.protagonist.location_label,
            snapshot.protagonist.activity,
            _short_mapping(snapshot.protagonist.body_state),
            snapshot.scene.location_label,
            snapshot.world_state.scene_text,
        ],
        "u": snapshot.protagonist_utterance,
    }
    if runtime.motive_state:
        payload["o"] = [list(item) for item in runtime.motive_state.items()]
    if runtime.knowledge_state:
        payload["k"] = [list(item) for item in runtime.knowledge_state.items()]
    memories = snapshot.selected_memory_frame.selected_memories
    if memories:
        payload["m"] = [
            [index, _memory_kind_code(memory.kind), memory.statement]
            for index, memory in enumerate(memories, start=2)
        ]
    return payload


def m2_payload(snapshot: TurnWorldSnapshot) -> dict[str, object]:
    runtime = snapshot.heroine_runtime
    payload: dict[str, object] = {
        "p": M2_PROTOCOL,
        "s": [
            runtime.living_mind.form,
            runtime.living_mind.body,
            runtime.living_mind.emotion,
            runtime.living_mind.attention,
            runtime.living_mind.current_activity,
            runtime.living_mind.immediate_intent,
            runtime.relationship.stage,
            runtime.relationship.trust,
            runtime.relationship.unresolved_tension,
        ],
        "w": [
            f"D{snapshot.captured_game_time.day} {snapshot.captured_game_time:%H:%M}",
            snapshot.scene.location_label,
            snapshot.protagonist.location_label,
            snapshot.protagonist.activity,
            _short_mapping(snapshot.protagonist.body_state),
            snapshot.world_state.scene_text,
        ],
        "u": snapshot.protagonist_utterance,
    }
    memories = snapshot.selected_memory_frame.selected_memories
    if memories:
        payload["m"] = [
            [index, _memory_kind_code(memory.kind), memory.statement]
            for index, memory in enumerate(memories, start=2)
        ]
    return payload


def compile_m2(value: object, snapshot: TurnWorldSnapshot) -> MindPatchV2Result:
    item = _object(value, "M2 output")
    if "r" in item or "reply" in item:
        raise ShortProtocolError("M2 cannot contain reply text")
    if "c" not in item or not set(item) <= {"c", "a"}:
        raise ShortProtocolError("M2 output fields do not match protocol")
    compiled = _compile_patch_fields(item, snapshot, protocol="M2")
    return MindPatchV2Result(
        mind_result=compiled[0],
        changed_field_codes=compiled[1],
        raw_output=dict(item),
    )


def compile_m1(
    value: object, snapshot: TurnWorldSnapshot
) -> ForegroundSemanticTurnResult:
    item = _object(value, "M1 output")
    if not {"c", "r"} <= set(item) or not set(item) <= {"c", "a", "r"}:
        raise ShortProtocolError("M1 output fields do not match protocol")
    reply_text = _text(item["r"], "r", maximum=500)
    mind_result, changed_codes = _compile_patch_fields(item, snapshot, protocol="M1")
    assertions = _snapshot_assertions(snapshot)
    return ForegroundSemanticTurnResult(
        mind_result=mind_result,
        reply_result=GameReplyResult(
            text=reply_text,
            fact_assertions=assertions,
            snapshot_id=snapshot.snapshot_id,
            approved_mind_state_version=snapshot.mind_state_version + 1,
        ),
        changed_field_codes=changed_codes,
        raw_output=dict(item),
    )


def b1_payload(request: ReconcileMindRequest) -> dict[str, object]:
    payload, _aliases = _b1_material(request)
    return payload


def compile_b1(
    value: object, request: ReconcileMindRequest
) -> ReconcileMindResult:
    item = _object(value, "B1 output")
    if "c" not in item or not set(item) <= {"c", "m", "t"}:
        raise ShortProtocolError("B1 output fields do not match protocol")
    changes = _array(item["c"], "c", maximum=B1_MAX_CHANGES)
    _payload, aliases = _b1_material(request)
    living_updates: dict[str, str] = {}
    relationship_updates: dict[str, str] = {}
    motive_updates: dict[str, str] = {}
    knowledge_updates: dict[str, str] = {}
    evidence_refs: list[str] = []
    seen_fields: set[tuple[int, str | None]] = set()
    living_fields = {
        0: "form",
        1: "body",
        2: "emotion",
        3: "attention",
        4: "current_activity",
        5: "immediate_intent",
    }
    relationship_fields = {6: "stage", 7: "trust", 8: "unresolved_tension"}
    for index, raw_change in enumerate(changes):
        change = _array(raw_change, f"c[{index}]", minimum=3, maximum=4)
        code = _code(change[0], f"c[{index}][0]", minimum=0, maximum=10)
        if code <= 8:
            if len(change) != 3:
                raise ShortProtocolError("B1 fixed-field change must have 3 items")
            key = None
            new_value = _text(change[1], f"c[{index}][1]", maximum=160)
            raw_aliases = change[2]
        else:
            if len(change) != 4:
                raise ShortProtocolError("B1 dynamic change must have 4 items")
            key = _dynamic_key(change[1], f"c[{index}][1]")
            new_value = _text(change[2], f"c[{index}][2]", maximum=160)
            raw_aliases = change[3]
        identity = (code, key)
        if identity in seen_fields:
            raise ShortProtocolError("B1 cannot change the same field twice")
        seen_fields.add(identity)
        evidence_refs.extend(
            _resolve_aliases(raw_aliases, aliases, f"c[{index}] evidence")
        )
        if code in living_fields:
            living_updates[living_fields[code]] = new_value
        elif code in relationship_fields:
            relationship_updates[relationship_fields[code]] = new_value
        elif code == 9:
            assert key is not None
            motive_updates[key] = new_value
        else:
            assert key is not None
            knowledge_updates[key] = new_value

    memory_candidates = tuple(
        _text(candidate, "m candidate", maximum=240)
        for candidate in _array(item.get("m", []), "m", maximum=2)
    )
    timeline_candidates = tuple(
        _text(candidate, "t candidate", maximum=240)
        for candidate in _array(item.get("t", []), "t", maximum=2)
    )
    snapshot = request.snapshot
    transition_basis = tuple(dict.fromkeys(evidence_refs)) or (
        snapshot.snapshot_id,
    )
    patch = None
    operation = "keep"
    if changes:
        operation = "update"
        patch = HeroineMindPatch(
            living_mind=LivingMindPatch(**living_updates),
            relationship=RelationshipPatch(**relationship_updates),
            evidence_refs=transition_basis,
            motive_updates=motive_updates,
            knowledge_updates=knowledge_updates,
        )
    return ReconcileMindResult(
        operation=operation,
        reason=(
            f"B1 compiled {len(changes)} state changes"
            if changes
            else "B1 found no durable state change"
        ),
        parent_mind_state_version=snapshot.mind_state_version,
        snapshot_id=snapshot.snapshot_id,
        latest_world_version=snapshot.live_world_version,
        transition_basis=transition_basis,
        patch=patch,
        memory_candidates=memory_candidates,
        timeline_candidates=timeline_candidates,
    )


def _compile_patch_fields(
    item: Mapping[str, object],
    snapshot: TurnWorldSnapshot,
    *,
    protocol: str,
) -> tuple[MindAdvanceResult, tuple[int, ...]]:
    changes = _array(item["c"], "c", maximum=M1_MAX_CHANGES)
    actions = _array(item.get("a", []), "a", maximum=M1_MAX_ACTIONS)
    aliases = _m1_evidence_aliases(snapshot)
    living_updates: dict[str, str] = {}
    relationship_updates: dict[str, str] = {}
    motive_updates: dict[str, str] = {}
    knowledge_updates: dict[str, str] = {}
    evidence_refs: list[str] = []
    changed_codes: list[int] = []
    seen_fields: set[tuple[int, str | None]] = set()
    living_fields = {
        0: "form",
        1: "body",
        2: "emotion",
        3: "attention",
        4: "current_activity",
        5: "immediate_intent",
    }
    relationship_fields = {6: "stage", 7: "trust", 8: "unresolved_tension"}
    for index, raw_change in enumerate(changes):
        change = _array(raw_change, f"c[{index}]", minimum=3, maximum=4)
        code = _code(change[0], f"c[{index}][0]", minimum=0, maximum=10)
        if code <= 8:
            if len(change) != 3:
                raise ShortProtocolError(f"{protocol} fixed-field change must have 3 items")
            key = None
            new_value = _text(change[1], f"c[{index}][1]", maximum=160)
            raw_aliases = change[2]
        else:
            if len(change) != 4:
                raise ShortProtocolError(f"{protocol} dynamic change must have 4 items")
            key = _dynamic_key(change[1], f"c[{index}][1]")
            new_value = _text(change[2], f"c[{index}][2]", maximum=160)
            raw_aliases = change[3]
        field_identity = (code, key)
        if field_identity in seen_fields:
            raise ShortProtocolError(f"{protocol} cannot change the same field twice")
        seen_fields.add(field_identity)
        refs = _resolve_aliases(raw_aliases, aliases, f"c[{index}] evidence")
        evidence_refs.extend(refs)
        changed_codes.append(code)
        if code in living_fields:
            living_updates[living_fields[code]] = new_value
        elif code in relationship_fields:
            relationship_updates[relationship_fields[code]] = new_value
        elif code == 9:
            assert key is not None
            motive_updates[key] = new_value
        else:
            assert key is not None
            knowledge_updates[key] = new_value
    compiled_actions: list[HeroineDiegeticAction] = []
    for index, raw_action in enumerate(actions):
        action = _array(raw_action, f"a[{index}]", minimum=2, maximum=2)
        compiled_actions.append(
            HeroineDiegeticAction(
                description=_text(action[0], f"a[{index}][0]", maximum=240),
                evidence_refs=_resolve_aliases(
                    action[1], aliases, f"a[{index}] evidence"
                ),
            )
        )
    patch_refs = tuple(dict.fromkeys(evidence_refs)) or (snapshot.snapshot_id,)
    patch = HeroineMindPatch(
        living_mind=LivingMindPatch(**living_updates),
        relationship=RelationshipPatch(**relationship_updates),
        evidence_refs=patch_refs,
        motive_updates=motive_updates,
        knowledge_updates=knowledge_updates,
    )
    assertions = _snapshot_assertions(snapshot)
    mind_result = MindAdvanceResult(
        patch=patch,
        reply_intent="从本轮更新后的自身状态出发回应男主",
        fact_assertions=assertions,
        parent_mind_state_version=snapshot.mind_state_version,
        snapshot_id=snapshot.snapshot_id,
        transition_basis=patch_refs,
        heroine_diegetic_actions=tuple(compiled_actions),
    )
    return mind_result, tuple(changed_codes)


def r1_payload(request: HeroineMemoryProposeRequest) -> dict[str, object]:
    latest = max(event.game_time for event in request.source_events)
    payload: dict[str, object] = {
        "p": R1_PROTOCOL,
        "n": f"D{latest.day} {latest:%H:%M}",
        "e": [
            [index, _speaker_code(event.actor), event.text]
            for index, event in enumerate(request.source_events)
        ],
    }
    if request.existing_memories:
        payload["m"] = [
            [index, _memory_kind_code(memory.kind), memory.statement]
            for index, memory in enumerate(request.existing_memories[:8])
        ]
    return payload


def r2_payload(request: HeroineMemoryProposeRequest) -> dict[str, object]:
    payload = r1_payload(request)
    payload["p"] = R2_PROTOCOL
    return payload


def compile_r1(
    value: object, request: HeroineMemoryProposeRequest
) -> tuple[MemoryProposalDraft, ...]:
    item = _object(value, "R1 output")
    if set(item) != {"m"}:
        raise ShortProtocolError("R1 output fields do not match protocol")
    memories = _array(item["m"], "m", maximum=R1_MAX_MEMORIES)
    events = {index: event for index, event in enumerate(request.source_events)}
    existing = {
        index: memory for index, memory in enumerate(request.existing_memories[:8])
    }
    drafts: list[MemoryProposalDraft] = []
    for index, raw_memory in enumerate(memories):
        memory = _array(raw_memory, f"m[{index}]", minimum=6, maximum=8)
        kind = _enum_code(memory[0], _R1_MEMORY_KINDS, f"m[{index}].kind")
        statement = _text(memory[1], f"m[{index}].statement", maximum=300)
        subject_code = _code(memory[2], f"m[{index}].subject", minimum=1, maximum=5)
        modality = _enum_code(
            memory[3], _R1_MODALITIES, f"m[{index}].certainty"
        )
        temporal_relation = _enum_code(
            memory[4], _R1_TEMPORAL_RELATIONS, f"m[{index}].time_relation"
        )
        evidence_aliases = _integer_aliases(
            memory[5], f"m[{index}].evidence", maximum=2
        )
        source_events = []
        for alias in evidence_aliases:
            try:
                source_events.append(events[alias])
            except KeyError as error:
                raise ShortProtocolError(
                    "R1 evidence alias escaped source events"
                ) from error
        time_expression = None
        if len(memory) >= 7 and memory[6] is not None:
            time_expression = _text(
                memory[6], f"m[{index}].time_expression", maximum=120
            )
        relations = MemoryRelations(None, (), (), ())
        if len(memory) == 8:
            raw_relation = _array(
                memory[7], f"m[{index}].relation", minimum=2, maximum=2
            )
            relation_name = _enum_code(
                raw_relation[0], _R1_RELATIONS, f"m[{index}].relation.type"
            )
            target_alias = _code(
                raw_relation[1], f"m[{index}].relation.target", minimum=0
            )
            try:
                target_id = existing[target_alias].memory_id
            except KeyError as error:
                raise ShortProtocolError(
                    "R1 relation alias escaped current heroine repository"
                ) from error
            relation_values = {"supersedes": (), "contradicts": (), "refines": ()}
            relation_values[relation_name] = (target_id,)
            relations = MemoryRelations(None, **relation_values)
        drafts.append(
            MemoryProposalDraft(
                kind=kind,
                statement=statement,
                subject=_r1_subject(subject_code, request),
                epistemic=MemoryEpistemic(
                    polarity="affirmed",
                    modality=modality,
                    grounding=_grounding(source_events),
                ),
                temporal=_r1_temporal(
                    temporal_relation,
                    time_expression,
                    source_events[0].event_id,
                ),
                evidence_quotes=tuple(
                    EvidenceQuote(
                        event_id=event.event_id,
                        role="support",
                        quote=event.text,
                        start_hint=0,
                    )
                    for event in source_events
                ),
                semantic_reason="R1 semantic memory proposal",
                confidence=1.0,
                relation_suggestions=relations,
            )
        )
    return tuple(drafts)


def compile_r2(
    value: object, request: HeroineMemoryProposeRequest
) -> tuple[MemoryProposalDraft, ...]:
    item = _object(value, "R2 output")
    if set(item) != {"m"}:
        raise ShortProtocolError("R2 output fields do not match protocol")
    memories = _array(item["m"], "m", maximum=R1_MAX_MEMORIES)
    events = {index: event for index, event in enumerate(request.source_events)}
    existing = {
        index: memory for index, memory in enumerate(request.existing_memories[:8])
    }
    drafts: list[MemoryProposalDraft] = []
    for index, raw_memory in enumerate(memories):
        memory = _object(raw_memory, f"m[{index}]")
        if not {"k", "s", "e"} <= set(memory) or not set(memory) <= {
            "k",
            "s",
            "e",
            "q",
            "t",
            "r",
        }:
            raise ShortProtocolError("R2 memory fields do not match protocol")
        kind = _enum_code(memory["k"], _R1_MEMORY_KINDS, f"m[{index}].k")
        statement = _text(memory["s"], f"m[{index}].s", maximum=300)
        evidence_aliases = _integer_aliases(
            memory["e"], f"m[{index}].e", maximum=2
        )
        source_events = []
        for alias in evidence_aliases:
            try:
                source_events.append(events[alias])
            except KeyError as error:
                raise ShortProtocolError(
                    "R2 evidence alias escaped source events"
                ) from error
        modality = _enum_code(
            memory.get("q", 1), _R1_MODALITIES, f"m[{index}].q"
        )
        temporal_relation = "atemporal"
        time_expression = None
        if "t" in memory:
            raw_temporal = _array(memory["t"], f"m[{index}].t", minimum=2, maximum=2)
            temporal_relation = _enum_code(
                raw_temporal[0], _R1_TEMPORAL_RELATIONS, f"m[{index}].t[0]"
            )
            if raw_temporal[1] is not None:
                time_expression = _text(
                    raw_temporal[1], f"m[{index}].t[1]", maximum=120
                )
        relations = MemoryRelations(None, (), (), ())
        if "r" in memory:
            raw_relation = _array(memory["r"], f"m[{index}].r", minimum=2, maximum=2)
            relation_name = _enum_code(
                raw_relation[0], _R1_RELATIONS, f"m[{index}].r[0]"
            )
            target_alias = _code(
                raw_relation[1], f"m[{index}].r[1]", minimum=0
            )
            try:
                target_id = existing[target_alias].memory_id
            except KeyError as error:
                raise ShortProtocolError(
                    "R2 relation alias escaped current heroine repository"
                ) from error
            relation_values = {"supersedes": (), "contradicts": (), "refines": ()}
            relation_values[relation_name] = (target_id,)
            relations = MemoryRelations(None, **relation_values)
        drafts.append(
            MemoryProposalDraft(
                kind=kind,
                statement=statement,
                subject=_r2_subject(kind, request),
                epistemic=MemoryEpistemic(
                    polarity="affirmed",
                    modality=modality,
                    grounding=_grounding(source_events),
                ),
                temporal=_r1_temporal(
                    temporal_relation,
                    time_expression,
                    source_events[0].event_id,
                ),
                evidence_quotes=tuple(
                    EvidenceQuote(
                        event_id=event.event_id,
                        role="support",
                        quote=event.text,
                        start_hint=0,
                    )
                    for event in source_events
                ),
                semantic_reason="R2 semantic memory proposal",
                confidence=1.0,
                relation_suggestions=relations,
            )
        )
    return tuple(drafts)


def decode_json_object(text: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ShortProtocolError("short protocol output is not valid JSON") from error
    return _object(value, "short protocol output")


_R1_MEMORY_KINDS = {
    1: "player_fact",
    2: "preference_boundary",
    3: "person_relation",
    4: "shared_experience",
    5: "relationship_meaning",
    6: "future_event",
    7: "unfinished_topic",
    8: "character_self_claim",
}
_MEMORY_KIND_CODES = {value: key for key, value in _R1_MEMORY_KINDS.items()}
_R1_MODALITIES = {
    1: "asserted",
    2: "uncertain",
    3: "hypothetical",
    4: "joking",
    5: "quoted",
}
_R1_TEMPORAL_RELATIONS = {
    0: "atemporal",
    1: "past",
    2: "present",
    3: "future",
    4: "unknown",
}
_R1_RELATIONS = {1: "supersedes", 2: "contradicts", 3: "refines"}
_DYNAMIC_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def _b1_material(
    request: ReconcileMindRequest,
) -> tuple[dict[str, object], dict[int, str]]:
    snapshot = request.snapshot
    runtime = snapshot.heroine_runtime
    mode_code = {
        POST_REPLY_WORLD_MIND_RECONCILE: 1,
        FIVE_MINUTE_WORLD_MIND_RECONCILE: 2,
    }[request.mode]
    payload: dict[str, object] = {
        "p": B1_PROTOCOL,
        "q": mode_code,
        "s": [
            _clip(runtime.living_mind.form, 64),
            _clip(runtime.living_mind.body, 64),
            _clip(runtime.living_mind.emotion, 64),
            _clip(runtime.living_mind.attention, 64),
            _clip(runtime.living_mind.current_activity, 64),
            _clip(runtime.living_mind.immediate_intent, 64),
            _clip(runtime.relationship.stage, 64),
            _clip(runtime.relationship.trust, 64),
            _clip(runtime.relationship.unresolved_tension, 64),
        ],
        "w": [
            f"D{snapshot.captured_game_time.day} {snapshot.captured_game_time:%H:%M}",
            _clip(snapshot.scene.location_label, 64),
            _clip(snapshot.protagonist.location_label, 64),
            _clip(snapshot.protagonist.activity, 96),
            _clip(_short_mapping(snapshot.protagonist.body_state), 96),
            _clip(snapshot.world_state.scene_text, 160),
        ],
        "u": [round(request.elapsed_game_seconds), request.missed_intervals],
    }
    aliases: dict[int, str] = {0: snapshot.snapshot_id}
    next_alias = 1
    if request.source_turn is not None:
        payload["x"] = [
            _clip(request.source_turn.protagonist_utterance, 160),
            _clip(request.source_turn.heroine_reply, 160),
            [
                _clip(action.description, 96)
                for action in request.source_turn.approved_actions[:2]
            ],
        ]
        aliases[1] = request.source_turn.user_event_id
        aliases[2] = request.source_turn.assistant_event_id
        next_alias = 3
    deltas = []
    for delta in snapshot.event_deltas[-4:]:
        alias = next_alias
        next_alias += 1
        aliases[alias] = delta.event_id
        deltas.append(
            [
                alias,
                f"D{delta.game_time.day} {delta.game_time:%H:%M}",
                _clip("；".join(delta.changed_fields), 96),
            ]
        )
    if deltas:
        payload["d"] = deltas
    memories = []
    for memory in snapshot.selected_memory_frame.selected_memories[:4]:
        alias = next_alias
        next_alias += 1
        aliases[alias] = memory.memory_id
        memories.append(
            [alias, _memory_kind_code(memory.kind), _clip(memory.statement, 140)]
        )
    if memories:
        payload["m"] = memories
    if request.review_feedback is not None:
        payload["f"] = _clip(request.review_feedback, 120)
    _fit_b1_payload(payload)
    retained_aliases = {0}
    if "x" in payload:
        retained_aliases.update((1, 2))
    for key in ("d", "m"):
        for entry in payload.get(key, []):
            retained_aliases.add(entry[0])
    return payload, {
        alias: evidence_id
        for alias, evidence_id in aliases.items()
        if alias in retained_aliases
    }


def _fit_b1_payload(payload: dict[str, object], maximum_bytes: int = 1800) -> None:
    def size() -> int:
        return len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )

    while size() > maximum_bytes:
        memories = payload.get("m")
        if isinstance(memories, list) and memories:
            memories.pop()
            if not memories:
                payload.pop("m")
            continue
        deltas = payload.get("d")
        if isinstance(deltas, list) and deltas:
            deltas.pop(0)
            if not deltas:
                payload.pop("d")
            continue
        source_turn = payload.get("x")
        if isinstance(source_turn, list):
            candidates = [
                (len(value), index)
                for index, value in enumerate(source_turn[:2])
                if isinstance(value, str) and len(value) > 32
            ]
            if candidates:
                _length, index = max(candidates)
                source_turn[index] = _clip(source_turn[index], _length - 16)
                continue
        world = payload["w"]
        if isinstance(world, list) and isinstance(world[-1], str) and len(world[-1]) > 48:
            world[-1] = _clip(world[-1], len(world[-1]) - 16)
            continue
        states = payload["s"]
        if isinstance(states, list):
            candidates = [
                (len(value), index)
                for index, value in enumerate(states)
                if isinstance(value, str) and len(value) > 24
            ]
            if candidates:
                _length, index = max(candidates)
                states[index] = _clip(states[index], _length - 8)
                continue
        raise ShortProtocolError("B1 payload cannot fit its frozen byte budget")


def _clip(value: str, maximum: int) -> str:
    text = value.strip()
    if len(text) <= maximum:
        return text
    return text[: maximum - 1].rstrip() + "…"


def _m1_evidence_aliases(snapshot: TurnWorldSnapshot) -> dict[int, str]:
    if not snapshot.protagonist_utterance_event_id:
        raise ShortProtocolError("M1 snapshot requires a preallocated utterance event ID")
    aliases = {0: snapshot.snapshot_id, 1: snapshot.protagonist_utterance_event_id}
    aliases.update(
        {
            index: memory.memory_id
            for index, memory in enumerate(
                snapshot.selected_memory_frame.selected_memories, start=2
            )
        }
    )
    return aliases


def _resolve_aliases(value: object, aliases: Mapping[int, str], name: str) -> tuple[str, ...]:
    values = _integer_aliases(value, name, maximum=4)
    resolved = []
    for alias in values:
        try:
            resolved.append(aliases[alias])
        except KeyError as error:
            raise ShortProtocolError(f"{name} contains an unknown alias") from error
    return tuple(resolved)


def _integer_aliases(value: object, name: str, *, maximum: int) -> tuple[int, ...]:
    values = _array(value, name, minimum=1, maximum=maximum)
    aliases = tuple(_code(item, name, minimum=0) for item in values)
    if len(set(aliases)) != len(aliases):
        raise ShortProtocolError(f"{name} cannot contain duplicate aliases")
    return aliases


def _snapshot_assertions(snapshot: TurnWorldSnapshot) -> ReplyFactAssertions:
    return ReplyFactAssertions(
        protagonist_location_id=snapshot.protagonist.location_id,
        protagonist_activity=snapshot.protagonist.activity,
        live_world_version=snapshot.live_world_version,
        captured_game_time=snapshot.captured_game_time,
    )


def _r1_subject(
    code: int, request: HeroineMemoryProposeRequest
) -> MemorySubject:
    if code == 1:
        return MemorySubject("player", "protagonist", "男主")
    if code == 2:
        return MemorySubject("character", request.owner_character_id, None)
    if code == 3:
        return MemorySubject("both", None, "男主与当前女主")
    if code == 4:
        return MemorySubject("relationship", None, "双方关系")
    return MemorySubject("third_party", None, "第三方")


def _r2_subject(kind: str, request: HeroineMemoryProposeRequest) -> MemorySubject:
    if kind == "character_self_claim":
        return MemorySubject("character", request.owner_character_id, None)
    if kind in {"shared_experience", "future_event", "unfinished_topic"}:
        return MemorySubject("both", None, "男主与当前女主")
    if kind == "relationship_meaning":
        return MemorySubject("relationship", None, "双方关系")
    return MemorySubject("player", "protagonist", "男主")


def _r1_temporal(
    relation: str, source_text: str | None, anchor_event_id: str
) -> MemoryTemporal:
    if relation == "atemporal":
        return MemoryTemporal(
            relation=relation,
            resolution="not_applicable",
            source_text=None,
            anchor_event_id=None,
            start_at=None,
            end_at=None,
            timezone=None,
            precision="not_applicable",
        )
    return MemoryTemporal(
        relation=relation,
        resolution="ambiguous",
        source_text=source_text,
        anchor_event_id=anchor_event_id,
        start_at=None,
        end_at=None,
        timezone=None,
        precision="unknown",
    )


def _grounding(events: list[object]) -> str:
    actors = {getattr(event, "actor") for event in events}
    return "acknowledged" if actors == {"protagonist", "heroine"} else "speaker_report"


def _speaker_code(actor: str) -> int:
    try:
        return {"protagonist": 1, "heroine": 2}[actor]
    except KeyError as error:
        raise ShortProtocolError(f"unsupported R1 source actor: {actor}") from error


def _memory_kind_code(kind: str) -> int:
    try:
        return _MEMORY_KIND_CODES[kind]
    except KeyError as error:
        raise ShortProtocolError(f"unsupported memory kind: {kind}") from error


def _short_mapping(value: Mapping[str, str]) -> str:
    return "；".join(f"{key}={item}" for key, item in value.items()) or "正常"


def _dynamic_key(value: object, name: str) -> str:
    key = _text(value, name, maximum=80)
    if not _DYNAMIC_KEY_PATTERN.fullmatch(key):
        raise ShortProtocolError(f"{name} is not a valid dynamic key")
    return key


def _enum_code(value: object, mapping: Mapping[int, str], name: str) -> str:
    code = _code(value, name, minimum=min(mapping), maximum=max(mapping))
    try:
        return mapping[code]
    except KeyError as error:
        raise ShortProtocolError(f"{name} is an unknown code") from error


def _code(value: object, name: str, *, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (
        maximum is not None and value > maximum
    ):
        raise ShortProtocolError(f"{name} is outside the allowed code range")
    return value


def _object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ShortProtocolError(f"{name} must be an object with string keys")
    return value


def _array(
    value: object,
    name: str,
    *,
    minimum: int = 0,
    maximum: int,
) -> list[object]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ShortProtocolError(f"{name} has an invalid array length")
    return value


def _text(value: object, name: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ShortProtocolError(f"{name} must be non-empty bounded text")
    return value.strip()

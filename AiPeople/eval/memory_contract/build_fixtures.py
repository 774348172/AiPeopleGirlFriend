from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "eval" / "memory_contract" / "fixtures"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _temporal(
    *,
    relation: str = "atemporal",
    resolution: str = "not_applicable",
    source_text: str | None = None,
    anchor_event_id: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    timezone: str | None = None,
    precision: str = "not_applicable",
) -> dict[str, object]:
    return {
        "relation": relation,
        "resolution": resolution,
        "source_text": source_text,
        "anchor_event_id": anchor_event_id,
        "start_at": start_at,
        "end_at": end_at,
        "timezone": timezone,
        "precision": precision,
    }


def _source(
    event_id: str,
    text: str,
    *,
    actor: str = "user",
    conversation_id: str = "conversation-fixture",
    event_type: str = "message",
    committed: bool = True,
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "conversation_id": conversation_id,
        "actor": actor,
        "event_type": event_type,
        "committed": committed,
        "text": text,
    }


def _representation(
    case_id: str,
    *,
    kind: str,
    statement: str,
    subject_type: str,
    sources: list[dict[str, object]],
    modality: str = "asserted",
    polarity: str = "affirmed",
    grounding: str = "speaker_report",
    temporal: dict[str, object] | None = None,
    roles: list[str] | None = None,
    entity_id: str | None = None,
    display_name: str | None = None,
    relations: dict[str, object] | None = None,
    memory_id: str | None = None,
) -> dict[str, object]:
    roles = roles or ["support"] * len(sources)
    evidence = []
    for source, role in zip(sources, roles, strict=True):
        text = str(source["text"])
        evidence.append(
            {
                "event_id": source["event_id"],
                "role": role,
                "excerpt_start": 0,
                "excerpt_end": len(text),
                "excerpt": text,
                "excerpt_sha256": _sha256(text),
            }
        )
    return {
        "case_id": case_id,
        "representation": {
            "schema_version": 1,
            "memory_id": memory_id or f"memory-{case_id}",
            "version": 1,
            "conversation_id": "conversation-fixture",
            "proposal_run_id": "proposal-fixture",
            "proposal_ordinal": 0,
            "kind": kind,
            "statement": statement,
            "subject": {
                "type": subject_type,
                "entity_id": entity_id,
                "display_name": display_name,
            },
            "epistemic": {
                "polarity": polarity,
                "modality": modality,
                "grounding": grounding,
            },
            "temporal": temporal or _temporal(),
            "evidence": evidence,
            "semantic_reason": "该内容可能影响后续关系对话的自然连续性",
            "confidence": 0.9,
            "relations": relations
            or {
                "semantic_slot": None,
                "supersedes": [],
                "contradicts": [],
                "refines": [],
            },
            "status": "proposed",
            "created_at": "2026-08-07T12:00:00Z",
            "idempotency_key": f"proposal-fixture:{case_id}",
        },
        "evidence_sources": sources,
        "relation_targets": [],
    }


def _valid_cases() -> list[dict[str, object]]:
    cases = [
        _representation(
            "player-fact",
            kind="player_fact",
            statement="玩家说自己在杭州工作",
            subject_type="player",
            sources=[_source("event-player-fact", "我在杭州工作。")],
        ),
        _representation(
            "preference-negated",
            kind="preference_boundary",
            statement="玩家明确表示自己不喜欢香菜",
            subject_type="player",
            polarity="negated",
            sources=[_source("event-preference", "我不喜欢香菜。")],
        ),
        _representation(
            "person-relation-quoted",
            kind="person_relation",
            statement="玩家引用小林的话说明小林把玩家当作老朋友",
            subject_type="third_party",
            entity_id="person-xiaolin",
            display_name="小林",
            modality="quoted",
            sources=[_source("event-relation", "小林说，他一直把我当老朋友。")],
        ),
        _representation(
            "shared-experience",
            kind="shared_experience",
            statement="双方在上次对话中共同回顾了看电影的经历",
            subject_type="both",
            grounding="mutually_confirmed",
            sources=[
                _source("event-shared-user", "上周我们一起看的那部电影挺好。"),
                _source(
                    "event-shared-character",
                    "嗯，片尾出来的时候我们都没急着说话。",
                    actor="character",
                ),
            ],
        ),
        _representation(
            "relationship-meaning",
            kind="relationship_meaning",
            statement="双方承认最近的冷淡让关系有些别扭",
            subject_type="relationship",
            grounding="acknowledged",
            sources=[
                _source("event-meaning-user", "我们最近是不是有点疏远了？"),
                _source(
                    "event-meaning-character", "我也感觉到了，是有点别扭。", actor="character"
                ),
            ],
        ),
        _representation(
            "future-uncertain",
            kind="future_event",
            statement="玩家表示下周可能去上海出差，但尚未确定",
            subject_type="player",
            modality="uncertain",
            temporal=_temporal(
                relation="future",
                resolution="ambiguous",
                source_text="下周",
                anchor_event_id="event-future",
                precision="unknown",
            ),
            sources=[_source("event-future", "我下周可能去上海出差，还没定。")],
        ),
        _representation(
            "unfinished-hypothetical",
            kind="unfinished_topic",
            statement="玩家假设辞职后去学画画，并未表示已经决定",
            subject_type="player",
            modality="hypothetical",
            sources=[_source("event-unfinished", "如果我辞职去学画画，会不会太冲动？")],
        ),
        _representation(
            "character-self-joking",
            kind="character_self_claim",
            statement="秦未晞开玩笑说自己可以靠咖啡活着，并非生理事实",
            subject_type="character",
            modality="joking",
            sources=[
                _source(
                    "event-self", "照这个喝法，我大概可以靠咖啡活着。开玩笑的。", actor="character"
                )
            ],
        ),
    ]

    old = _representation(
        "old-location",
        kind="player_fact",
        statement="玩家说自己住在城西",
        subject_type="player",
        sources=[_source("event-old-location", "我住在城西。")],
        memory_id="memory-old-location",
    )
    correction = _representation(
        "correction",
        kind="player_fact",
        statement="玩家纠正自己现在住在城南，不是城西",
        subject_type="player",
        sources=[_source("event-correction", "我刚才说错了，我住城南，不是城西。")],
        roles=["correction"],
        relations={
            "semantic_slot": "player.home.area",
            "supersedes": ["memory-old-location"],
            "contradicts": [],
            "refines": [],
        },
    )
    correction["relation_targets"] = [old["representation"]]
    cases.append(correction)

    conflict_target = deepcopy(old)
    conflict_target["representation"]["memory_id"] = "memory-conflict-target"
    conflict_target["representation"]["idempotency_key"] = "proposal-fixture:conflict-target"
    conflict = _representation(
        "conflict",
        kind="player_fact",
        statement="玩家另一次表示自己住在城北，与现有说法冲突",
        subject_type="player",
        sources=[_source("event-conflict", "其实我住在城北。")],
        relations={
            "semantic_slot": "player.home.area",
            "supersedes": [],
            "contradicts": ["memory-conflict-target"],
            "refines": [],
        },
    )
    conflict["relation_targets"] = [conflict_target["representation"]]
    cases.append(conflict)
    return cases


def _invalid_cases(valid: list[dict[str, object]]) -> list[dict[str, object]]:
    base = valid[0]

    def changed(case_id: str) -> dict[str, object]:
        case = deepcopy(base)
        case["case_id"] = case_id
        case["representation"]["memory_id"] = f"memory-{case_id}"
        case["representation"]["idempotency_key"] = f"proposal-fixture:{case_id}"
        return case

    result = []

    case = changed("cross-conversation")
    case["evidence_sources"][0]["conversation_id"] = "conversation-other"
    case["expected_error"] = "memory_evidence_cross_conversation"
    result.append(case)

    case = changed("cancelled-output")
    case["evidence_sources"][0].update(event_type="generation_cancelled", actor="character")
    case["expected_error"] = "memory_evidence_uncommitted"
    result.append(case)

    case = changed("uncommitted-output")
    case["evidence_sources"][0]["committed"] = False
    case["expected_error"] = "memory_evidence_uncommitted"
    result.append(case)

    case = changed("excerpt-mismatch")
    case["evidence_sources"][0]["text"] = "账本里是另一段原文。"
    case["expected_error"] = "memory_evidence_excerpt_mismatch"
    result.append(case)

    case = changed("context-only")
    case["representation"]["evidence"][0]["role"] = "context"
    case["expected_error"] = "memory_evidence_missing"
    result.append(case)

    case = changed("character-self-from-player")
    case["representation"].update(kind="character_self_claim")
    case["representation"]["subject"]["type"] = "character"
    case["expected_error"] = "memory_evidence_missing"
    result.append(case)

    case = changed("mutual-with-one-actor")
    case["representation"]["epistemic"]["grounding"] = "mutually_confirmed"
    case["expected_error"] = "memory_evidence_missing"
    result.append(case)

    case = changed("future-date-guessed")
    case["representation"].update(kind="future_event")
    case["representation"]["temporal"] = _temporal(
        relation="future",
        resolution="ambiguous",
        source_text="改天",
        anchor_event_id="event-player-fact",
        start_at="2026-08-10T00:00:00Z",
        timezone="Asia/Shanghai",
        precision="day",
    )
    case["expected_error"] = "memory_cross_field_invalid"
    result.append(case)

    case = changed("empty-evidence")
    case["representation"]["evidence"] = []
    case["expected_error"] = "memory_schema_invalid"
    result.append(case)

    return result


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    valid = _valid_cases()
    invalid = _invalid_cases(valid)
    _write(FIXTURE_ROOT / "valid" / "semantic_boundaries_v1.json", valid)
    _write(FIXTURE_ROOT / "valid" / "zero_proposals_v1.json", {"proposals": []})
    _write(FIXTURE_ROOT / "invalid" / "evidence_boundaries_v1.json", invalid)


if __name__ == "__main__":
    main()

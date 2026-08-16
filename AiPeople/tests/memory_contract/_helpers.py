from __future__ import annotations

import hashlib
from copy import deepcopy

from runtime._memory_contracts import EvidenceSource


def draft_dict() -> dict[str, object]:
    return {
        "kind": "future_event",
        "statement": "玩家表示下周可能去上海出差，但尚未确定",
        "subject": {"type": "player", "entity_id": None, "display_name": None},
        "epistemic": {
            "polarity": "affirmed",
            "modality": "uncertain",
            "grounding": "speaker_report",
        },
        "temporal": {
            "relation": "future",
            "resolution": "ambiguous",
            "source_text": "下周",
            "anchor_event_id": "event-user-1",
            "start_at": None,
            "end_at": None,
            "timezone": None,
            "precision": "unknown",
        },
        "evidence_quotes": [
            {
                "event_id": "event-user-1",
                "role": "support",
                "quote": "我下周可能去上海出差，还没完全定。",
                "start_hint": None,
            }
        ],
        "semantic_reason": "数日后继续聊天时可能自然关心出差是否确定",
        "confidence": 0.96,
        "relation_suggestions": {
            "semantic_slot": "player.future.travel.shanghai",
            "supersedes": [],
            "contradicts": [],
            "refines": [],
        },
    }


def representation_dict(
    *,
    text: str = "我下周可能去上海出差，还没完全定。",
    excerpt: str | None = None,
    event_id: str = "event-user-1",
    conversation_id: str = "conversation-1",
) -> dict[str, object]:
    value = deepcopy(draft_dict())
    excerpt = text if excerpt is None else excerpt
    start = text.index(excerpt)
    value.update(
        {
            "schema_version": 1,
            "memory_id": "memory-1",
            "version": 1,
            "conversation_id": conversation_id,
            "proposal_run_id": "proposal-run-1",
            "proposal_ordinal": 0,
            "evidence": [
                {
                    "event_id": event_id,
                    "role": "support",
                    "excerpt_start": start,
                    "excerpt_end": start + len(excerpt),
                    "excerpt": excerpt,
                    "excerpt_sha256": hashlib.sha256(
                        excerpt.encode("utf-8")
                    ).hexdigest(),
                }
            ],
            "relations": value.pop("relation_suggestions"),
            "status": "proposed",
            "created_at": "2026-08-07T12:00:00Z",
            "idempotency_key": "proposal-run-1:0",
        }
    )
    value.pop("evidence_quotes")
    return value


def source(
    *,
    event_id: str = "event-user-1",
    conversation_id: str = "conversation-1",
    actor: str = "user",
    event_type: str = "message",
    committed: bool = True,
    text: str = "我下周可能去上海出差，还没完全定。",
) -> EvidenceSource:
    return EvidenceSource(
        event_id=event_id,
        conversation_id=conversation_id,
        actor=actor,
        event_type=event_type,
        committed=committed,
        text=text,
    )

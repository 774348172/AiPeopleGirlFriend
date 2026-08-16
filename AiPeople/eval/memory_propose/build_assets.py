from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SCHEMA_DIR = ROOT / "runtime" / "schemas"
EVAL_DIR = ROOT / "eval" / "memory_propose"


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _input_schema() -> dict[str, object]:
    identifier = {"type": "string", "minLength": 1, "maxLength": 256, "pattern": r"^\S+$"}
    subject = {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "entity_id", "display_name"],
        "properties": {
            "type": {
                "enum": ["player", "character", "both", "relationship", "third_party"]
            },
            "entity_id": {"anyOf": [identifier, {"type": "null"}]},
            "display_name": {
                "anyOf": [
                    {"type": "string", "minLength": 1, "maxLength": 100},
                    {"type": "null"},
                ]
            },
        },
    }
    event = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "event_id",
            "conversation_id",
            "sequence_no",
            "actor",
            "event_type",
            "occurred_at",
            "timezone",
            "text",
            "commit_state",
        ],
        "properties": {
            "event_id": identifier,
            "conversation_id": identifier,
            "sequence_no": {"type": "integer", "minimum": 0},
            "actor": {"enum": ["user", "character", "system"]},
            "event_type": {"enum": ["message", "app_event", "offscreen_event"]},
            "occurred_at": {
                "type": "string",
                "format": "date-time",
                "pattern": "Z$",
            },
            "timezone": {"type": "string", "minLength": 1, "maxLength": 128},
            "text": {"type": "string", "minLength": 1, "maxLength": 4000},
            "commit_state": {"const": "committed"},
        },
    }
    memory = {
        "type": "object",
        "additionalProperties": False,
        "required": ["memory_id", "conversation_id", "kind", "statement", "subject", "status"],
        "properties": {
            "memory_id": identifier,
            "conversation_id": identifier,
            "kind": {
                "enum": [
                    "player_fact",
                    "preference_boundary",
                    "person_relation",
                    "shared_experience",
                    "relationship_meaning",
                    "future_event",
                    "unfinished_topic",
                    "character_self_claim",
                ]
            },
            "statement": {"type": "string", "minLength": 1, "maxLength": 500},
            "subject": subject,
            "status": {"enum": ["active", "disputed"]},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://aipeople.local/schemas/memory_propose_request_v1.schema.json",
        "title": "MEMORY_PROPOSE request v1",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "mode",
            "proposal_run_id",
            "conversation_id",
            "from_sequence_no",
            "through_sequence_no",
            "events",
            "existing_memories",
            "max_proposals",
        ],
        "properties": {
            "schema_version": {"const": 1},
            "mode": {"const": "MEMORY_PROPOSE"},
            "proposal_run_id": identifier,
            "conversation_id": identifier,
            "from_sequence_no": {"type": "integer", "minimum": 0},
            "through_sequence_no": {"type": "integer", "minimum": 0},
            "events": {"type": "array", "minItems": 1, "maxItems": 32, "items": event},
            "existing_memories": {
                "type": "array",
                "maxItems": 32,
                "items": memory,
            },
            "max_proposals": {"const": 8},
        },
    }


def _result_schema() -> dict[str, object]:
    proposal = json.loads(
        (RUNTIME_SCHEMA_DIR / "memory_proposal_draft_v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    proposal.pop("$schema", None)
    proposal.pop("$id", None)
    proposal.pop("title", None)
    proposal_defs = proposal.pop("$defs")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://aipeople.local/schemas/memory_propose_result_v1.schema.json",
        "title": "MEMORY_PROPOSE result v1",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "mode", "proposal_run_id", "proposals"],
        "properties": {
            "schema_version": {"const": 1},
            "mode": {"const": "MEMORY_PROPOSE"},
            "proposal_run_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256,
                "pattern": r"^\S+$",
            },
            "proposals": {
                "type": "array",
                "minItems": 0,
                "maxItems": 8,
                "items": {"$ref": "#/$defs/proposal"},
            },
        },
        "$defs": {"proposal": proposal, **proposal_defs},
    }


def _request(*, with_existing: bool = False) -> dict[str, object]:
    request = {
        "schema_version": 1,
        "mode": "MEMORY_PROPOSE",
        "proposal_run_id": "proposal-run-fixture",
        "conversation_id": "conversation-fixture",
        "from_sequence_no": 10,
        "through_sequence_no": 11,
        "events": [
            {
                "event_id": "event-user-10",
                "conversation_id": "conversation-fixture",
                "sequence_no": 10,
                "actor": "user",
                "event_type": "message",
                "occurred_at": "2026-08-07T07:00:00Z",
                "timezone": "Asia/Shanghai",
                "text": "我下周可能去上海出差，还没定。",
                "commit_state": "committed",
            },
            {
                "event_id": "event-character-11",
                "conversation_id": "conversation-fixture",
                "sequence_no": 11,
                "actor": "character",
                "event_type": "message",
                "occurred_at": "2026-08-07T07:00:05Z",
                "timezone": "Asia/Shanghai",
                "text": "还没定的话，就先别急着替未来的自己收拾行李。",
                "commit_state": "committed",
            },
        ],
        "existing_memories": [],
        "max_proposals": 8,
    }
    if with_existing:
        request["existing_memories"] = [
            {
                "memory_id": "memory-old-trip",
                "conversation_id": "conversation-fixture",
                "kind": "future_event",
                "statement": "玩家此前表示下周确定去北京出差",
                "subject": {"type": "player", "entity_id": None, "display_name": None},
                "status": "active",
            }
        ]
    return request


def _proposal() -> dict[str, object]:
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
            "anchor_event_id": "event-user-10",
            "start_at": None,
            "end_at": None,
            "timezone": None,
            "precision": "unknown",
        },
        "evidence_quotes": [
            {
                "event_id": "event-user-10",
                "role": "support",
                "quote": "我下周可能去上海出差，还没定。",
                "start_hint": 0,
            }
        ],
        "semantic_reason": "后续对话中可能自然关心出差是否确定",
        "confidence": 0.94,
        "relation_suggestions": {
            "semantic_slot": "player.future.travel",
            "supersedes": [],
            "contradicts": [],
            "refines": [],
        },
    }


def _result(proposals: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "MEMORY_PROPOSE",
        "proposal_run_id": "proposal-run-fixture",
        "proposals": proposals,
    }


def _fixtures() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    proposal = _proposal()
    correction = deepcopy(proposal)
    correction["statement"] = "玩家改口表示下周可能去上海，而非此前确定去北京"
    correction["evidence_quotes"][0]["role"] = "correction"
    correction["relation_suggestions"]["supersedes"] = ["memory-old-trip"]
    correction_request = _request(with_existing=True)
    correction_text = "我刚才说错了，不是去北京，是下周可能去上海，还没定。"
    correction_request["events"][0]["text"] = correction_text
    correction["evidence_quotes"][0]["quote"] = correction_text

    valid = [
        {
            "case_id": "important-future",
            "request": _request(),
            "output": _result([proposal]),
        },
        {
            "case_id": "unimportant-empty",
            "request": _request(),
            "output": _result([]),
        },
        {
            "case_id": "correction-existing-target",
            "request": correction_request,
            "output": _result([correction]),
        },
    ]

    def invalid(case_id: str, output: object, error: str, request=None):
        return {
            "case_id": case_id,
            "request": request or _request(),
            "raw_output": output
            if isinstance(output, str)
            else json.dumps(output, ensure_ascii=False, separators=(",", ":")),
            "expected_error": error,
        }

    wrong_run = _result([])
    wrong_run["proposal_run_id"] = "other-run"
    outside_evidence = deepcopy(proposal)
    outside_evidence["evidence_quotes"][0]["event_id"] = "event-outside"
    bad_quote = deepcopy(proposal)
    bad_quote["evidence_quotes"][0]["quote"] = "模型编造的原文"
    outside_relation = deepcopy(proposal)
    outside_relation["relation_suggestions"]["supersedes"] = ["memory-outside"]
    forbidden = deepcopy(proposal)
    forbidden["status"] = "active"

    invalid_cases = [
        invalid("markdown-fence", "```json\n{}\n```", "memory_propose_invalid_json"),
        invalid("role-text", "秦未晞：我会记住的", "memory_propose_invalid_json"),
        invalid("partial-json", '{"schema_version":1', "memory_propose_invalid_json"),
        invalid("wrong-run", wrong_run, "memory_propose_contract_invalid"),
        invalid("outside-evidence", _result([outside_evidence]), "memory_propose_evidence_outside_input"),
        invalid("quote-mismatch", _result([bad_quote]), "memory_propose_evidence_outside_input"),
        invalid("outside-relation", _result([outside_relation]), "memory_propose_relation_outside_input"),
        invalid("forbidden-runtime-field", _result([forbidden]), "memory_propose_schema_invalid"),
        invalid(
            "duplicate-json-field",
            '{"schema_version":1,"mode":"MEMORY_PROPOSE","proposal_run_id":"proposal-run-fixture","proposal_run_id":"again","proposals":[]}',
            "memory_propose_invalid_json",
        ),
    ]
    return valid, invalid_cases


def _profile() -> dict[str, object]:
    return {
        "profile_id": "memory-propose-mode-v1",
        "schema_version": 1,
        "mode": "MEMORY_PROPOSE",
        "model": {
            "role": "shared_main_model",
            "family": "Qwen3.5-4B",
            "stream": False,
            "thinking": False,
        },
        "sampling": {
            "temperature": 0.1,
            "top_p": 0.8,
            "max_output_tokens": 2048,
        },
        "structured_output": {
            "mechanism": "json_schema_grammar",
            "schema": "runtime/schemas/memory_propose_result_v1.schema.json",
            "allow_empty_proposals": True,
            "allow_visible_text": False,
            "allow_partial_result": False,
        },
        "scheduling": {
            "path": "background_after_committed_reply",
            "preemptible_by_player_turn": True,
            "blocks_reply": False,
            "shares_main_model_slot": True,
        },
        "failure_policy": {
            "commit_on_failure": False,
            "keyword_fallback": False,
            "partial_acceptance": False,
            "raw_output_in_error": False,
            "retryable": ["memory_propose_timeout", "memory_propose_model_unavailable"],
        },
    }


def main() -> None:
    valid, invalid = _fixtures()
    _write(RUNTIME_SCHEMA_DIR / "memory_propose_request_v1.schema.json", _input_schema())
    _write(RUNTIME_SCHEMA_DIR / "memory_propose_result_v1.schema.json", _result_schema())
    _write(EVAL_DIR / "mem02_mode_profile_v1.json", _profile())
    _write(EVAL_DIR / "fixtures" / "valid" / "mode_cases_v1.json", valid)
    _write(EVAL_DIR / "fixtures" / "invalid" / "mode_cases_v1.json", invalid)


if __name__ == "__main__":
    main()

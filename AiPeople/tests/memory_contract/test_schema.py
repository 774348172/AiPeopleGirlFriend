from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from runtime import _memory_contracts as vocabulary


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "runtime" / "schemas"
DRAFT_SCHEMA_PATH = SCHEMA_DIR / "memory_proposal_draft_v1.schema.json"
REPRESENTATION_SCHEMA_PATH = SCHEMA_DIR / "memory_representation_v1.schema.json"


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(path: Path) -> Draft202012Validator:
    schema = _load(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _draft() -> dict[str, object]:
    return {
        "kind": "future_event",
        "statement": "玩家表示下周可能去上海出差，但尚未确定",
        "subject": {
            "type": "player",
            "entity_id": None,
            "display_name": None,
        },
        "epistemic": {
            "polarity": "affirmed",
            "modality": "uncertain",
            "grounding": "speaker_report",
        },
        "temporal": {
            "relation": "future",
            "resolution": "ambiguous",
            "source_text": "下周",
            "anchor_event_id": "event-user-123",
            "start_at": None,
            "end_at": None,
            "timezone": None,
            "precision": "unknown",
        },
        "evidence_quotes": [
            {
                "event_id": "event-user-123",
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


def _representation() -> dict[str, object]:
    value = _draft()
    value["schema_version"] = 1
    value["memory_id"] = "memory-123"
    value["version"] = 1
    value["conversation_id"] = "conversation-123"
    value["proposal_run_id"] = "proposal-run-123"
    value["proposal_ordinal"] = 0
    value["evidence"] = [
        {
            "event_id": "event-user-123",
            "role": "support",
            "excerpt_start": 0,
            "excerpt_end": 20,
            "excerpt": "我下周可能去上海出差，还没完全定。",
            "excerpt_sha256": "a" * 64,
        }
    ]
    value["relations"] = value.pop("relation_suggestions")
    value.pop("evidence_quotes")
    value["status"] = "proposed"
    value["created_at"] = "2026-08-07T12:00:00Z"
    value["idempotency_key"] = "proposal-run-123:0"
    return value


def test_schemas_are_valid_draft_2020_12_and_examples_pass() -> None:
    _validator(DRAFT_SCHEMA_PATH).validate(_draft())
    _validator(REPRESENTATION_SCHEMA_PATH).validate(_representation())


@pytest.mark.parametrize(
    ("schema_path", "factory", "missing"),
    [
        (DRAFT_SCHEMA_PATH, _draft, "kind"),
        (DRAFT_SCHEMA_PATH, _draft, "evidence_quotes"),
        (REPRESENTATION_SCHEMA_PATH, _representation, "memory_id"),
        (REPRESENTATION_SCHEMA_PATH, _representation, "status"),
    ],
)
def test_required_top_level_fields_are_enforced(
    schema_path: Path, factory: object, missing: str
) -> None:
    value = factory()
    value.pop(missing)
    with pytest.raises(ValidationError, match="required property"):
        _validator(schema_path).validate(value)


@pytest.mark.parametrize(
    ("schema_path", "factory", "path"),
    [
        (DRAFT_SCHEMA_PATH, _draft, ()),
        (DRAFT_SCHEMA_PATH, _draft, ("subject",)),
        (DRAFT_SCHEMA_PATH, _draft, ("evidence_quotes", 0)),
        (REPRESENTATION_SCHEMA_PATH, _representation, ("temporal",)),
        (REPRESENTATION_SCHEMA_PATH, _representation, ("evidence", 0)),
        (REPRESENTATION_SCHEMA_PATH, _representation, ("relations",)),
    ],
)
def test_unknown_fields_are_rejected_everywhere(
    schema_path: Path, factory: object, path: tuple[object, ...]
) -> None:
    value = factory()
    target = value
    for part in path:
        target = target[part]
    target["unexpected"] = True
    with pytest.raises(ValidationError, match="Additional properties"):
        _validator(schema_path).validate(value)


def test_draft_rejects_runtime_owned_fields() -> None:
    validator = _validator(DRAFT_SCHEMA_PATH)
    for name, value in (
        ("memory_id", "memory-123"),
        ("status", "active"),
        ("created_at", "2026-08-07T12:00:00Z"),
        ("evidence", []),
    ):
        draft = _draft()
        draft[name] = value
        with pytest.raises(ValidationError, match="Additional properties"):
            validator.validate(draft)


def test_representation_rejects_draft_only_evidence_fields() -> None:
    value = _representation()
    value["evidence_quotes"] = _draft()["evidence_quotes"]
    with pytest.raises(ValidationError, match="Additional properties"):
        _validator(REPRESENTATION_SCHEMA_PATH).validate(value)


def test_format_and_sha256_constraints_are_enforced() -> None:
    validator = _validator(REPRESENTATION_SCHEMA_PATH)
    invalid_created = _representation()
    invalid_created["created_at"] = "2026-08-07 12:00:00"
    with pytest.raises(ValidationError):
        validator.validate(invalid_created)

    non_utc_created = _representation()
    non_utc_created["created_at"] = "2026-08-07T20:00:00+08:00"
    with pytest.raises(ValidationError):
        validator.validate(non_utc_created)

    invalid_hash = _representation()
    invalid_hash["evidence"][0]["excerpt_sha256"] = "A" * 64
    with pytest.raises(ValidationError):
        validator.validate(invalid_hash)


def test_enums_and_limits_are_bound_to_mem01a_vocabulary() -> None:
    draft = _load(DRAFT_SCHEMA_PATH)
    representation = _load(REPRESENTATION_SCHEMA_PATH)
    assert tuple(draft["$defs"]["memoryKind"]["enum"]) == vocabulary.MEMORY_KINDS
    assert tuple(representation["properties"]["status"]["enum"]) == vocabulary.MEMORY_STATUSES
    assert tuple(draft["$defs"]["subject"]["properties"]["type"]["enum"]) == vocabulary.SUBJECT_TYPES
    assert tuple(draft["$defs"]["epistemic"]["properties"]["polarity"]["enum"]) == vocabulary.EPISTEMIC_POLARITIES
    assert tuple(draft["$defs"]["epistemic"]["properties"]["modality"]["enum"]) == vocabulary.EPISTEMIC_MODALITIES
    assert tuple(draft["$defs"]["epistemic"]["properties"]["grounding"]["enum"]) == vocabulary.EPISTEMIC_GROUNDINGS
    assert tuple(draft["$defs"]["temporalDraft"]["properties"]["relation"]["enum"]) == vocabulary.TEMPORAL_RELATIONS
    assert tuple(draft["$defs"]["temporalDraft"]["properties"]["resolution"]["enum"]) == vocabulary.TEMPORAL_RESOLUTIONS
    assert tuple(draft["$defs"]["temporalDraft"]["properties"]["precision"]["enum"]) == vocabulary.TEMPORAL_PRECISIONS
    assert draft["properties"]["statement"]["maxLength"] == vocabulary.FIELD_LIMITS["statement_chars_max"]
    assert draft["properties"]["evidence_quotes"]["maxItems"] == vocabulary.FIELD_LIMITS["evidence_count_max"]


def test_schema_field_sets_match_frozen_vocabulary() -> None:
    draft = _load(DRAFT_SCHEMA_PATH)
    representation = _load(REPRESENTATION_SCHEMA_PATH)
    assert tuple(draft["required"]) == vocabulary.DRAFT_FIELDS
    assert set(draft["properties"]) == set(vocabulary.DRAFT_FIELDS)
    assert tuple(representation["required"]) == vocabulary.REPRESENTATION_FIELDS
    assert set(representation["properties"]) == set(vocabulary.REPRESENTATION_FIELDS)


def test_shared_definitions_cannot_drift_between_schemas() -> None:
    draft_defs = _load(DRAFT_SCHEMA_PATH)["$defs"]
    representation_defs = _load(REPRESENTATION_SCHEMA_PATH)["$defs"]
    for name in (
        "identifier",
        "nullableIdentifier",
        "memoryKind",
        "subject",
        "epistemic",
        "relationTargets",
        "relations",
    ):
        assert draft_defs[name] == representation_defs[name]
    for field in ("relation", "resolution", "precision"):
        assert (
            draft_defs["temporalDraft"]["properties"][field]
            == representation_defs["temporalMaterialized"]["properties"][field]
        )
    assert (
        draft_defs["evidenceQuote"]["properties"]["role"]
        == representation_defs["evidence"]["properties"]["role"]
    )


def test_schema_does_not_expose_reminder_or_notification_fields() -> None:
    forbidden = {"due_at", "reminder", "timer", "notification", "send_at"}

    def property_names(value: object) -> set[str]:
        if isinstance(value, dict):
            names = set(value.get("properties", {}))
            for nested in value.values():
                names.update(property_names(nested))
            return names
        if isinstance(value, list):
            names: set[str] = set()
            for nested in value:
                names.update(property_names(nested))
            return names
        return set()

    assert property_names(_load(DRAFT_SCHEMA_PATH)).isdisjoint(forbidden)
    assert property_names(_load(REPRESENTATION_SCHEMA_PATH)).isdisjoint(forbidden)


def test_schema_files_are_declared_as_runtime_package_data() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"schemas/*.json"' in pyproject

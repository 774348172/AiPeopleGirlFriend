"""Strict production ``judge_payload`` to V5 training projection."""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "schemas"
    / "mode_v5"
    / "runtime_grounded_scenario.schema.json"
)

MODEL_VIEW_KEYS = frozenset(
    {
        "mode",
        "evidence_contract",
        "snapshot",
        "previous_heroine_runtime",
        "selected_memory_frame",
        "current_protagonist_utterance",
        "recent_dialogue",
        "pending_actions",
        "game_feedback",
        "player_fact_reports",
        "unknown_facts",
        "authoritative_world",
    }
)
# authoritative_world is a new generator-side projection.  It is optional for
# legacy runtime payloads until the production payload publisher is migrated;
# new generated scenarios always include it.
REQUIRED_MODEL_VIEW_KEYS = MODEL_VIEW_KEYS - {"player_fact_reports", "unknown_facts", "authoritative_world"}
FULL_JUDGE_PAYLOAD_KEYS = (MODEL_VIEW_KEYS - {"authoritative_world"}) | {"available_actions"}


class ProjectionContractError(ValueError):
    """The runtime payload no longer matches the frozen V5 projection."""


@lru_cache(maxsize=1)
def _model_view_validator() -> Draft7Validator:
    scenario_schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$ref": "#/definitions/ModelView",
        "definitions": scenario_schema["definitions"],
    }
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


def project_judge_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove only the action manifest and validate the exact remaining shape.

    Unknown fields fail closed so a production payload change requires an
    explicit training-contract decision instead of silently drifting.
    """

    if not isinstance(payload, dict):
        raise ProjectionContractError("judge_payload must be an object")
    actual = set(payload)
    if actual != FULL_JUDGE_PAYLOAD_KEYS:
        missing = sorted(FULL_JUDGE_PAYLOAD_KEYS - actual)
        unknown = sorted(actual - FULL_JUDGE_PAYLOAD_KEYS)
        raise ProjectionContractError(
            f"judge_payload top-level drift: missing={missing}, unknown={unknown}"
        )
    model_view = copy.deepcopy(payload)
    del model_view["available_actions"]
    if not set(model_view).issubset(MODEL_VIEW_KEYS):
        raise ProjectionContractError("projection removed or retained unexpected fields")
    errors = [
        f"$.{'.'.join(map(str, error.absolute_path))}:{error.message}"
        for error in sorted(
            _model_view_validator().iter_errors(model_view),
            key=lambda item: list(item.absolute_path),
        )
    ]
    if errors:
        raise ProjectionContractError("; ".join(errors))
    return model_view

"""Schema-backed contracts shared by the V5 offline generation path."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas" / "mode_v5"


class RuntimeGroundedContractError(ValueError):
    """Raised when a teacher target or semantic audit violates its schema."""

    def __init__(self, contract: str, errors: list[str]) -> None:
        self.contract = contract
        self.errors = tuple(errors)
        super().__init__(f"{contract}: " + "; ".join(errors))


@lru_cache(maxsize=None)
def _validator(schema_name: str) -> Draft7Validator:
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


def _errors(schema_name: str, value: Any) -> list[str]:
    return [
        f"$.{'.'.join(map(str, error.absolute_path))}:{error.message}"
        for error in sorted(
            _validator(schema_name).iter_errors(value),
            key=lambda item: list(item.absolute_path),
        )
    ]


def validate_teacher_target(value: dict[str, Any]) -> None:
    errors = _errors("runtime_grounded_teacher_target.schema.json", value)
    if errors:
        raise RuntimeGroundedContractError("teacher_target", errors)


def validate_semantic_audit(value: dict[str, Any]) -> None:
    errors = _errors("runtime_grounded_audit.schema.json", value)
    if errors:
        raise RuntimeGroundedContractError("semantic_audit", errors)


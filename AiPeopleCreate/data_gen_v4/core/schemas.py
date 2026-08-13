"""schema 加载与 jsonschema 校验封装。

阶段 0 冻结的五份合同（data_gen_v4/schemas/）是 record/plan/package/lock/evidence
的唯一校验依据；核心实现不重复定义字段规则，避免与合同漂移。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft7Validator

from .errors import SchemaValidationError

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "schemas"

_PACKAGE_SCHEMA = "package_v4.schema.json"
_RECORD_SCHEMA = "record_v4.schema.json"
_PLAN_SCHEMA = "plan_v4.schema.json"
_LOCK_SCHEMA = "lock_v4.schema.json"
_EVIDENCE_SCHEMA = "evidence_v4.schema.json"


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict:
    path = SCHEMAS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"冻结 schema 缺失: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(name: str) -> Draft7Validator:
    doc = load_schema(name)
    return Draft7Validator(doc)


def _validate(name: str, instance: dict, what: str) -> None:
    errors = sorted(_validator(name).iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(
            f"{'/'.join(str(p) for p in e.path) or '$'}: {e.message}" for e in errors[:5]
        )
        raise SchemaValidationError(f"{what} 未通过 {name}: {detail}")


def validate_package(package: dict) -> None:
    _validate(_PACKAGE_SCHEMA, package, "package")


def validate_record(record: dict) -> None:
    _validate(_RECORD_SCHEMA, record, "record")


def validate_plan(plan: dict) -> None:
    _validate(_PLAN_SCHEMA, plan, "plan")


def validate_plan_item(item: dict) -> None:
    """按 plan_v4.schema.json 的 #/definitions/PlanItemV4 校验单个 plan item。"""
    doc = load_schema(_PLAN_SCHEMA)
    errors = sorted(
        Draft7Validator(doc).iter_errors(item, {"$ref": "#/definitions/PlanItemV4"}),
        key=lambda e: list(e.path),
    )
    if errors:
        detail = "; ".join(
            f"{'/'.join(str(p) for p in e.path) or '$'}: {e.message}" for e in errors[:5]
        )
        raise SchemaValidationError(f"plan item 未通过 {_PLAN_SCHEMA}: {detail}")


def validate_lock(lock: dict) -> None:
    _validate(_LOCK_SCHEMA, lock, "lock")


def validate_evidence(evidence: dict) -> None:
    _validate(_EVIDENCE_SCHEMA, evidence, "evidence unit")

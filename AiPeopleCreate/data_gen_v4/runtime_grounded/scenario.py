"""Cross-field validation for the V5 runtime-grounded ScenarioSpec."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft7Validator


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "schemas"
    / "mode_v5"
    / "runtime_grounded_scenario.schema.json"
)

TASK_DIRECTIONS = {
    "reply_accept_authoritative_update": "accept_authoritative_update",
    "reply_reject_stale_claim": "reject_stale_claim",
    "reply_correct_false_premise": "correct_false_premise",
    "reply_resolve_world_memory_conflict": "prefer_world_over_memory",
    "reply_confirm_current_state": "confirm_current_state",
    "reply_subject_attribution": "preserve_subject",
    "reply_insufficient_information": "ask_for_clarification",
    "reply_direct_answer": "answer_directly",
}

_ORACLE_ONLY_KEYS = frozenset(
    {
        "oracle_view",
        "required_assertions",
        "forbidden_assertions",
        "correction_direction",
        "answer_shape",
        "must_not_be_vague",
        "unsupported_detail_policy",
        "reference_answer",
        "judge_score",
        "audit_reason",
    }
)


class ScenarioContractError(ValueError):
    """Raised when a ScenarioSpec violates schema or V5 invariants."""

    def __init__(self, errors: Iterable[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


@lru_cache(maxsize=1)
def _validator() -> Draft7Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


def _path_text(path: Iterable[Any]) -> str:
    values = [str(part) for part in path]
    return "$" if not values else "$." + ".".join(values)


def _oracle_keys_in_model_view(value: Any, path: str = "model_view") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in _ORACLE_ONLY_KEYS:
                errors.append(f"oracle_leak:{child_path}")
            errors.extend(_oracle_keys_in_model_view(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_oracle_keys_in_model_view(child, f"{path}[{index}]"))
    return errors


def _semantic_errors(scenario: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    task_type = scenario.get("task_type")
    oracle = scenario.get("oracle_view")
    model_view = scenario.get("model_view")
    if isinstance(model_view, dict):
        errors.extend(_oracle_keys_in_model_view(model_view))
    if not isinstance(oracle, dict):
        return errors

    expected_direction = TASK_DIRECTIONS.get(str(task_type))
    actual_direction = oracle.get("correction_direction")
    if expected_direction and actual_direction != expected_direction:
        errors.append(
            f"direction_mismatch:{task_type}:{actual_direction}!={expected_direction}"
        )

    response_contract = oracle.get("response_contract")
    if isinstance(response_contract, dict):
        expected_unknown = (
            "required" if task_type == "reply_insufficient_information" else "not_applicable"
        )
        expected_binding = (
            "required" if task_type == "reply_subject_attribution" else "not_applicable"
        )
        if response_contract.get("unknown_acknowledgement") != expected_unknown:
            errors.append(
                "response_contract_unknown_mismatch:"
                f"{response_contract.get('unknown_acknowledgement')}!={expected_unknown}"
            )
        if response_contract.get("subject_proposition_binding") != expected_binding:
            errors.append(
                "response_contract_subject_binding_mismatch:"
                f"{response_contract.get('subject_proposition_binding')}!={expected_binding}"
            )

    facts = oracle.get("facts")
    if not isinstance(facts, list):
        return errors

    fact_ids: set[str] = set()
    current_values: dict[tuple[str, str], str] = {}
    stale_values: dict[tuple[str, str], set[str]] = {}
    has_resolvable_fact = False
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        fact_id = fact.get("fact_id")
        if isinstance(fact_id, str):
            if fact_id in fact_ids:
                errors.append(f"duplicate_fact_id:{fact_id}")
            fact_ids.add(fact_id)
        subject = fact.get("subject")
        predicate = fact.get("predicate")
        value = fact.get("object")
        status = fact.get("status")
        if not all(isinstance(item, str) for item in (subject, predicate, value, status)):
            continue
        key = (subject, predicate)
        if status in {"current", "unknown"}:
            has_resolvable_fact = True
        if status == "current":
            previous = current_values.get(key)
            if previous is not None and previous != value:
                errors.append(
                    f"conflicting_current:{subject}:{predicate}:{previous}!={value}"
                )
            current_values[key] = value
        elif status in {"stale", "false"}:
            stale_values.setdefault(key, set()).add(value)

    if not has_resolvable_fact:
        errors.append("missing_current_or_unknown_fact")
    for key, current_value in current_values.items():
        if current_value in stale_values.get(key, set()):
            errors.append(f"same_value_current_and_stale:{key[0]}:{key[1]}:{current_value}")

    for list_name in ("required_assertions", "forbidden_assertions"):
        assertions = oracle.get(list_name)
        if not isinstance(assertions, list):
            continue
        for index, assertion in enumerate(assertions):
            if not isinstance(assertion, dict):
                continue
            refs = assertion.get("source_fact_ids")
            if not isinstance(refs, list):
                continue
            for fact_id in refs:
                if fact_id not in fact_ids:
                    errors.append(f"unknown_fact_ref:{list_name}[{index}]:{fact_id}")

    anchors = scenario.get("split_anchors")
    expected_task_anchor = f"task:{task_type}"
    if isinstance(anchors, list) and expected_task_anchor not in anchors:
        errors.append(f"missing_task_split_anchor:{expected_task_anchor}")
    return errors


def scenario_contract_errors(scenario: dict[str, Any]) -> tuple[str, ...]:
    """Return stable schema and cross-field errors without mutating the scenario."""

    errors = [
        f"schema:{_path_text(error.absolute_path)}:{error.message}"
        for error in sorted(_validator().iter_errors(scenario), key=lambda item: list(item.absolute_path))
    ]
    errors.extend(_semantic_errors(scenario))
    return tuple(errors)


def validate_scenario(scenario: dict[str, Any]) -> None:
    """Validate one fully expanded ScenarioSpec or raise ScenarioContractError."""

    errors = scenario_contract_errors(scenario)
    if errors:
        raise ScenarioContractError(errors)


def validate_scenario_collection(scenarios: Iterable[dict[str, Any]]) -> None:
    """Validate a fixture collection and reject duplicate scenario IDs."""

    errors: list[str] = []
    scenario_ids: set[str] = set()
    for index, scenario in enumerate(scenarios):
        errors.extend(f"scenario[{index}]:{item}" for item in scenario_contract_errors(scenario))
        scenario_id = scenario.get("scenario_id")
        if isinstance(scenario_id, str):
            if scenario_id in scenario_ids:
                errors.append(f"duplicate_scenario_id:{scenario_id}")
            scenario_ids.add(scenario_id)
    if errors:
        raise ScenarioContractError(errors)

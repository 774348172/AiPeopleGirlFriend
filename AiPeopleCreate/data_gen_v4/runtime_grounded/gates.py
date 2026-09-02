"""V5 runtime-grounded RG0-RG10 hard gates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .contracts import validate_semantic_audit, validate_teacher_target
from .projection import MODEL_VIEW_KEYS
from .scenario import validate_scenario

RG_GATE_IDS = tuple(f"RG{index}" for index in range(11))


@dataclass(frozen=True, slots=True)
class RuntimeGroundedGateReport:
    verdicts: dict[str, bool]
    reasons: dict[str, tuple[str, ...]]
    decision: str

    @property
    def approved(self) -> bool:
        return self.decision == "approve"

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdicts": dict(self.verdicts),
            "reasons": {key: list(values) for key, values in self.reasons.items()},
            "decision": self.decision,
        }


def _claim_key(claim: dict[str, Any]) -> tuple[Any, ...]:
    return (
        claim.get("subject"),
        claim.get("predicate"),
        claim.get("object"),
        claim.get("polarity"),
        claim.get("temporal_status"),
        tuple(sorted(claim.get("source_fact_ids") or [])),
    )


def _assertion_key(assertion: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(assertion.get("subject", "")),
        str(assertion.get("predicate", "")),
        str(assertion.get("object", "")),
    )


def _claim_assertion_key(claim: dict[str, Any]) -> tuple[str, str, str] | None:
    if claim.get("polarity") != "positive":
        return None
    if claim.get("temporal_status") not in {"current", "unknown"}:
        return None
    return (
        str(claim.get("subject", "")),
        str(claim.get("predicate", "")),
        str(claim.get("object", "")),
    )


def _spoken_only_errors(reply: str) -> list[str]:
    stripped = reply.strip()
    errors: list[str] = []
    if not stripped:
        return ["empty_reply"]
    if "```" in stripped or (stripped.startswith("{") and stripped.endswith("}")):
        errors.append("structured_output_leak")
    if re.search(r"(^|\n)\s*(?:[-*]\s|#{1,6}\s|(?:玩家|角色|assistant|user)\s*[:：])", stripped):
        errors.append("non_dialogue_format")
    if re.search(r"\*[^*]+\*|（[^）]*(?:动作|神态|表情)[^）]*）", stripped):
        errors.append("action_narration_format")
    return errors


_UNKNOWN_ACK_RE = re.compile(
    r"(?:不知道|不清楚|不太清楚|记不清|没记清|记不太清|不太记得|不确定|不太确定|"
    r"无法确定|没法确定|想不起|没印象|没有可靠记录|没有具体记录|没有历史记录)"
)
_CONCRETE_DATE_RE = re.compile(
    r"(?:\d{1,2}\s*月\s*\d{1,2}\s*[日号]|"
    r"[一二三四五六七八九十两零〇]+月[一二三四五六七八九十两零〇]+[日号]|"
    r"前?周[一二三四五六七日天]|星期[一二三四五六七日天])"
)
_HIGH_RISK_EXPANSIONS = (
    "每天",
    "每次",
    "一直",
    "永远",
    "从来",
    "都会",
    "都有",
    "已经通知",
    "现在就通知",
    "已经安排",
    "已经联系",
    "我查过",
    "我确认过",
)


def _surface_value_present(value: str, reply: str) -> bool:
    if not value or value in reply:
        return True
    compact_value = re.sub(r"[的现在目前正处于进行是上了在中]", "", value)
    compact_reply = re.sub(r"[的现在目前正处于进行是上了在中]", "", reply)
    if compact_value and compact_value in compact_reply:
        return True
    if "顶层" in value and "最上面" in reply:
        return True
    if value.startswith("晚上") and value[2:] in reply:
        return True
    return False


def _model_view_text(model_view: dict[str, Any]) -> str:
    return str(model_view)


def _surface_semantic_errors(
    scenario: dict[str, Any], reply: str
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Detect meaning lost between structured claims and Chinese surface text."""

    subject_errors: list[str] = []
    support_errors: list[str] = []
    shape_errors: list[str] = []
    required_errors: list[str] = []
    model_view = scenario["model_view"]
    utterance = model_view["current_protagonist_utterance"]
    required = scenario["oracle_view"]["required_assertions"]
    contract = scenario["oracle_view"].get("response_contract") or {}

    if "我的" in utterance and "我的" in reply:
        subject_errors.append("protagonist_first_person_possession_retained_by_heroine")

    protagonist_required = any(item["subject"] == "protagonist" for item in required)
    if protagonist_required and re.search(
        r"我[^，。！？]{0,12}(?:查|确认|核对|调|改|吃|喝|喜欢|放)", reply
    ):
        support_errors.append("unsupported_heroine_first_person_action_or_preference")

    if scenario["task_type"] == "reply_subject_attribution":
        speakers = [
            line.split("：", 1)[0].strip()
            for line in model_view.get("recent_dialogue", [])
            if "：" in line
        ]
        expected = next(
            (speaker for speaker in speakers if speaker not in {"男主", "白未晞"}),
            None,
        )
        if expected and expected not in reply:
            subject_errors.append(f"speaker_not_explicitly_named:{expected}")
        if contract.get("subject_proposition_binding", "required") == "required":
            for assertion in required:
                value = str(assertion.get("object", ""))
                if value and not _surface_value_present(value, reply):
                    required_errors.append(
                        f"subject_attribution_missing_proposition_surface:{value}"
                    )

    if scenario["task_type"] == "reply_insufficient_information":
        if contract.get("unknown_acknowledgement", "required") == "required" and not _UNKNOWN_ACK_RE.search(reply):
            shape_errors.append("unknown_acknowledgement_missing")
        if _CONCRETE_DATE_RE.search(reply):
            support_errors.append("unknown_reply_invented_concrete_date")
        if re.search(r"(?:出|有)问题", reply):
            support_errors.append("unknown_reply_invented_problem_state")
        if re.search(r"现在[^。！？]{0,12}(?:还好|正常|没事|没问题)", reply):
            support_errors.append("unknown_reply_invented_current_status")

    if contract.get("unsupported_expansion_policy", "reject") == "reject":
        source_text = _model_view_text(model_view)
        for marker in _HIGH_RISK_EXPANSIONS:
            if marker in reply and marker not in source_text:
                support_errors.append(f"unsupported_expansion_marker:{marker}")

    for assertion in required:
        value = str(assertion.get("object", ""))
        if scenario["task_type"] == "reply_insufficient_information":
            continue
        if value and not _surface_value_present(value, reply):
            reason = f"required_value_missing_from_surface:{value}"
            if reason not in required_errors:
                required_errors.append(reason)

    for assertion in required:
        if assertion["predicate"] != "location" or not assertion["object"]:
            continue
        value = re.escape(assertion["object"])
        copula = re.search(rf"(?:现在)?(?:是|为)\s*{value}", reply)
        locative = re.search(
            rf"(?:放在|挂在|位于|收在|(?<!现)在)[^。！？]{{0,12}}{value}",
            reply,
        )
        if copula and not locative:
            shape_errors.append(f"location_rendered_as_identity:{assertion['object']}")
    return subject_errors, support_errors, shape_errors, required_errors


def _claim_source_errors(
    claims: list[dict[str, Any]], facts: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    facts_by_id = {fact["fact_id"]: fact for fact in facts}
    unsupported: list[str] = []
    subject_errors: list[str] = []
    for index, claim in enumerate(claims):
        refs = claim.get("source_fact_ids") or []
        referenced = [facts_by_id[ref] for ref in refs if ref in facts_by_id]
        unknown = sorted(set(refs) - set(facts_by_id))
        if unknown:
            unsupported.append(f"claim[{index}].unknown_source:{unknown}")
            continue
        if not referenced:
            unsupported.append(f"claim[{index}].missing_source")
            continue
        subject_matches = [fact for fact in referenced if fact["subject"] == claim["subject"]]
        if not subject_matches:
            subject_errors.append(f"claim[{index}].subject_not_supported")
            continue
        predicate_matches = [
            fact for fact in subject_matches if fact["predicate"] == claim["predicate"]
        ]
        if not predicate_matches:
            unsupported.append(f"claim[{index}].predicate_not_supported")
            continue
        compatible = False
        for fact in predicate_matches:
            if fact["status"] == "unknown" and claim["temporal_status"] == "unknown":
                compatible = True
            elif claim["polarity"] == "negative" and fact["status"] in {"stale", "false"}:
                compatible = claim["object"] == fact["object"]
            elif claim["object"] == fact["object"]:
                compatible = (
                    fact["status"] == "current"
                    and claim["temporal_status"] == "current"
                ) or (
                    fact["status"] in {"stale", "false"}
                    and claim["temporal_status"] == "past"
                )
            if compatible:
                break
        if not compatible:
            unsupported.append(f"claim[{index}].value_or_time_not_supported")
    return unsupported, subject_errors


def evaluate_runtime_grounded_candidate(
    scenario: dict[str, Any],
    teacher_target: dict[str, Any],
    semantic_audit: dict[str, Any],
) -> RuntimeGroundedGateReport:
    """Evaluate one complete reply; semantic audit cannot override mechanics."""

    validate_scenario(scenario)
    validate_teacher_target(teacher_target)
    validate_semantic_audit(semantic_audit)

    reasons: dict[str, list[str]] = {gate: [] for gate in RG_GATE_IDS}
    verdicts = {gate: True for gate in RG_GATE_IDS}

    model_view = scenario["model_view"]
    if set(model_view) != set(MODEL_VIEW_KEYS):
        reasons["RG3"].append("runtime_context_shape_drift")

    reply = teacher_target["reply"]
    spoken_errors = _spoken_only_errors(reply)
    if spoken_errors:
        reasons["RG4"].extend(spoken_errors)
    surface_subject, surface_support, surface_shape, surface_required = _surface_semantic_errors(
        scenario, reply
    )
    reasons["RG5"].extend(surface_required)
    reasons["RG7"].extend(surface_subject)
    reasons["RG6"].extend(surface_support)
    reasons["RG8"].extend(surface_shape)

    extracted = semantic_audit["extracted_claims"]
    declared = teacher_target["declared_claims"]
    extracted_assertions = {
        key for claim in extracted if (key := _claim_assertion_key(claim)) is not None
    }
    required = {
        _assertion_key(assertion)
        for assertion in scenario["oracle_view"]["required_assertions"]
    }
    forbidden = {
        _assertion_key(assertion)
        for assertion in scenario["oracle_view"]["forbidden_assertions"]
    }
    missing_required = sorted(required - extracted_assertions)
    hit_forbidden = sorted(forbidden & extracted_assertions)
    if missing_required:
        reasons["RG5"].append(f"missing_required:{missing_required}")
    if hit_forbidden:
        reasons["RG6"].append(f"forbidden_assertions:{hit_forbidden}")

    unsupported, subject_errors = _claim_source_errors(
        extracted, scenario["oracle_view"]["facts"]
    )
    reasons["RG6"].extend(unsupported)
    reasons["RG7"].extend(subject_errors)

    audit_verdicts = semantic_audit["verdicts"]
    for gate in ("RG5", "RG6", "RG7", "RG8", "RG9", "RG10"):
        if not audit_verdicts[gate]:
            reasons[gate].append("semantic_audit_failed")
    reasons["RG9"].extend(spoken_errors)

    if sorted(map(_claim_key, declared)) != sorted(map(_claim_key, extracted)):
        reasons["RG10"].append("declared_extracted_claim_mismatch")

    for gate in RG_GATE_IDS:
        verdicts[gate] = not reasons[gate]

    all_pass = all(verdicts.values())
    if all_pass and semantic_audit["decision"] == "approve":
        decision = "approve"
    elif semantic_audit["decision"] == "human_review" and all(
        verdicts[gate] for gate in RG_GATE_IDS if gate != "RG10"
    ):
        decision = "human_review"
    else:
        decision = "reject"
    return RuntimeGroundedGateReport(
        verdicts=verdicts,
        reasons={key: tuple(values) for key, values in reasons.items()},
        decision=decision,
    )

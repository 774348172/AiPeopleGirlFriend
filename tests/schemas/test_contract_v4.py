"""V4 阶段 0 合同自洽测试。

验证《数据生成器v4设计》阶段 0 冻结的五份 schema（data_gen_v4/schemas/）：
1. schema 文件本身合法 JSON，含 meta 字段。
2. 文档 §17 的 Alpha/Beta 示例 package（补全必填头字段后）能通过 package 合同。
3. 文档表格、schema、测试三方的枚举值一致（无漂移）。
4. P0 修复落位：plan item 有 refusal_required、candidate 有 visibility_scope、
   recipe 不再承载 ablation/curation/split、release policy 含 curation/split 子结构、
   ProfilePackage 含 disclosure_policy、EvidenceUnitV4 含 knowledge_scope。

不引入 jsonschema 依赖：本文件内置一个只覆盖合同所需关键词（required / properties /
const / enum / $ref / allOf / oneOf / pattern）的轻量校验器，够用于合同自洽断言。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "data_gen_v4" / "schemas"
SCHEMA_FILES = [
    "package_v4.schema.json",
    "evidence_v4.schema.json",
    "plan_v4.schema.json",
    "record_v4.schema.json",
    "lock_v4.schema.json",
]


@pytest.fixture(scope="module")
def schemas() -> dict[str, dict]:
    return {name: json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8")) for name in SCHEMA_FILES}


# ───────────────────────── 轻量校验器（合同子集） ─────────────────────────

def _resolve_ref(doc: dict, ref: str) -> dict:
    if not ref.startswith("#/"):
        raise ValueError(f"不支持跨文件引用: {ref}")
    node: dict = doc
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    return node


def _check(schema: dict, obj, doc: dict, path: str = "$") -> list[str]:
    """递归校验 required / const / enum / type(minItems) / pattern / allOf / oneOf / $ref。"""
    errors: list[str] = []
    if "$ref" in schema:
        return _check(_resolve_ref(doc, schema["$ref"]), obj, doc, path)
    if "allOf" in schema:
        for sub in schema["allOf"]:
            errors += _check(sub, obj, doc, path)
        return errors
    if "oneOf" in schema:
        branch_errors = [_check(sub, obj, doc, path) for sub in schema["oneOf"]]
        if not any(e == [] for e in branch_errors):
            return min(branch_errors, key=len)
        return []
    if "const" in schema and obj != schema["const"]:
        errors.append(f"{path}: 期望 const={schema['const']!r}，实际 {obj!r}")
    if "enum" in schema and obj not in schema["enum"]:
        errors.append(f"{path}: {obj!r} 不在枚举 {schema['enum']} 中")
    if "pattern" in schema and isinstance(obj, str) and not re.match(schema["pattern"], obj):
        errors.append(f"{path}: {obj!r} 不匹配 pattern {schema['pattern']!r}")
    if schema.get("type") in ("string", "number", "integer", "boolean") and isinstance(obj, dict):
        # 递归进入 properties 之前先做类型出口，避免把对象当标量
        if isinstance(obj, dict) and "properties" in schema:
            pass
        else:
            return errors
    if isinstance(obj, dict):
        if "properties" in schema:
            props = schema["properties"]
            for key, sub in props.items():
                if key in obj:
                    errors += _check(sub, obj[key], doc, f"{path}.{key}")
        # additionalProperties 模式：校验未在 properties 中显式声明的值（如 lock 的 pin 表）
        if "additionalProperties" in schema and isinstance(schema["additionalProperties"], dict):
            props = schema.get("properties", {})
            for key, val in obj.items():
                if key not in props:
                    errors += _check(schema["additionalProperties"], val, doc, f"{path}.{key}")
    for key in schema.get("required", []):
        if key not in obj:
            errors.append(f"{path}: 缺少必填字段 {key}")
    if isinstance(obj, list) and "items" in schema and schema.get("minItems"):
        if len(obj) < schema["minItems"]:
            errors.append(f"{path}: 数组长度 {len(obj)} < minItems {schema['minItems']}")
        for i, item in enumerate(obj):
            errors += _check(schema["items"], item, doc, f"{path}[{i}]")
    return errors


def validate_package(pkg: dict, schemas: dict[str, dict]) -> list[str]:
    """用 package_v4.schema.json 校验一个 package 对象。"""
    return _check(schemas["package_v4.schema.json"], pkg, schemas["package_v4.schema.json"])


# ───────────────────────── 1. schema 文件本身 ─────────────────────────

def test_schema_files_are_valid_json_and_have_meta(schemas: dict[str, dict]) -> None:
    assert set(schemas) == set(SCHEMA_FILES)
    for name, doc in schemas.items():
        assert doc.get("$schema") == "http://json-schema.org/draft-07/schema#", name
        assert doc.get("title"), name
        assert doc.get("description"), name


# ───────────────────────── 2. 文档 §17 Alpha/Beta 示例 ─────────────────────────

def _valid_alpha() -> dict:
    return {
        "package_id": "profile.fixture.alpha",
        "package_type": "profile",
        "schema_version": "aip.profile.v4",
        "package_version": "1.0.0",
        "content_hash": "sha256:" + "a" * 64,
        "compatible_core_range": ">=0.1.0",
        "dependencies": [],
        "created_at": "2026-08-04T00:00:00Z",
        "status": "approved",
        "profile_id": "fixture.alpha",
        "locale": "zh-CN",
        "identity_sources": ["source:alpha:identity"],
        "canon_sources": ["source:alpha:canon"],
        "timeline_sources": ["source:alpha:timeline"],
        "visibility_model": "visibility:audience-tiered-v1",
        "style_contract": "style:first-person-casual-v1",
        "disclosure_policy": "hint_only",
        "label_schema": "labels:emotion-action-v1",
        "capability_traits": [],
        "source_validators": [],
        "style_validators": [],
        "profile_prompt_fragments": [],
        "profile_evaluation_suites": [],
    }


def _valid_beta() -> dict:
    alpha = _valid_alpha()
    alpha.update(
        {
            "package_id": "profile.fixture.beta",
            "profile_id": "fixture.beta",
            "identity_sources": ["source:beta:identity"],
            "canon_sources": ["source:beta:canon"],
            "timeline_sources": [],
            "visibility_model": "visibility:public-only-v1",
            "style_contract": "style:third-person-formal-v1",
            "disclosure_policy": "direct_allowed",
            "label_schema": None,
        }
    )
    return alpha


def test_alpha_and_beta_pass_package_contract(schemas: dict[str, dict]) -> None:
    assert validate_package(_valid_alpha(), schemas) == []
    assert validate_package(_valid_beta(), schemas) == []


def test_alpha_missing_content_hash_is_rejected(schemas: dict[str, dict]) -> None:
    pkg = _valid_alpha()
    del pkg["content_hash"]
    errors = validate_package(pkg, schemas)
    assert any("content_hash" in e for e in errors)


def test_wrong_disclosure_policy_is_rejected(schemas: dict[str, dict]) -> None:
    pkg = _valid_alpha()
    pkg["disclosure_policy"] = "leak_all"
    errors = validate_package(pkg, schemas)
    assert any("disclosure_policy" in e for e in errors)


def test_non_approved_package_status_is_allowed_in_schema_but_lock_rejects(
    schemas: dict[str, dict],
) -> None:
    # 合同允许 draft 状态；lockfile 只接受 approved（lock_v4.schema.json 的 status 约束）
    pkg = _valid_alpha()
    pkg["status"] = "draft"
    assert validate_package(pkg, schemas) == []
    lock = {
        "lock_hash": "sha256:" + "b" * 64,
        "packages": {"profile.fixture.alpha": {"package_version": "1.0.0", "content_hash": pkg["content_hash"], "status": "draft"}},
        "source_snapshots": {},
        "schema_refs": {},
        "prompt_refs": {},
        "validator_refs": {},
        "adapter_revisions": {},
        "created_at": "2026-08-04T00:00:00Z",
    }
    errors = _check(schemas["lock_v4.schema.json"], lock, schemas["lock_v4.schema.json"])
    assert any("status" in e for e in errors)


# ───────────────────────── 3. 枚举一致性（文档 §6.3/§12/§13.2 ↔ schema） ─────────────────────────

def _enum_values(schema: dict, *path: str) -> list[str]:
    node: dict = schema
    for p in path:
        node = node[p]
    return list(node["enum"])


def test_visibility_scope_enums_are_consistent(schemas: dict[str, dict]) -> None:
    expected = {"profile_public", "profile_private", "profile_secret"}
    ev = schemas["evidence_v4.schema.json"]
    pl = schemas["plan_v4.schema.json"]
    std = set(_enum_values(ev, "definitions", "StandardVisibilityScope"))
    plan_std = set(pl["definitions"]["VisibilityScopeValue"]["oneOf"][0]["enum"])
    assert std == expected
    assert plan_std == expected


def test_knowledge_scope_enums_are_consistent(schemas: dict[str, dict]) -> None:
    expected = {"runtime_evidence", "general_knowledge", "realtime_external", "safety_critical"}
    ev = schemas["evidence_v4.schema.json"]
    pl = schemas["plan_v4.schema.json"]
    std = set(_enum_values(ev, "definitions", "StandardKnowledgeScope"))
    plan_std = set(pl["definitions"]["KnowledgeScopeValue"]["oneOf"][0]["enum"])
    assert std == expected
    assert plan_std == expected


def test_disclosure_policy_enum_is_consistent(schemas: dict[str, dict]) -> None:
    expected = {"direct_allowed", "hint_only", "withhold"}
    ev = schemas["evidence_v4.schema.json"]
    pkg = schemas["package_v4.schema.json"]
    std = set(_enum_values(ev, "definitions", "DisclosurePolicy"))
    pkg_std = set(
        pkg["definitions"]["ProfilePackage"]["allOf"][1]["properties"]["disclosure_policy"]["enum"]
    )
    assert std == expected
    assert pkg_std == expected


def test_evidence_state_enums_are_consistent(schemas: dict[str, dict]) -> None:
    expected = {"supported", "insufficient", "false_premise", "conflicted", "not_required"}
    for name in ("evidence_v4.schema.json", "plan_v4.schema.json"):
        std = set(_enum_values(schemas[name], "definitions", "EvidenceState"))
        assert std == expected


def test_desired_policy_enums_are_consistent(schemas: dict[str, dict]) -> None:
    expected = {
        "answer",
        "hint_only",  # T5：romance policy_distribution 首次生效（recipe 一直声明 0.2）
        "answer_with_uncertainty",
        "ask_for_evidence",
        "correct_premise",
        "surface_conflict",
        "withhold",
        "safety_first_answer",
        "refuse_unsafe_and_redirect",
        "no_op",
    }
    for name in ("evidence_v4.schema.json", "plan_v4.schema.json"):
        std = set(_enum_values(schemas[name], "definitions", "DesiredPolicy"))
        assert std == expected


def test_failure_error_codes_match_doc_section_13_2(schemas: dict[str, dict]) -> None:
    # 《数据生成器v4设计》§13.2 标准错误列表
    expected = {
        "package_invalid",
        "package_incompatible",
        "source_snapshot_failed",
        "precondition_failed",
        "mode_not_registered",
        "provider_error",
        "timeout",
        "truncated_output",
        "parse_error",
        "schema_error",
        "unsupported_claim",
        "policy_mismatch",
        "style_failure",
        "no_acceptable_candidate",
    }
    rec = schemas["record_v4.schema.json"]
    failure_props = rec["definitions"]["FailureRecord"]["allOf"][1]["properties"]
    codes = set(failure_props["error_code"]["enum"])
    assert codes == expected


def test_core_gate_ids_match_doc_section_14_1(schemas: dict[str, dict]) -> None:
    pkg = schemas["package_v4.schema.json"]
    gate_enum = set(
        pkg["definitions"]["ReleasePolicy"]["allOf"][1]["properties"]["required_core_gates"]["items"]["enum"]
    )
    assert gate_enum == {"G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7"}
    # G0-G3 不可裁剪：schema 用 contains 约束强制存在
    contains = pkg["definitions"]["ReleasePolicy"]["allOf"][1]["properties"]["required_core_gates"]["allOf"]
    mandatory = {c["contains"]["const"] for c in contains}
    assert mandatory == {"G0", "G1", "G2", "G3"}


# ───────────────────────── 4. P0 修复落位（schema 结构断言） ─────────────────────────

def test_plan_item_has_refusal_required_and_visibility_scope(schemas: dict[str, dict]) -> None:
    plan = schemas["plan_v4.schema.json"]
    item = plan["definitions"]["PlanItemV4"]
    assert "refusal_required" in item["required"]
    assert item["properties"]["refusal_required"] == {"type": "boolean"}
    assert "visibility_scope" in item["required"]
    assert item["properties"]["visibility_scope"]["minItems"] == 1


def test_candidate_has_visibility_scope_and_pending_review_status(schemas: dict[str, dict]) -> None:
    rec = schemas["record_v4.schema.json"]
    cand = rec["definitions"]["CandidateRecordV4"]["allOf"][1]
    assert "visibility_scope" in cand["required"]
    assert cand["properties"]["visibility_scope"]["minItems"] == 1
    assert cand["properties"]["review_status"] == {"const": "pending"}


def test_recipe_no_longer_carries_ablation_curation_split(schemas: dict[str, dict]) -> None:
    pkg = schemas["package_v4.schema.json"]
    props = pkg["definitions"]["DatasetRecipe"]["allOf"][1]["properties"]
    assert "ablation_manifests" not in props
    assert "curation_policy_ref" not in props
    assert "split_policy_ref" not in props


def test_release_policy_contains_curation_and_split(schemas: dict[str, dict]) -> None:
    pkg = schemas["package_v4.schema.json"]
    rp = pkg["definitions"]["ReleasePolicy"]["allOf"][1]
    assert "curation_policy" in rp["required"]
    assert "split_policy" in rp["required"]
    assert set(rp["properties"]["curation_policy"]["required"]) == {
        "exact_normalization",
        "ngram_range",
        "minhash_params",
        "embedding_model_ref",
        "allowed_dedup_modes",
    }
    assert set(rp["properties"]["split_policy"]["required"]) == {
        "initial_ratios",
        "max_component_ratio",
    }


def test_profile_package_has_disclosure_policy(schemas: dict[str, dict]) -> None:
    pkg = schemas["package_v4.schema.json"]
    profile = pkg["definitions"]["ProfilePackage"]["allOf"][1]
    assert "disclosure_policy" in profile["required"]
    assert profile["properties"]["disclosure_policy"]["enum"] == ["direct_allowed", "hint_only", "withhold"]


def test_evidence_unit_has_knowledge_scope(schemas: dict[str, dict]) -> None:
    ev = schemas["evidence_v4.schema.json"]
    unit = ev["definitions"]["EvidenceUnitV4"]
    assert "knowledge_scope" in unit["properties"]
    assert unit["properties"]["knowledge_scope"]["type"] == ["array", "null"]


def test_support_span_uses_unicode_code_point(schemas: dict[str, dict]) -> None:
    ev = schemas["evidence_v4.schema.json"]
    span = ev["definitions"]["SupportSpanV4"]
    assert "offset_unit" in span["required"]
    assert span["properties"]["offset_unit"] == {"const": "unicode_code_point"}
    assert span["properties"]["start"] == {"type": "integer", "minimum": 0}


def test_candidate_required_fields_are_traceable_to_plan(schemas: dict[str, dict]) -> None:
    """candidate 的 lineage/策略字段必须能在 plan（顶层或 item）中找到对应属性，保证 repair 可恢复。"""
    plan = schemas["plan_v4.schema.json"]
    plan_fields = set(plan["properties"]) | set(plan["definitions"]["PlanItemV4"]["properties"])
    rec = schemas["record_v4.schema.json"]
    cand = rec["definitions"]["CandidateRecordV4"]["allOf"][1]
    # 候选必须携带且 plan 必须可推导的字段（跨 schema 引用一致）
    traceable = {
        "profile_id",
        "profile_snapshot_id",
        "protocol_bundle_id",
        "recipe_id",
        "plan_id",
        "mode",
        "task_type",
        "family_id",
        "knowledge_scope",
        "visibility_scope",
        "evidence_state",
        "desired_policy",
        "required_behaviors",
        "forbidden_behaviors",
        "expected_outcomes",
        "fixture_id",
        "fixture_hash",
        "representation_ids",
        "render_profile_id",
        "prompt_template_version",
        "config_hash",
        "seed",
    }
    missing = traceable - plan_fields
    assert not missing, f"plan 无法推导字段: {sorted(missing)}"
    assert traceable <= set(cand["required"])


def test_lock_requires_approved_packages_only(schemas: dict[str, dict]) -> None:
    lock = schemas["lock_v4.schema.json"]
    pkg_status = lock["properties"]["packages"]["additionalProperties"]["properties"]["status"]
    assert pkg_status["enum"] == ["approved"]


def test_package_ref_pattern_matches_lockfile_usage(schemas: dict[str, dict]) -> None:
    pkg = schemas["package_v4.schema.json"]
    deps = pkg["definitions"]["PackageHeader"]["properties"]["dependencies"]
    pattern = deps["items"]["pattern"]
    assert re.match(pattern, "pkg:profile.fixture.alpha@1.0.0")
    assert re.match(pattern, "pkg:alpha@1.0.0")  # 单段 id 同样合法
    assert not re.match(pattern, "pkg:profile.fixture.alpha")  # 缺版本不合法
    assert not re.match(pattern, "profile.fixture.alpha")  # 缺 pkg: 前缀不合法

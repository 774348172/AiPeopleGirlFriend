"""tests/core 共享 fixtures：虚构 Alpha/Beta profile 与最小 package 集合。

所有 package 均为虚构测试数据（fixture.alpha / fixture.beta），不包含任何生产角色知识。
"""
from __future__ import annotations

import hashlib
from typing import Any

from data_gen_v4.core.interfaces import ModeAdapter, ModelAdapter, PackageRegistry
from data_gen_v4.core.plan import PlanItemV4, PackageSetV4
from data_gen_v4.core.resolver import canonical_json


def _content_hash(body: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def make_package(package_type: str, **fields: Any) -> dict[str, Any]:
    """构造自洽 package（自动计算 content_hash，排除字段自身）。"""
    body = {
        "package_id": fields.pop("package_id"),
        "package_type": package_type,
        "schema_version": fields.pop("schema_version"),
        "package_version": fields.pop("package_version"),
        "compatible_core_range": fields.pop("compatible_core_range", "*"),
        "dependencies": fields.pop("dependencies", []),
        "created_at": "2026-08-04T00:00:00Z",
        "status": fields.pop("status", "approved"),
    }
    body.update(fields)
    body["content_hash"] = _content_hash({k: v for k, v in body.items() if k != "content_hash"})
    return body


def profile_alpha() -> dict[str, Any]:
    return make_package(
        "profile",
        package_id="profile.fixture.alpha",
        schema_version="aip.profile.v4",
        package_version="1.0.0",
        profile_id="fixture.alpha",
        locale="zh-CN",
        identity_sources=["source:alpha:identity"],
        canon_sources=["source:alpha:canon"],
        timeline_sources=["source:alpha:timeline"],
        visibility_model="visibility:audience-tiered-v1",
        style_contract="style:first-person-casual-v1",
        disclosure_policy="hint_only",
        label_schema="labels:emotion-action-v1",
        capability_traits=[],
        source_validators=[],
        style_validators=[],
        profile_prompt_fragments=[],
        profile_evaluation_suites=[],
    )


def profile_beta() -> dict[str, Any]:
    return make_package(
        "profile",
        package_id="profile.fixture.beta",
        schema_version="aip.profile.v4",
        package_version="1.0.0",
        profile_id="fixture.beta",
        locale="zh-CN",
        identity_sources=["source:beta:identity"],
        canon_sources=["source:beta:canon"],
        timeline_sources=[],
        visibility_model="visibility:public-only-v1",
        style_contract="style:third-person-formal-v1",
        disclosure_policy="direct_allowed",
        label_schema=None,
        capability_traits=[],
        source_validators=[],
        style_validators=[],
        profile_prompt_fragments=[],
        profile_evaluation_suites=[],
    )


def protocol_bundle() -> dict[str, Any]:
    return make_package(
        "protocol",
        package_id="protocol.fixture.rel",
        schema_version="aip.protocol.v4",
        package_version="1.0.0",
        protocol_bundle_id="protocol:fixture:rel",
        modes=[
            {
                "mode_id": "REPLY",
                "mode_adapter_id": "mode.reply.v1",
                "input_schema_ref": "schemas/mode_v4/reply_input.schema.json",
                "target_schema_ref": "schemas/mode_v4/reply_target.schema.json",
                "grammar_ref": None,
                "render_profile_id": "reply-runtime-v1",
                "scheduling_semantics": "blocking",
                "failure_policy": "retry",
            }
        ],
    )


def dataset_recipe() -> dict[str, Any]:
    return make_package(
        "recipe",
        package_id="recipe.fixture.basic",
        schema_version="aip.recipe.v4",
        package_version="1.0.0",
        recipe_id="recipe:fixture:basic",
        strata=[
            {
                "task_type": "casual",
                "mode_id": "REPLY",
                "target_count": 2,
                "min_family_count": 1,
                "evidence_state_distribution": {},
                "policy_distribution": {},
                # 大块 B（阶段 2）：fixture 单候选（K 候选由专用测试覆盖）
                "candidate_count": 1,
                "risk_level": "low",
                "review_requirement": "auto",
            }
        ],
        contrast_suites=[],
        memory_selection_policy={},
        refusal_bounds={},
        training_mix={},
    )


def release_policy() -> dict[str, Any]:
    return make_package(
        "release",
        package_id="release.fixture.basic",
        schema_version="aip.release.v4",
        package_version="1.0.0",
        release_policy_id="release:fixture:basic",
        required_core_gates=["G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7"],
        required_profile_validators=[],
        required_protocol_validators=[],
        human_review_rules=[],
        metric_thresholds={},
        sealed_evaluation_refs=[],
        allowed_export_profiles=[],
        curation_policy={
            "exact_normalization": "NFKC",
            "ngram_range": [3, 5],
            "minhash_params": {},
            "embedding_model_ref": None,
            "allowed_dedup_modes": ["exact"],
        },
        split_policy={"initial_ratios": [0.8, 0.1, 0.1], "max_component_ratio": 0.15},
    )


# ───────────────────────── source snapshots ─────────────────────────

def make_snapshot(snapshot_id: str, source_adapter_id: str, units: list[dict[str, Any]]) -> dict[str, Any]:
    body = {
        "snapshot_id": snapshot_id,
        "source_adapter_id": source_adapter_id,
        "loaded_at": "2026-08-04T00:00:00Z",
        "units": units,
    }
    body["content_hash"] = _content_hash({k: v for k, v in body.items() if k != "content_hash"})
    return body


def _unit(source_kind: str, source_id: str, profile_id: str, snapshot_id: str, value: str,
          visibility: str = "profile_public") -> dict[str, Any]:
    return {
        "source_kind": source_kind,
        "source_id": source_id,
        "profile_id": profile_id,
        "snapshot_id": snapshot_id,
        "field_pointer": "",
        "value": value,
        "visibility_scope": visibility,
        "knowledge_scope": None,
        "disclosure_policy": "direct_allowed",
        "tags": [],
    }


def alpha_snapshots() -> dict[str, dict[str, Any]]:
    return {
        "source:alpha:identity": make_snapshot(
            "snap:alpha:identity", "structured-file",
            [_unit("identity_fact", "alpha:identity:001", "fixture.alpha",
                   "snap:alpha:identity", "角色名叫阿尔法，住在河畔公寓。")],
        ),
        "source:alpha:canon": make_snapshot(
            "snap:alpha:canon", "structured-file",
            [_unit("canon_fact", "alpha:canon:001", "fixture.alpha",
                   "snap:alpha:canon", "河畔公寓的猫叫豆包。")],
        ),
        "source:alpha:timeline": make_snapshot(
            "snap:alpha:timeline", "structured-file",
            [
                _unit("timeline_event", "alpha:timeline:001", "fixture.alpha",
                      "snap:alpha:timeline", "五岁那年搬过三次家。"),
                _unit("timeline_event", "alpha:timeline:002", "fixture.alpha",
                      "snap:alpha:timeline", "地下室里养过一只猫。", visibility="profile_secret"),
            ],
        ),
    }


def beta_snapshots() -> dict[str, dict[str, Any]]:
    return {
        "source:beta:identity": make_snapshot(
            "snap:beta:identity", "structured-file",
            [_unit("identity_fact", "beta:identity:001", "fixture.beta",
                   "snap:beta:identity", "贝塔是一名图书管理员。")],
        ),
        "source:beta:canon": make_snapshot(
            "snap:beta:canon", "structured-file",
            [_unit("canon_fact", "beta:canon:001", "fixture.beta",
                   "snap:beta:canon", "图书馆每周二闭馆。")],
        ),
    }


# ───────────────────────── fakes ─────────────────────────

class FakeRegistry:
    def __init__(self, packages: dict[str, dict[str, Any]]) -> None:
        self._packages = packages

    def get(self, package_id: str) -> dict[str, Any] | None:
        return self._packages.get(package_id)


def default_registry() -> FakeRegistry:
    packages = {
        p["package_id"]: p
        for p in (profile_alpha(), profile_beta(), protocol_bundle(), dataset_recipe(), release_policy())
    }
    return FakeRegistry(packages)


class FakeSourceLoader:
    def __init__(self, snapshots: dict[str, dict[str, Any]]) -> None:
        self._snapshots = snapshots
        self.calls: list[str] = []

    def load_snapshot(self, source_ref: str, snapshot_policy: str = "freeze") -> dict[str, Any]:
        self.calls.append(source_ref)
        try:
            return self._snapshots[source_ref]
        except KeyError as error:
            raise FileNotFoundError(f"source 不存在: {source_ref}") from error


def _base_item(*, family_id: str, family_role: str, **overrides: Any) -> PlanItemV4:
    fields: dict[str, Any] = {
        "family_id": family_id,
        "question_family_id": "qf:casual",
        "scene_family_id": None,
        "mode": "REPLY",
        "task_type": "casual",
        "family_role": family_role,
        "knowledge_scope": ["general_knowledge"],
        "visibility_scope": ["profile_public"],
        "evidence_state": "not_required",
        "desired_policy": "answer",
        "required_behaviors": [],
        "forbidden_behaviors": [],
        "expected_outcomes": [],
        "refusal_required": False,
        "fixture_id": None,
        "fixture_hash": None,
        "representation_ids": [],
        "render_profile_id": "reply-runtime-v1",
        "prompt_template_version": "reply-fixture-v1",
        "config_hash": "config:fixture:1",
        "seed": 0,
        # 大块 B（阶段 2 P0-4）：fixture 默认单候选（K 候选行为由专用测试覆盖）
        "candidate_count": 1,
        "max_attempts": 3,
        "risk_level": "low",
        "required_review": "auto",
        "split_anchor_ids": [],
    }
    fields.update(overrides)
    return PlanItemV4(**fields)


class FakeItemFactory:
    def build_items(
        self, *, profile: dict[str, Any], recipe: dict[str, Any],
        protocol: dict[str, Any], seed: int,
    ) -> list[PlanItemV4]:
        # 3 个 items：fam:1 成对对照 + fam:2 standalone，满足 recipe 配额（casual 目标 2）
        return [
            _base_item(family_id="fam:1", family_role="positive_control"),
            _base_item(family_id="fam:1", family_role="boundary_variant"),
            _base_item(family_id="fam:2", family_role="standalone"),
        ]


class FakeModeAdapter:
    """可配置的 REPLY 模式适配器：成功 / 先失败后成功 / 恒失败。

    fail_first 按 plan item 计数（engine 对每个 item 独立重试，adapter 实例共享）。
    """

    def __init__(self, *, fail_first: int = 0, always_fail: bool = False) -> None:
        self.fail_first = fail_first
        self.always_fail = always_fail
        self.calls = 0
        self._per_item: dict[str, int] = {}

    def prepare(self, plan_item: dict[str, Any], package_set: dict[str, Any]) -> dict[str, Any]:
        return {"plan_item": plan_item}

    def generate(self, mode_job: dict[str, Any], call_executor: Any) -> dict[str, Any]:
        self.calls += 1
        plan_id = mode_job["plan_item"]["plan_id"]
        item_calls = self._per_item.get(plan_id, 0) + 1
        self._per_item[plan_id] = item_calls
        if self.always_fail or item_calls <= self.fail_first:
            return {
                "mode_failure": True,
                "error_code": "no_acceptable_candidate",
                "retryable": True,
                "reason": "fixture 恒失败",
            }
        # 大块 B（阶段 2）：attempt/candidate 由 engine 注入（K 候选幂等键隔离）
        call_executor.call(
            {
                "model": {"name": "fixture-model", "revision": "r1"},
                "prompt_hash": "sha256:" + "c" * 64,
                "seed": 7,
                "input": {"scene": "日常"},
            },
            stage="semantic",
            attempt_no=mode_job["plan_item"].get("attempt_no", 1),
            candidate_no=mode_job["plan_item"].get("candidate_no"),
        )
        return {
            "input": {"scene": "日常"},
            "target": {
                "messages": [
                    {"role": "human", "content": "你好。"},
                    {"role": "assistant", "content": "你好呀。"},
                ]
            },
            "model": {"name": "fixture-model", "revision": "r1"},
            "prompt_hash": "sha256:" + "c" * 64,
            "prompt_template_version": "reply-fixture-v1",
            "config_hash": "config:fixture:1",
            "seed": 7,
            # 大块 B（阶段 2）：真实调用链回链（CallRecord.record_id）
            "calls": call_executor.record_ids(),
        }

    def render_training(self, candidate: dict[str, Any], package_set: dict[str, Any]) -> dict[str, Any]:
        return {"mode": "REPLY", "messages": candidate["target"]["messages"]}


class FakeModelAdapter:
    def __init__(self) -> None:
        self.specs: list[dict[str, Any]] = []

    def generate(self, spec: dict[str, Any]) -> dict[str, Any]:
        self.specs.append(spec)
        return {"content": "好的。", "finish_reason": "stop"}


class FakeModelPool:
    def __init__(self, adapter: FakeModelAdapter | None = None) -> None:
        self.adapter = adapter or FakeModelAdapter()

    def resolve(self, spec: dict[str, Any]) -> ModelAdapter:
        return self.adapter


def package_set() -> PackageSetV4:
    return PackageSetV4(
        profile_package_ref="pkg:profile.fixture.alpha@1.0.0",
        protocol_bundle_ref="pkg:protocol.fixture.rel@1.0.0",
        dataset_recipe_ref="pkg:recipe.fixture.basic@1.0.0",
        release_policy_ref="pkg:release.fixture.basic@1.0.0",
    )

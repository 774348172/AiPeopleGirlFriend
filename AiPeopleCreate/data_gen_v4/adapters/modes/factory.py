"""RecipeDrivenItemFactory：从 recipe 的 strata 与 sample_inputs 生成 plan items
（《数据生成器v4设计》§7.1 编排）。

同一实现驱动任意 profile/recipe；场景/话题等计划期输入来自 recipe 的
`sample_inputs` 扩展字段（schema additionalProperties 允许）。对照样本按
family 成对（positive_control + boundary_variant），standalone 单独存在。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from data_gen_v4.core.plan import PlanItemV4

_DEFAULT_RENDER_PROFILE = "reply-runtime-v1"
_DEFAULT_PROMPT_VERSION = "reply-style-v1"
_DEFAULT_CONFIG_HASH = "config:reply:v1"

# 记忆类型轴缺省推断表（2026-08-14 §24）：池条目显式 memory_type 优先；
# 未声明时按 task_type 通用推断（不内置角色知识，未覆盖类型不标注）。
_MEMORY_TYPE_INFERENCE: dict[str, str] = {
    "reply_identity": "persona",
    "reply_canon_qa": "persona",
    "reply_general": "general",
    "reply_safety": "general",
    "reply_memory": "special",
    "reply_item": "item",
}


def _infer_memory_type(task_type: str) -> str | None:
    return _MEMORY_TYPE_INFERENCE.get(task_type)


def _sample_weighted(
    distribution: dict[str, float], index: int, seed: int, *, default: str
) -> str:
    """按权重分布确定性抽样：同 (distribution, index, seed) 结果恒定。

    用途（T5）：evidence_state / desired_policy 按 recipe 分布落地，
    分批生成（--range-types 等）时同一 item 的标签稳定，不依赖随机状态。
    """
    if not distribution:
        return default
    total = sum(distribution.values())
    if total <= 0:
        return default
    # 混合位运算伪随机（index 与 seed 派生），落在 [0,1)
    r = ((index * 2654435761) ^ (seed * 40503)) % 100000 / 100000.0
    acc = 0.0
    for key, weight in distribution.items():
        acc += weight / total
        if r <= acc:
            return key
    return next(iter(distribution))


class RecipeDrivenItemFactory:
    """从 recipe 生成 plan items。三种模式：

    - `sample_inputs`（显式条目）：逐条生成，条目可带 family_id/family_role 做对照。
    - `topic_pools`（recipe 内嵌池）：对每个 strata，从 `topic_pools[task_type]` 池轮转
      取 target_count 个条目生成 items——适合大批量（如生产 profile 的百条级配额）。
    - 外部 pools 文件（`pools_path` 构造参数）：recipe 无内嵌池时，从外部 YAML
      读 `pools[task_type]` 列表——内容池与配额定义解耦（生产 profile 常由
      不同作者维护，阶段 5 支持）。
    两种池的条目字段相同（topic/scene/player_view/可选 family_id/family_role/
    desired_policy/refusal_required 等）。
    """

    def __init__(self, pools_path: str | None = None) -> None:
        self._pools_path = pools_path

    def build_items(
        self,
        *,
        profile: dict[str, Any],
        recipe: dict[str, Any],
        protocol: dict[str, Any],
        seed: int,
    ) -> list[PlanItemV4]:
        pools = self._resolve_pools(recipe)
        if pools:
            return self._build_from_pools(recipe, pools, seed, profile)
        inputs = recipe.get("sample_inputs", [])
        if not inputs:
            raise ValueError(
                f"recipe {recipe.get('recipe_id')} 缺少 sample_inputs / topic_pools / pools 文件"
            )
        return [
            self._item_from_sample(sample, seed, index)
            for index, sample in enumerate(inputs)
        ]

    def _resolve_pools(self, recipe: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        if recipe.get("topic_pools"):
            return recipe["topic_pools"]
        if self._pools_path:
            import yaml

            doc = yaml.safe_load(Path(self._pools_path).read_text(encoding="utf-8"))
            if isinstance(doc, dict) and doc.get("pools"):
                return doc["pools"]
        return {}

    def _build_from_pools(
        self,
        recipe: dict[str, Any],
        pools: dict[str, list[dict[str, Any]]],
        seed: int,
        profile: dict[str, Any] | None = None,
    ) -> list[PlanItemV4]:
        items: list[PlanItemV4] = []
        index = 0
        # T9（2026-08-06）：打破池循环复用——同条目第 2+ 次复用时注入情境变体
        # （profile.pool_variants 声明，通用机制；未配置时退化为原行为）
        variants = list((profile or {}).get("pool_variants") or [])
        use_count: dict[str, int] = {}
        for stratum in recipe.get("strata", []):
            task = stratum["task_type"]
            pool = pools.get(task, [])
            if not pool:
                continue
            evidence_dist = stratum.get("evidence_state_distribution") or {}
            policy_dist = stratum.get("policy_distribution") or {}
            for i in range(stratum["target_count"]):
                entry = pool[i % len(pool)]
                if variants:
                    key = str(entry.get("topic", ""))
                    reuse = use_count.get(key, 0)
                    use_count[key] = reuse + 1
                    if reuse >= 1:
                        variant = variants[(reuse - 1) % len(variants)]
                        base_view = str(entry.get("player_view", "")).rstrip("。")
                        entry = {
                            **entry,
                            "player_view": f"{base_view}。{variant}",
                        }
                item_entry = {**entry, "task_type": task, "mode_id": stratum["mode_id"]}
                # 块 1.4（2026-08-07，P1-3）：stratum 的 risk/review/candidate 配置
                # 落入条目（条目显式声明优先，stratum 为兜底）——修复 safety 24 条
                # 编译为 low/auto/2 的失真；required_review 与 recipe 字段名
                # review_requirement 的映射在 _item_from_sample 完成
                for key in ("candidate_count", "risk_level", "review_requirement"):
                    if key not in item_entry and key in stratum:
                        item_entry[key] = stratum[key]
                # T10（2026-08-06）：条目未显式声明 behaviors 时，用 strata 的
                # 默认行为合同填充（承接/边界约束进生成 prompt，而非空"（无）"）
                if "required_behaviors" not in entry:
                    item_entry["required_behaviors"] = list(
                        stratum.get("default_required_behaviors") or []
                    )
                if "forbidden_behaviors" not in entry:
                    item_entry["forbidden_behaviors"] = list(
                        stratum.get("default_forbidden_behaviors") or []
                    )
                # T5（2026-08-06）：recipe 分布采样落地——条目显式指定时优先，
                # 否则按 strata 的 evidence_state/policy 分布确定性抽样（同 seed 可复现）
                if "evidence_state" not in entry:
                    item_entry["evidence_state"] = _sample_weighted(
                        evidence_dist, index, seed, default="not_required"
                    )
                if "desired_policy" not in entry:
                    item_entry["desired_policy"] = _sample_weighted(
                        policy_dist, index, seed, default="answer"
                    )
                items.append(
                    self._item_from_sample(
                        item_entry,
                        seed,
                        index,
                    )
                )
                index += 1
        return items

    def _item_from_sample(self, sample: dict[str, Any], seed: int, index: int) -> PlanItemV4:
        family_id = str(
            sample.get("family_id") or sample.get("family_key") or f"fam:{index}"
        )
        family_role = sample.get("family_role", "standalone")
        task_type = str(sample.get("task_type", "casual"))
        mode = str(sample.get("mode", sample.get("mode_id", "REPLY")))
        turn_bounds = list(sample.get("turn_bounds", [4, 8]))
        return PlanItemV4(
            family_id=family_id,
            question_family_id=sample.get("question_family_id", f"qf:{family_id}"),
            scene_family_id=sample.get("scene_family_id"),
            mode=mode,
            task_type=task_type,
            family_role=family_role,
            knowledge_scope=sample.get("knowledge_scope", ["general_knowledge"]),
            visibility_scope=sample.get("visibility_scope", ["profile_public"]),
            memory_type=sample.get("memory_type") or _infer_memory_type(task_type),
            evidence_state=sample.get("evidence_state", "not_required"),
            desired_policy=sample.get("desired_policy", "answer"),
            required_behaviors=sample.get("required_behaviors", []),
            forbidden_behaviors=sample.get("forbidden_behaviors", []),
            expected_outcomes=sample.get("expected_outcomes", []),
            refusal_required=bool(sample.get("refusal_required", False)),
            fixture_id=sample.get("fixture_id"),
            fixture_hash=sample.get("fixture_hash"),
            representation_ids=sample.get("representation_ids", []),
            render_profile_id=sample.get("render_profile_id", _DEFAULT_RENDER_PROFILE),
            prompt_template_version=sample.get("prompt_template_version", _DEFAULT_PROMPT_VERSION),
            config_hash=sample.get("config_hash", _DEFAULT_CONFIG_HASH),
            seed=sample.get("seed", seed),
            candidate_count=int(sample.get("candidate_count", 2)),
            max_attempts=int(sample.get("max_attempts", 3)),
            risk_level=sample.get("risk_level", "low"),
            # 块 1.4（P1-3）：recipe stratum 字段名 review_requirement ↔ PlanItem
            # required_review 映射统一（pools 条目显式 required_review 优先）
            required_review=sample.get(
                "required_review", sample.get("review_requirement", "auto")
            ),
            split_anchor_ids=sample.get("split_anchor_ids", []),
            input={
                "scene": str(sample.get("scene", "日常")),
                "topic": str(sample.get("topic", "闲聊")),
                "player_view": str(sample.get("player_view", "")),
                # 条目级质量指导：只约束当前话题的角色边界与表达方式，
                # 不进入正典事实，供 REPLY prompt 做局部纠偏。
                "generation_guidance": str(sample.get("generation_guidance", "")),
                "turn_bounds": [int(turn_bounds[0]), int(turn_bounds[1])],
                # 2026-08-09：MEMORY_RERANK 场景字段透传（REPLY 条目缺省空值，
                # 不影响现有行为；pool 条目可声明 working_state / memory_pool）
                "working_state": str(sample.get("working_state", "")),
                "memory_pool": list(sample.get("memory_pool", []) or []),
            },
        )

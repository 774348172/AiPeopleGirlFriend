"""V4 阶段 0/1 冻结的 adapter 与基础设施接口（Protocol）。

阶段 1 只定义合同，不实现；阶段 2 提供真实 adapter。
所有接口以《数据生成器v4设计》§6-§7、§14-§15 为准。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .plan import PlanItemV4


# ───────────────────────── 来源 ─────────────────────────

@runtime_checkable
class SourceLoader(Protocol):
    """按 source ref 加载冻结快照（阶段 2 由 SourceAdapter 实现）。"""

    def load_snapshot(self, source_ref: str, snapshot_policy: str = "freeze") -> dict[str, Any]:
        """返回符合 evidence_v4.schema.json SourceSnapshot 的对象。"""
        ...


# ───────────────────────── 计划编排 ─────────────────────────

@runtime_checkable
class PlanItemFactory(Protocol):
    """把 recipe strata/contrast 编排为具体 plan items（阶段 2/3 实现内容知识）。"""

    def build_items(
        self,
        *,
        profile: dict[str, Any],
        recipe: dict[str, Any],
        protocol: dict[str, Any],
        seed: int,
    ) -> list[PlanItemV4]:
        ...


# ───────────────────────── 模型 ─────────────────────────

@runtime_checkable
class ModelAdapter(Protocol):
    """单次模型调用（§7.4）。三次失败后必须显式返回错误，不得回退 mock。"""

    def generate(self, spec: dict[str, Any]) -> dict[str, Any]:
        """输入 ModelCallSpec（provider/model/revision/role/prompt_hash/采样参数/seed），
        返回 ModelCallResult（content/finish_reason/output_hash）或抛 V4Error。"""
        ...


@runtime_checkable
class ModelPool(Protocol):
    """按 spec 解析可用的 ModelAdapter（§7.2 model_pool）。"""

    def resolve(self, spec: dict[str, Any]) -> ModelAdapter:
        ...


# ───────────────────────── 模式 ─────────────────────────

@runtime_checkable
class ModeAdapter(Protocol):
    """mode 适配器（§7.3）：prepare/generate/render_training。阶段 2 实现。"""

    def prepare(self, plan_item: PlanItemV4, package_set: dict[str, Any]) -> dict[str, Any]:
        ...

    def generate(
        self, mode_job: dict[str, Any], call_executor: Any
    ) -> dict[str, Any]:
        """返回 CandidatePayload 或 ModeFailure。"""
        ...

    def render_training(self, candidate: dict[str, Any], package_set: dict[str, Any]) -> dict[str, Any]:
        """产出 TrainingRecord 语义层（含 supervised_message_indexes / loss mask 信息）。"""
        ...


# ───────────────────────── 校验 ─────────────────────────

@runtime_checkable
class ValidatorAdapter(Protocol):
    """profile/protocol 校验器（§14）。接收 profile_snapshot_id，不硬编码角色事实。"""

    def validate(self, candidate: dict[str, Any], validation_context: dict[str, Any]) -> list[dict[str, Any]]:
        """返回 ValidationResult 列表（{ok, reason_code, detail}）。"""
        ...


# ───────────────────────── 包注册表 ─────────────────────────

@runtime_checkable
class PackageRegistry(Protocol):
    """package 仓库：按 package_id 返回 package 文档（dict）。"""

    def get(self, package_id: str) -> dict[str, Any] | None:
        ...


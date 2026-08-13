"""DatasetGenerator：正式外部 Seam（v2 审核报告 §8.1；P0-5 阶段 1 最小实现）。

build(run_spec, package_set) -> CompileResult：编译 plan + FREEZE-02 admission
预检（任一未冻结 mode → AdmissionBlockedError，不产出可执行会话）——
"已生成/已导出 ≠ 已发布"的入口闸门，未冻结 mode 在生成前被 AdmissionBlocked。

阶段 1 只覆盖 build 准入；engine 执行/导出由调用方组合（与 adapter 绑定），
resume 语义不变。后续阶段在此 Seam 上叠加 ledger/发布事务。
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Iterable

from .admission import AdmissionBlocked, Freeze02Admission
from .compiler import CompileResult, GenerationPlanCompiler
from .plan import GenerationPlanV4, PackageSetV4, PlanItemV4, RunSpec


ItemSelector = Callable[[GenerationPlanV4], Iterable[PlanItemV4]]


class AdmissionBlockedError(Exception):
    """build 预检发现未冻结 mode，拒绝产出可执行会话。"""

    def __init__(self, blocked: list[AdmissionBlocked]) -> None:
        self.blocked = blocked
        detail = "; ".join(f"{b.mode}({','.join(b.reason_codes)})" for b in blocked)
        super().__init__(f"生成被 FREEZE-02 冻结合同阻断: {detail}")

    def to_records(self) -> list[dict[str, Any]]:
        return [b.to_dict() for b in self.blocked]


class DatasetGenerator:
    """生成入口门面：编译 + 冻结合同准入（build）→ CompileResult。

    未配置 admission 时退化为纯编译（测试/工具场景）；生产入口必须配置。
    """

    def __init__(
        self,
        compiler: GenerationPlanCompiler,
        admission: Freeze02Admission | None = None,
    ) -> None:
        self._compiler = compiler
        self._admission = admission

    def build(
        self,
        run_spec: RunSpec,
        package_set: PackageSetV4,
        *,
        item_selector: ItemSelector | None = None,
    ) -> CompileResult:
        """Compile a plan, optionally select its executable items, then admit that subset.

        Selection belongs inside this interface because admission must cover exactly the
        items a caller will execute.  Compiling first still validates the complete recipe;
        the returned plan contains only the selected, admitted items.
        """
        result = self._compiler.compile(run_spec, package_set)
        if item_selector is not None:
            selected = list(item_selector(result.plan))
            result = replace(result, plan=replace(result.plan, items=selected))
        if self._admission is not None:
            blocked_by_reason: dict[tuple[str, tuple[str, ...]], AdmissionBlocked] = {}
            for item in result.plan.items:
                decision = self._admission.admit(item.mode)
                if decision is not None:
                    key = (decision.mode, tuple(decision.reason_codes))
                    blocked_by_reason.setdefault(key, decision)
            blocked = list(blocked_by_reason.values())
            if blocked:
                raise AdmissionBlockedError(blocked)
        return result

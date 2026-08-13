"""P0-2 证据链（块 1.2）验收：编译期证据填充在真实秦未晞 profile 上生效。

G1 验收用例之一"evidence 空被拒"的单元侧在 tests/core/test_gates.py
（G2 evidence_missing_* 拒绝）；本文件验证编译产物（_attach_evidence）：
- evidence-required items 全部带非空 source_refs + support_spans；
- span 引用的快照单元真实存在且 quote 与快照逐字符一致（G2 可过）；
- not_required items 不强制（装饰性，允许空）。
"""
from __future__ import annotations

from data_gen_v4.core.plan import RunSpec
from tests.stage5.test_qin_pipeline import QWX_PACKAGE_SET, qin_compiler

EVIDENCE_REQUIRED = {"supported", "insufficient", "conflicted", "false_premise"}


def test_compile_attaches_evidence_to_required_items(qin_compiler):
    """编译产物证据填充 + 全覆盖：required 全部非空、span 与快照逐字符一致。"""
    from collections import Counter

    result = qin_compiler.compile(RunSpec(run_id="p02-evidence", seed=42), QWX_PACKAGE_SET)
    items = result.plan.items
    required = [i for i in items if i.evidence_state in EVIDENCE_REQUIRED]
    assert required, "recipe 必须含 evidence-required 任务（否则 P0-2 无校验对象）"

    empty = [i for i in required if not i.source_refs or not i.support_spans]
    assert not empty, f"{len(empty)} 条 evidence-required items 证据为空"
    print(
        f"evidence-required: {len(required)} / {len(items)}"
        f"（{Counter(i.evidence_state for i in required)}）"
    )

    snapshots = result.context["snapshots"]
    for item in required:
        assert len(item.support_spans) == len(item.source_refs), "refs 与 spans 一一对应"
        for span in item.support_spans:
            # span 引用的快照单元必须真实存在
            snap = next(
                (s for s in snapshots.values() if s["snapshot_id"] == span["snapshot_id"]),
                None,
            )
            assert snap is not None, (
                f"{item.family_id} span 引用不存在的快照 {span['snapshot_id']}"
            )
            unit = next(
                (u for u in snap["units"] if u["source_id"] == span["source_id"]),
                None,
            )
            assert unit is not None, f"{item.family_id} span 引用不存在的单元 {span['source_id']}"
            # quote 与快照 value 逐字符一致（G2 校验通过的前提）
            assert span["quote"] == unit["value"], (
                f"{item.family_id} quote 与快照不一致"
            )
            assert span["offset_unit"] == "unicode_code_point"
            assert span["start"] == 0 and span["end"] == len(span["quote"])

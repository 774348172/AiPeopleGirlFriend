"""端到端：真实 source 文件 → SourceAdapter → compiler → engine（REPLY 通路）
→ core gates → ShareGPT 训练文件。第一个可用的训练数据产出路径。"""
from __future__ import annotations

import json

from data_gen_v4.adapters.exporters.export import ShareGPTReplyExportAdapter
from data_gen_v4.adapters.models.pool import ModelPool, StrictTestModelAdapter
from data_gen_v4.adapters.modes.reply import ReplyModeAdapter
from data_gen_v4.adapters.sources.structured_file import StructuredFileSourceAdapter
from data_gen_v4.core.compiler import GenerationPlanCompiler
from data_gen_v4.core.engine import GenerationEngineV4
from data_gen_v4.core.gates import ReleaseQualityGate
from data_gen_v4.core.plan import RunSpec
from data_gen_v4.core.sink import AppendSink
from tests.adapters.conftest import REPLY_OK_SCRIPT
from tests.core.fixtures import (
    FakeItemFactory,
    _base_item,
    default_registry,
    make_package,
    package_set,
)


def _file_based_registry(source_dir) -> dict:
    """用真实文件 ref 替换 alpha profile 的 sources（StructuredFileSourceAdapter 只认 file:）。"""
    profile = make_package(
        "profile", package_id="profile.fixture.alpha", schema_version="aip.profile.v4",
        package_version="1.0.0", profile_id="fixture.alpha", locale="zh-CN",
        identity_sources=["file:identity.yaml"],
        canon_sources=["file:canon.json"],
        timeline_sources=["file:timeline.yaml"],
        visibility_model="visibility:audience-tiered-v1",
        style_contract="第一人称、口语、俏皮，不使用 emoji、markdown 或助手腔。",
        disclosure_policy="hint_only", label_schema="labels:emotion-action-v1",
        capability_traits=[], source_validators=[], style_validators=[],
        profile_prompt_fragments=[], profile_evaluation_suites=[],
    )
    registry = default_registry()
    registry._packages["profile.fixture.alpha"] = profile
    return registry


class E2EItemFactory(FakeItemFactory):
    """产出带 input（scene/topic/player_view）的 REPLY items，满足 recipe 配额。"""

    def build_items(self, *, profile, recipe, protocol, seed):
        return [
            _base_item(
                family_id="fam:1",
                family_role="standalone",
                input={
                    "scene": "晚上在客厅，她在打游戏你在加班",
                    "topic": "今天吃什么",
                    "player_view": "你们合租，今晚加班回来晚了",
                    "turn_bounds": [4, 8],
                },
            ),
            _base_item(
                family_id="fam:2",
                family_role="standalone",
                input={
                    "scene": "周末下午，她霸占沙发你坐地上",
                    "topic": "周末干什么",
                    "player_view": "周末在家，没什么安排",
                    "turn_bounds": [4, 8],
                },
            ),
        ]


def test_e2e_reply_pipeline_produces_sharegpt(tmp_path, source_dir):
    # 1. package + 真实 SourceAdapter
    registry = _file_based_registry(source_dir)
    loader = StructuredFileSourceAdapter(source_dir)

    # 2. compile（真实 loader + 内容工厂）
    compiler = GenerationPlanCompiler(registry, loader, E2EItemFactory())
    result = compiler.compile(RunSpec(run_id="e2e-1", seed=42), package_set())
    assert result.plan.profile_id == "fixture.alpha"
    assert len(result.plan.items) == 2
    assert result.context["facts"]  # compiler 从快照收集了 identity/canon 事实

    # 3. engine：REPLY adapter + 严格测试模型（2 items × 1 次调用；2026-08-05 合并）
    model = StrictTestModelAdapter(REPLY_OK_SCRIPT * 2)
    pool = ModelPool(adapters={"strict": model})
    pool.default_id = "strict"
    engine = GenerationEngineV4(
        {"REPLY": ReplyModeAdapter()}, package_context=result.context
    )
    with AppendSink.open(tmp_path / "e2e.sqlite") as sink:
        run = engine.execute(result.plan, result.lock, pool, sink)
        assert run.completed == 2
        progress = sink.read_progress("e2e-1")
        assert len(progress.candidates) == 2
        assert len(model.calls) == 2

        # 4. core gates：G0-G4 全部通过——G3 为阶段 1 真实门（fixture item 为
        #    not_required 装饰性任务 → 无需 profile 锚定，放行）；G0-G3 不可裁剪。
        snapshots_by_id = {s["snapshot_id"]: s for s in result.context["snapshots"].values()}
        gate = ReleaseQualityGate()
        for candidate in progress.candidates:
            data = candidate.to_dict()
            results = gate.evaluate(data, {"lock": result.lock, "snapshots": snapshots_by_id})
            by_id = {r.gate_id: r for r in results}
            assert by_id["G0"].decision == "approved"
            assert by_id["G1"].decision == "approved"
            assert by_id["G2"].decision == "approved"
            assert by_id["G3"].decision == "approved"

        # 5. export：TrainingRecord → ShareGPT 训练行
        adapter = ReplyModeAdapter()
        exporter = ShareGPTReplyExportAdapter()
        records = []
        for candidate in progress.candidates:
            training = adapter.render_training(candidate.to_dict(), result.context)
            line = exporter.render(training)
            assert exporter.validate_roundtrip(line) == [{"ok": True}]
            records.append(json.loads(line))
        assert len(records) == 2
        roles = [c["from"] for c in records[0]["conversations"]]
        assert "human" in roles and "gpt" in roles
        # 训练文件写盘
        out = tmp_path / "train.jsonl"
        with out.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        assert out.stat().st_size > 0


def test_e2e_lock_and_plan_roundtrip_through_records(tmp_path, source_dir):
    """候选记录携带完整 lineage，可从 JSONL 无损恢复。"""
    registry = _file_based_registry(source_dir)
    compiler = GenerationPlanCompiler(
        registry, StructuredFileSourceAdapter(source_dir), E2EItemFactory()
    )
    result = compiler.compile(RunSpec(run_id="e2e-2"), package_set())
    model = StrictTestModelAdapter(REPLY_OK_SCRIPT * 2)
    pool = ModelPool(adapters={"strict": model})
    pool.default_id = "strict"
    engine = GenerationEngineV4({"REPLY": ReplyModeAdapter()}, package_context=result.context)
    with AppendSink.open(tmp_path / "e2e2.sqlite") as sink:
        engine.execute(result.plan, result.lock, pool, sink)
        progress = sink.read_progress("e2e-2")
        candidate = progress.candidates[0]
        # lineage 字段完整（profile/protocol/recipe/lock）
        assert candidate.header.profile_id == "fixture.alpha"
        assert candidate.header.package_lock_hash == result.plan.package_lock_hash
        assert candidate.profile_snapshot_id == result.plan.profile_snapshot_id
        assert candidate.header.plan_id.startswith(result.plan.plan_id + ":")
        # candidate 与 plan item 字段一致（repair 可恢复）
        item = next(i for i in result.plan.items if i.plan_id == candidate.header.plan_id)
        assert candidate.evidence_state == item.evidence_state
        assert candidate.desired_policy == item.desired_policy
        # refusal_required 只存在于 plan item（§9），repair 从 plan 恢复，不写回 candidate

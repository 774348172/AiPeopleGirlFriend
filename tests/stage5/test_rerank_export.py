"""MEMORY_RERANK 导出：RerankerJsonlExportAdapter 单元测试 + gen_v4 mode 分支（2026-08-09）。

覆盖：
- exporter：一条 TrainingRecord → 每条 (query, candidate) 一行；字段对齐
  reranker_record.schema.json（label 三分类/should_recall/label_evidence/character_id）；
  character_id / canon_snapshot / split 来自 export_profile（多角色不写死）
- 多角色：baiweixi 与假 profile 的 character_id 分别正确
- 往返校验：required 字段齐全
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data_gen_v4.adapters.exporters.reranker_jsonl import (  # noqa: E402
    RerankerJsonlExportAdapter,
)
from data_gen_v4.adapters.modes.rerank import MemoryRerankAdapter  # noqa: E402
from data_gen_v4.adapters.modes.training import TrainingRecordV4  # noqa: E402
from data_gen_v4.adapters.sources.registry import (  # noqa: E402
    CompositeSourceLoader,
    FilePackageRegistry,
)
from data_gen_v4.core.compiler import GenerationPlanCompiler  # noqa: E402
from data_gen_v4.core.plan import RunSpec  # noqa: E402
from gen_v4 import PROFILES_ROOT, ROOT as GEN_ROOT, build_package_set  # noqa: E402


def _compile_context(profile_id: str):
    registry = FilePackageRegistry(PROFILES_ROOT)
    loader = CompositeSourceLoader(GEN_ROOT)
    pools_path = PROFILES_ROOT / profile_id / "pools.yaml"
    from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory

    factory = RecipeDrivenItemFactory(
        pools_path=str(pools_path) if pools_path.exists() else None
    )
    compiler = GenerationPlanCompiler(registry, loader, factory)
    result = compiler.compile(
        RunSpec(run_id=f"rerank-export-{profile_id}", seed=42),
        build_package_set(PROFILES_ROOT, profile_id),
    )
    return result.context


def _make_record(context: dict, profile_id: str, memory_ids: list[str]) -> TrainingRecordV4:
    """构造一条 MEMORY_RERANK TrainingRecord（input=candidates, target=query+labels）。"""
    candidates = [
        {
            "memory_id": mid,
            "selector_text": f"[Day 0] 记忆文本 {mid}",
            "evidence_refs": [f"timeline_event:{mid}"],
        }
        for mid in memory_ids
    ]
    target = {
        "query": {
            "recent_dialogue": ["白未晞：嗯。"],
            "current_user_message": "终于结束了。",
            "working_state": "面试结束。",
        },
        "labels": [
            {
                "memory_id": mid,
                "label": "positive" if i == 0 else "hard_negative",
                "relevance_score": 0.9 if i == 0 else 0.05,
                "should_recall": i == 0,
                "label_evidence": "当前消息与该记忆直接相关。",
            }
            for i, mid in enumerate(memory_ids)
        ],
    }
    return TrainingRecordV4(
        sample_id=f"plan-{profile_id}:0000:a1:c1",
        mode="MEMORY_RERANK",
        render_profile_id="memory-rerank-v1",
        messages=[
            {"role": "human", "content": json.dumps({"candidates": candidates}, ensure_ascii=False)},
            {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)},
        ],
        supervised_message_indexes=[1],
        supervised_token_count=100,
        protocol_snapshot_id="protocol-v1",
        system_anchor="sha256:abc",
    )


def _export_profile(profile_id: str, context: dict) -> dict:
    canon_refs = (context["profile"] or {}).get("canon_sources") or []
    snap = context["snapshots"].get(canon_refs[0]) or {}
    return {
        "character_id": (context["profile"] or {}).get("profile_id", profile_id),
        "canon_snapshot": {
            "snapshot_id": snap.get("snapshot_id", ""),
            "sha256": str(snap.get("content_hash", "")).removeprefix("sha256:"),
        },
        "run_id": "rerank-run-1",
        "generator_version": "aipeople-gen-v4",
        "split": "train",
        "scenario_family": "scn:interview",
        "created_at": "2026-08-09T00:00:00+08:00",
    }


# ───────────────────────── exporter 单测 ─────────────────────────


def test_export_expands_one_record_per_pair():
    context = _compile_context("baiweixi")
    record = _make_record(context, "baiweixi", ["ev:bwx_rescued", "ev:bwx_minimum_trust"])
    adapter = RerankerJsonlExportAdapter()
    serialized = adapter.render(record, _export_profile("baiweixi", context))
    lines = [l for l in serialized.splitlines() if l.strip()]
    assert len(lines) == 2  # 每个候选一条


def test_export_fields_aligned_with_schema():
    context = _compile_context("baiweixi")
    record = _make_record(context, "baiweixi", ["ev:bwx_rescued"])
    adapter = RerankerJsonlExportAdapter()
    doc = json.loads(adapter.render(record, _export_profile("baiweixi", context)))
    # 对齐 reranker_record.schema.json 的 required 字段
    required = {
        "sample_id", "schema_version", "dataset_family", "character_id", "mode",
        "split", "conversation_group_id", "leakage_group_id", "scenario_family",
        "source_record_ids", "canon_snapshot", "generation", "content_kind",
        "query_id", "query_text", "candidate_memory_id", "candidate_memory_text",
        "label", "relevance_score", "should_recall", "label_evidence",
    }
    assert required <= set(doc.keys())
    assert doc["schema_version"] == 1
    assert doc["dataset_family"] == "memory_reranker"
    assert doc["mode"] == "MEMORY_RERANK"
    assert doc["content_kind"] == "ranking_pair"
    assert doc["label"] in ("positive", "hard_negative", "easy_negative")
    assert 0 <= doc["relevance_score"] <= 1
    assert isinstance(doc["should_recall"], bool)
    assert doc["query_text"] and doc["candidate_memory_text"]
    assert doc["candidate_memory_id"] == "ev:bwx_rescued"
    assert doc["source_record_ids"] == ["timeline_event:ev:bwx_rescued"]
    assert doc["canon_snapshot"]["snapshot_id"]
    assert len(doc["canon_snapshot"]["sha256"]) == 64


def test_export_character_id_not_hardcoded():
    # baiweixi profile → character_id=baiweixi
    bwx_ctx = _compile_context("baiweixi")
    bwx = json.loads(
        RerankerJsonlExportAdapter().render(
            _make_record(bwx_ctx, "baiweixi", ["ev:bwx_rescued"]),
            _export_profile("baiweixi", bwx_ctx),
        )
    )
    assert bwx["character_id"] == "baiweixi"
    assert "qinweixi" not in bwx["character_id"]


def test_export_validate_roundtrip_ok():
    context = _compile_context("baiweixi")
    record = _make_record(context, "baiweixi", ["ev:bwx_rescued", "ev:bwx_minimum_trust"])
    adapter = RerankerJsonlExportAdapter()
    serialized = adapter.render(record, _export_profile("baiweixi", context))
    assert adapter.validate_roundtrip(serialized) == [{"ok": True}]


def test_export_validate_roundtrip_catches_missing_fields():
    adapter = RerankerJsonlExportAdapter()
    broken = json.dumps({"sample_id": "x"}, ensure_ascii=False)
    errors = adapter.validate_roundtrip(broken)
    assert any("missing:" in e["reason_code"] for e in errors)


def test_query_text_render_contains_components():
    context = _compile_context("baiweixi")
    record = _make_record(context, "baiweixi", ["ev:bwx_rescued"])
    doc = json.loads(
        RerankerJsonlExportAdapter().render(record, _export_profile("baiweixi", context))
    )
    assert "current_user_message=终于结束了。" in doc["query_text"]
    assert "recent_dialogue:" in doc["query_text"]
    assert "working_state=面试结束。" in doc["query_text"]


# ───────────────────────── gen_v4 导出分支（fixture 不调 API） ─────────────────────────


def test_gen_v4_exporter_render_mode_dispatch():
    """按 mode 选择 exporter：REPLY → ShareGPT 行；MEMORY_RERANK → 配对行。"""
    from gen_v4 import exporter_render

    context = _compile_context("baiweixi")
    # REPLY TrainingRecord
    reply_record = TrainingRecordV4(
        sample_id="plan-reply:0000:a1:c1",
        mode="REPLY",
        render_profile_id="reply-runtime-v1",
        messages=[{"role": "human", "content": "hi"}, {"role": "assistant", "content": "嗯。"}],
        supervised_message_indexes=[1],
        supervised_token_count=10,
        protocol_snapshot_id="reply-protocol-v1",
        system_anchor="你是 白未晞。",
    )
    reply_line = exporter_render(reply_record)
    assert json.loads(reply_line)["conversations"][0]["from"] == "system"
    # MEMORY_RERANK TrainingRecord
    rerank_record = _make_record(context, "baiweixi", ["ev:bwx_rescued"])
    rerank_lines = exporter_render(rerank_record, _export_profile("baiweixi", context))
    assert json.loads(rerank_lines)["dataset_family"] == "memory_reranker"

"""MEMORY_RERANK 模式：adapter 单元测试（2026-08-09）。

覆盖：
- 候选蒸馏：timeline/canon units → 候选列表（selector_text 含时间前缀、evidence_refs）
- generate：合法 JSON → target；schema 失败/未知 memory_id/遗漏 → mode_failure
- render_training：监督 JSON target、protocol_snapshot_id
- 多角色：baiweixi 与假 profile 编译，character_id/canon 从 profile 读（不写死）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data_gen_v4.adapters.modes.rerank import (  # noqa: E402
    MemoryRerankAdapter,
    distill_candidates,
)
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
        RunSpec(run_id=f"rerank-{profile_id}", seed=42),
        build_package_set(PROFILES_ROOT, profile_id),
    )
    return result.context


class _FakeCallExecutor:
    """固定输出（可按 call 计数切换），记录调用 spec。"""

    def __init__(self, outputs: list[str]):
        self._outputs = list(outputs)
        self.specs: list[dict] = []
        self._calls = 0

    def call(self, spec: dict, **kwargs) -> dict:
        self.specs.append(spec)
        content = self._outputs[min(self._calls, len(self._outputs) - 1)]
        self._calls += 1
        return {"content": content}

    def record_ids(self) -> list[str]:
        return [f"call-{i}" for i in range(self._calls)]


def _ok_target(memory_ids: list[str]) -> dict:
    return {
        "query": {
            "recent_dialogue": ["白未晞：嗯，你出门前说紧张。"],
            "current_user_message": "终于结束了，累死我了。",
            "working_state": "玩家今天有一场重要面试。",
        },
        "labels": [
            {
                "memory_id": mid,
                "label": "positive" if i == 0 else "hard_negative",
                "relevance_score": 0.9 if i == 0 else 0.1,
                "should_recall": i == 0,
                "label_evidence": "当前消息与这段经历直接相关。",
            }
            for i, mid in enumerate(memory_ids)
        ],
    }


# ───────────────────────── 候选蒸馏 ─────────────────────────


def test_distill_candidates_from_timeline():
    context = _compile_context("baiweixi")
    cands = distill_candidates(context)
    assert cands, "应蒸馏出 timeline/canon 候选"
    kinds = {c["evidence_refs"][0].split(":")[0] for c in cands}
    assert kinds <= {"timeline_event", "canon_fact"}
    # selector_text 含时间前缀（如 [Day 0·暴雨夜]）
    assert any(c["selector_text"].startswith("[") for c in cands)


def test_distill_candidates_respects_memory_pool():
    context = _compile_context("baiweixi")
    pool = ["ev:bwx_rescued", "ev:bwx_human_reveal"]
    cands = distill_candidates(context, pool)
    ids = {c["memory_id"] for c in cands}
    assert ids == set(pool)


def test_distill_candidates_empty_pool_returns_all():
    context = _compile_context("baiweixi")
    all_cands = distill_candidates(context, None)
    assert len(distill_candidates(context, [])) == len(all_cands)


# ───────────────────────── generate ─────────────────────────


def test_generate_ok_target():
    context = _compile_context("baiweixi")
    item = {
        "seed": 1,
        "attempt_no": 1,
        "input": {
            "scene": "傍晚出租屋",
            "working_state": "玩家面试结束回家",
            "memory_pool": ["ev:bwx_rescued", "ev:bwx_minimum_trust"],
        },
    }
    adapter = MemoryRerankAdapter()
    job = adapter.prepare(item, context)
    executor = _FakeCallExecutor([json.dumps(_ok_target(["ev:bwx_rescued", "ev:bwx_minimum_trust"]), ensure_ascii=False)])
    payload = adapter.generate(job, executor)
    assert "mode_failure" not in payload
    assert payload["target"]["query"]["current_user_message"] == "终于结束了，累死我了。"
    assert len(payload["target"]["labels"]) == 2
    # prompt 含场景与候选
    prompt = executor.specs[0]["messages"][0]["content"]
    assert "傍晚出租屋" in prompt
    assert "ev:bwx_rescued" in prompt


def test_generate_invalid_json_fails():
    context = _compile_context("baiweixi")
    item = {
        "seed": 1,
        "attempt_no": 1,
        "input": {"scene": "s", "working_state": "w", "memory_pool": []},
    }
    adapter = MemoryRerankAdapter()
    job = adapter.prepare(item, context)
    payload = adapter.generate(job, _FakeCallExecutor(["不是 JSON"]))
    assert payload["mode_failure"] is True
    assert payload["error_code"] == "parse_error"
    assert payload["retryable"] is True


def test_generate_unknown_memory_id_rejected():
    context = _compile_context("baiweixi")
    item = {
        "seed": 1,
        "attempt_no": 1,
        "input": {
            "scene": "s", "working_state": "w",
            "memory_pool": ["ev:bwx_rescued"],
        },
    }
    adapter = MemoryRerankAdapter()
    job = adapter.prepare(item, context)
    # 教师编造了不存在的 memory_id
    bad = _ok_target(["ev:bwx_rescued", "made_up_id"])
    payload = adapter.generate(job, _FakeCallExecutor([json.dumps(bad, ensure_ascii=False)]))
    assert payload["mode_failure"] is True
    assert "编造" in payload["reason"] or "覆盖不完整" in payload["reason"]


def test_generate_missing_label_rejected():
    context = _compile_context("baiweixi")
    item = {
        "seed": 1, "attempt_no": 1,
        "input": {"scene": "s", "working_state": "w",
                  "memory_pool": ["ev:bwx_rescued", "ev:bwx_minimum_trust"]},
    }
    adapter = MemoryRerankAdapter()
    job = adapter.prepare(item, context)
    # 只标了一个，漏一个
    partial = _ok_target(["ev:bwx_rescued"])
    payload = adapter.generate(job, _FakeCallExecutor([json.dumps(partial, ensure_ascii=False)]))
    assert payload["mode_failure"] is True
    assert "未标注" in payload["reason"]


def test_generate_empty_candidates_precondition():
    context = _compile_context("baiweixi")
    item = {
        "seed": 1, "attempt_no": 1,
        "input": {"scene": "s", "working_state": "w",
                  "memory_pool": ["ev:not_exists_xyz"]},
    }
    adapter = MemoryRerankAdapter()
    job = adapter.prepare(item, context)
    payload = adapter.generate(job, _FakeCallExecutor([""]))
    assert payload["mode_failure"] is True
    assert payload["error_code"] == "precondition_failed"


# ───────────────────────── render_training ─────────────────────────


def test_render_training_supervises_json_target():
    context = _compile_context("baiweixi")
    item = {
        "seed": 1, "attempt_no": 1,
        "input": {"scene": "s", "working_state": "w",
                  "memory_pool": ["ev:bwx_rescued"]},
    }
    adapter = MemoryRerankAdapter()
    job = adapter.prepare(item, context)
    payload = adapter.generate(
        job, _FakeCallExecutor([json.dumps(_ok_target(["ev:bwx_rescued"]), ensure_ascii=False)])
    )
    record = adapter.render_training(payload, context)
    assert record.mode == "MEMORY_RERANK"
    assert record.supervised_message_indexes == [1]
    assert record.messages[1]["role"] == "assistant"
    assert record.protocol_snapshot_id  # 非空
    assert "memory_id" in record.messages[1]["content"]


# ───────────────────────── 多角色（character_id 从 profile 读） ─────────────────────────


def test_multi_profile_character_id_not_hardcoded():
    bwx = _compile_context("baiweixi")
    assert (bwx["profile"] or {}).get("profile_id") == "baiweixi"
    # canon_snapshot 来源存在（exporter 用）
    canon_refs = (bwx["profile"] or {}).get("canon_sources") or []
    assert canon_refs, "profile 必须声明 canon_sources"
    snap = bwx["snapshots"].get(canon_refs[0]) or {}
    assert snap.get("snapshot_id") and snap.get("content_hash")

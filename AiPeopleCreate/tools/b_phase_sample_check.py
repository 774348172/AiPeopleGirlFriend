# -*- coding: utf-8 -*-
"""B 阶段合并小样验证（G-B 门，2026-08-06）。

改造后生成器（T5-T11 全部生效）真实 API 跑 20 条分层样本：
- failure 率复测（T4 遗留：<5%）
- evidence_state 分布落地（insufficient/false_premise 出样本）
- 口癖率（开头哼/喂/啧 <10%、谁稀罕 <10%）
- G5 秘密词拒绝（0 泄漏）
- 输出 G-B 报告 JSON：local_runtime/b_phase_sample_result.json

用法：.venv/Scripts/python.exe tools/b_phase_sample_check.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_env(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


def main() -> int:
    _load_env(ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        print("[error] OPENAI_API_KEY 未配置")
        return 1

    from dataclasses import replace

    from data_gen_v4.adapters.models.pool import ModelPool, OpenAICompatModelAdapter
    from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory
    from data_gen_v4.adapters.modes.reply import ReplyModeAdapter
    from data_gen_v4.adapters.sources.registry import (
        CompositeSourceLoader,
        FilePackageRegistry,
    )
    from data_gen_v4.core.compiler import GenerationPlanCompiler
    from data_gen_v4.core.engine import GenerationEngineV4
    from data_gen_v4.core.gates import ReleaseQualityGate, accepted, secret_keyword_checker
    from data_gen_v4.core.plan import RunSpec
    from data_gen_v4.core.sink import AppendSink
    from gen_qin_v4 import PROFILES_ROOT, QWX_PACKAGE_SET, ROOT as GEN_ROOT, qin_style_resolver

    registry = FilePackageRegistry(PROFILES_ROOT)
    loader = CompositeSourceLoader(GEN_ROOT)
    factory = RecipeDrivenItemFactory(pools_path=str(PROFILES_ROOT / "qinweixi" / "pools.yaml"))
    compiler = GenerationPlanCompiler(registry, loader, factory)
    result = compiler.compile(RunSpec(run_id="b-sample", seed=42), QWX_PACKAGE_SET)

    # 分层取 20 条：casual 7（supported3+insufficient2+false_premise2）/ romance 4 / emotion 4 / identity 2 / protective 3
    plan_items = list(result.plan.items)
    picked: list = []
    wanted = {
        "reply_casual": {"supported": 3, "insufficient": 2, "false_premise": 2},
        "reply_romance": {"supported": 2, "insufficient": 1, "false_premise": 1},
        "reply_emotion": {"supported": 3, "insufficient": 1},
        "reply_identity": {"supported": 2},
        "reply_protective": {"supported": 3},
    }
    seen: dict[str, Counter] = {k: Counter() for k in wanted}
    for item in plan_items:
        task = item.task_type
        if task not in wanted:
            continue
        state = item.evidence_state
        if seen[task][state] < wanted[task].get(state, 0):
            picked.append(item)
            seen[task][state] += 1
        if len(picked) >= 20:
            break
    print(f"样本: {len(picked)} 条")
    print("  分布:", {k: dict(v) for k, v in seen.items() if sum(v.values())})

    subset = replace(result.plan, items=picked)
    adapter = OpenAICompatModelAdapter(
        base_url="https://aihub.lmdgame.com/api-product/v1", default_model="deepseek-v4-flash"
    )
    pool = ModelPool(adapters={"openai-compat": adapter})
    engine = GenerationEngineV4(
        {"REPLY": ReplyModeAdapter(style_contract_resolver=qin_style_resolver)},
        package_context=result.context,
    )
    sink_path = ROOT / "local_runtime/b_phase_sample.sqlite"
    t0 = time.time()
    with AppendSink.open(sink_path) as sink:
        run = engine.execute_parallel(subset, result.lock, pool, sink, workers=5)
        progress = sink.read_progress(subset.run_id)
    print(f"生成: completed={run.completed} failed={run.failed} 耗时 {time.time()-t0:.0f}s")

    # 门禁 + 统计
    # 历史验收工具（B 阶段，2026-08-05）：阶段 1（2026-08-07）后门禁语义变化——
    # G3/G4 为真实门且不可注入 no-op；G5 词表须显式传入。此工具仅供历史对照。
    gate = ReleaseQualityGate(release_policy=result.context.get("release") or {})
    secret_terms = tuple(
        (result.context.get("profile") or {}).get("policy_terms", {}).get("secret_terms", [])
    )
    gate.set_injected("G5", secret_keyword_checker(secret_terms))
    gate_context = {"lock": result.lock, "snapshots": result.context["snapshots"], "turn_bounds": (2, 64)}
    assist_texts: list[str] = []
    state_counts: Counter[str] = Counter()
    leak_hits: Counter[str] = Counter()
    gate_rejected = 0
    n_exported = 0
    for candidate in progress.candidates:
        state_counts[candidate.evidence_state] += 1
        decisions = gate.evaluate(candidate.to_dict(), gate_context, enabled_gates={"G0", "G1", "G2", "G5", "G6"})
        if not accepted(decisions):
            gate_rejected += 1
            for d in decisions:
                for code in d.reason_codes:
                    if code.startswith("secret_leak:"):
                        leak_hits[code] += 1
            continue
        n_exported += 1
        for m in candidate.target["messages"]:
            if m["role"] == "assistant":
                assist_texts.append(m["content"])

    n = len(assist_texts)
    def anyc(pat): return sum(1 for t in assist_texts if re.search(pat, t))
    def startc(pat): return sum(1 for t in assist_texts if re.match(pat, t.strip()))
    report = {
        "run": {"completed": run.completed, "failed": run.failed},
        "gates": {"exported": n_exported, "rejected": gate_rejected, "leak_hits": dict(leak_hits)},
        "evidence_states": dict(state_counts),
        "tics": {
            "any_哼": anyc("哼"), "start_哼喂啧": startc("^(哼|喂|啧)"),
            "any_谁稀罕": anyc("谁稀罕"),
            "assistant_replies": n,
        },
        "failure_rate": round(run.failed / len(picked), 3) if picked else None,
    }
    out = ROOT / "local_runtime/b_phase_sample_result.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

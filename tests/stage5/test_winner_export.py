"""阶段 2 B-3（P0-4）：winner 选择集成验收（真实 profile mock 管线）。

- K 候选逐 item 选择 winner（第一个 gate 全过的候选，按 attempt/candidate_no 排序）；
- 训练行 = 每 item 1 条（winner）；gate_decision 记录落 sqlite（winner lineage 可重建）。
"""
from __future__ import annotations

import json
from dataclasses import replace

from data_gen_v4.adapters.exporters.export import ShareGPTReplyExportAdapter
from data_gen_v4.adapters.modes.reply import ReplyModeAdapter
from data_gen_v4.core.gates import ReleaseQualityGate, accepted, secret_keyword_checker
from data_gen_v4.core.plan import RunSpec
from data_gen_v4.core.sink import AppendSink
from tests.stage5.test_qin_pipeline import (
    LOOP_SCRIPT,
    QWX_PACKAGE_SET,
    GenerationEngineV4,
    LoopingModelAdapter,
    LoopingPool,
    qin_compiler,
    qin_style_resolver,
)


def _export_winner_logic(progress, subset, result, sink, run_id):
    """与 gen_qin_v4.py 导出段同构：逐 item 选 winner + gate_decision 落盘。"""
    gate = ReleaseQualityGate(release_policy=result.context["release"])
    secret_terms = tuple(
        (result.context["profile"].get("policy_terms") or {}).get("secret_terms", [])
    )
    gate.set_injected("G5", secret_keyword_checker(secret_terms))
    gate_context = {
        "lock": result.lock,
        # G2 按 snapshot_id 查（快照 dict 的 key 是 source ref，须重建索引）
        "snapshots": {
            s["snapshot_id"]: s for s in result.context["snapshots"].values()
        },
        "protocol": result.context["protocol"],
        "turn_bounds": (2, 64),
    }
    renderer = ReplyModeAdapter(style_contract_resolver=qin_style_resolver)
    exporter = ShareGPTReplyExportAdapter()

    candidates_by_item: dict[str, list] = {}
    for candidate in progress.candidates:
        candidates_by_item.setdefault(candidate.header.plan_id, []).append(candidate)
    winners: list = []
    for plan_id, cands in candidates_by_item.items():
        cands.sort(key=lambda c: (c.attempt_no, c.candidate_no))
        item = next(x for x in subset.items if x.plan_id == plan_id)
        winner = None
        for candidate in cands:
            eval_context = dict(gate_context)
            if item.required_review == "full":
                eval_context["human_review_required"] = True
            if accepted(gate.evaluate(candidate.to_dict(), eval_context)):
                winner = candidate
                break
        if winner is not None:
            winners.append(winner)
            from data_gen_v4.core.records import GateDecisionRecord

            sink.append(
                GateDecisionRecord(
                    header=winner.header.with_record(record_type="gate_decision"),
                    subject_sample_id=winner.sample_id,
                    subject_candidate_record_id=winner.header.record_id,
                    gate_id="release",
                    decision="approved",
                    reason_codes=[],
                    validator_id="core-gates",
                    reviewer="gate",
                    reviewed_at="2026-08-08T00:00:00Z",
                ),
                json.dumps([run_id, plan_id, "winner", winner.sample_id], separators=(",", ":")),
            )
    return winners, renderer, exporter


def test_winner_selection_per_item_and_gate_decision_landed(qin_compiler, tmp_path):
    result = qin_compiler.compile(RunSpec(run_id="winner-run", seed=42), QWX_PACKAGE_SET)
    subset = replace(
        result.plan,
        items=[result.plan.items[0], result.plan.items[100]],  # casual + romance 各 1
    )
    model = LoopingModelAdapter(LOOP_SCRIPT)
    pool = LoopingPool(model)
    engine = GenerationEngineV4(
        {"REPLY": ReplyModeAdapter(style_contract_resolver=qin_style_resolver)},
        package_context=result.context,
    )
    with AppendSink.open(tmp_path / "winner.sqlite") as sink:
        engine.execute(subset, result.lock, pool, sink)
        progress = sink.read_progress("winner-run")
        # K=2（真实 recipe）→ 2 items × 2 候选 = 4
        assert len(progress.candidates) == 4

        winners, renderer, exporter = _export_winner_logic(
            progress, subset, result, sink, "winner-run"
        )
        # 每 item 恰好 1 个 winner → 训练行 2 条
        assert len(winners) == 2
        assert len({w.header.plan_id for w in winners}) == 2
        for winner in winners:
            training = renderer.render_training(winner.to_dict(), result.context)
            line = exporter.render(training)
            assert exporter.validate_roundtrip(line) == [{"ok": True}]
        # gate_decision 落盘（winner lineage）
        decisions = sink.read_progress("winner-run").gate_decisions
        assert len(decisions) == 2
        assert all(d.decision == "approved" for d in decisions)
        assert {d.subject_sample_id for d in decisions} == {w.sample_id for w in winners}
        # K 候选池中第一个通过者（mock 全通过 → c1）
        assert all(w.candidate_no == 1 for w in winners)

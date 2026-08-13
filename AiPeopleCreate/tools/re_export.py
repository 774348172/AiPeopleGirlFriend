# -*- coding: utf-8 -*-
"""从 ledger 重导出（阶段 5 收尾：审核放行闭环的最后一块）。

场景：生成后 G7 拦截 full 任务（safety/protective/supportive）→ 人工复核 →
apply_review 导入 reviewer 记录 → 本工具**从 ledger 重跑导出（0 重新生成）**，
已通过的候选（含新放行的）重新选 winner 并产出训练行/metadata/gate_report。

winner 选择（不重复调用模型）：
- 候选已有 quality（复用旧 metadata 分数）→ 按总分选最高；
- 无 quality + 有 API key → judge 打分（LLMJudge）；
- 无 quality + 无 key → 退化"首个合法"。

用法:
  python tools/re_export.py --ledger 训练数据/qin_v4_20.sqlite \
      --out 训练数据/qin_v4_20_final
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_gen_v4.adapters.exporters.export import ShareGPTReplyExportAdapter  # noqa: E402
from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory  # noqa: E402
from data_gen_v4.adapters.modes.reply import ReplyModeAdapter  # noqa: E402
from data_gen_v4.adapters.sources.registry import (  # noqa: E402
    CompositeSourceLoader,
    FilePackageRegistry,
)
from data_gen_v4.core.admission import Freeze02Admission  # noqa: E402
from data_gen_v4.core.compiler import GenerationPlanCompiler, run_timestamp  # noqa: E402
from data_gen_v4.core.dataset_generator import DatasetGenerator  # noqa: E402
from data_gen_v4.core.gates import (  # noqa: E402
    ReleaseQualityGate,
    accepted,
    safety_action_checker,
    secret_keyword_checker,
)
from data_gen_v4.core.judge import LLMJudge  # noqa: E402
from data_gen_v4.core.plan import RunSpec  # noqa: E402
from data_gen_v4.core.records import GateDecisionRecord  # noqa: E402
from data_gen_v4.core.sink import AppendSink  # noqa: E402
from gen_qin_v4 import (  # noqa: E402
    FREEZE02_CONTRACT,
    PROFILES_ROOT,
    QWX_PACKAGE_SET,
    ROOT as GEN_ROOT,
    qin_style_resolver,
)


def load_prior_quality(metadata_path: Path) -> dict[str, dict]:
    """读取旧 metadata（若有）→ sample_id → quality 映射（复用 judge 分数，0 重新调用）。"""
    mapping: dict[str, dict] = {}
    if metadata_path.exists():
        for line in metadata_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("quality"):
                mapping[row["sample_id"]] = row["quality"]
    return mapping


def detect_run_id(ledger_path: Path) -> str:
    """取最新有候选记录的 run（跳过发布事件等无候选 run）。"""
    import sqlite3

    con = sqlite3.connect(str(ledger_path))
    try:
        rows = con.execute(
            "SELECT run_id FROM v4_records "
            "WHERE record_type='candidate' GROUP BY run_id ORDER BY MAX(id) DESC"
        ).fetchall()
    finally:
        con.close()
    if not rows:
        raise SystemExit(f"[error] ledger 无候选 run: {ledger_path}")
    return rows[0][0]


def detect_profile_id(ledger_path: Path) -> str:
    """从候选记录取 profile_id（多角色：编译与导出按角色，lock 才能一致）。"""
    import sqlite3

    con = sqlite3.connect(str(ledger_path))
    try:
        rows = con.execute(
            "SELECT DISTINCT payload_json FROM v4_records "
            "WHERE record_type='candidate'"
        ).fetchall()
    finally:
        con.close()
    profiles = sorted({json.loads(p[0]).get("profile_id", "") for p in rows})
    profiles = [p for p in profiles if p]
    if not profiles:
        raise SystemExit(f"[error] ledger 无 profile_id 候选记录: {ledger_path}")
    if len(profiles) > 1:
        raise SystemExit(f"[error] ledger 含多个 profile: {profiles}（请用 --profile 指定）")
    return profiles[0]


def detect_plan_indexes(ledger_path: Path, run_id: str) -> set[int]:
    """Return full-plan indexes represented by candidates in one ledger run."""
    import sqlite3

    indexes: set[int] = set()
    con = sqlite3.connect(str(ledger_path))
    try:
        rows = con.execute(
            "SELECT payload_json FROM v4_records "
            "WHERE record_type='candidate' AND run_id=?",
            (run_id,),
        ).fetchall()
    finally:
        con.close()
    for (payload,) in rows:
        plan_id = str(json.loads(payload).get("plan_id", ""))
        try:
            indexes.add(int(plan_id.rsplit(":", 1)[-1]))
        except ValueError as error:
            raise SystemExit(f"[error] 无法从 plan_id 解析索引: {plan_id}") from error
    if not indexes:
        raise SystemExit(f"[error] run 无候选 plan 索引: {run_id}")
    return indexes


def _render_for_mode(winner, context, reply_renderer, rerank_renderer):
    """按 winner 的 mode 渲染 TrainingRecord（REPLY → 对话；MEMORY_RERANK → 协议）。

    2026-08-09 称呼注入复现：candidate.input.player_name（生成时透传持久化）
    存在时，浅拷贝 context 往锚注入 player_name/player_age —— 与生成时
    锚一致（T2 锚统一）。
    """
    candidate = winner.to_dict()
    mode = candidate.get("mode")
    if mode == "MEMORY_RERANK":
        return rerank_renderer.render_training(candidate, context)
    player_name = (candidate.get("input") or {}).get("player_name")
    ctx = context
    if player_name:
        ctx = dict(context)
        profile = dict(context["profile"])
        contract = dict(profile.get("anchor_contract") or {})
        contract["player_name"] = player_name
        profile["anchor_contract"] = contract
        ctx["profile"] = profile
        if context.get("anchor_facts") is not None:
            anchor_facts = dict(context["anchor_facts"])
            anchor_facts["player_age"] = str(
                (candidate.get("input") or {}).get("player_age") or 24
            )
            ctx["anchor_facts"] = anchor_facts
    return reply_renderer.render_training(candidate, ctx)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True, help="ledger sqlite")
    ap.add_argument("--out", required=True, help="输出前缀（.jsonl/.metadata.jsonl/.gate_report.jsonl）")
    ap.add_argument("--run-id", help="指定 run（缺省取最新）")
    ap.add_argument("--model", default="deepseek-v4-flash")
    ap.add_argument("--base-url", default="https://aihub.lmdgame.com/api-product/v1")
    ap.add_argument("--api-key-env", default="OPENAI_API_KEY")
    # 2026-08-09 多角色接线：--profile 从 ledger 候选记录读，编译与导出按角色
    ap.add_argument("--profile", default=None,
                    help="角色包 id（缺省从 ledger 候选记录自动识别）")
    ap.add_argument("--skip-admission", action="store_true",
                    help="跳过 FREEZE-02 准入（MEMORY_RERANK 等协议模式机制验证用）")
    args = ap.parse_args()

    ledger = Path(args.ledger)
    run_id = args.run_id or detect_run_id(ledger)
    out_prefix = Path(args.out)
    out_path = out_prefix.with_suffix(".jsonl") if out_prefix.suffix else Path(str(out_prefix) + ".jsonl")
    metadata_path = Path(str(out_path).replace(".jsonl", ".metadata.jsonl"))
    gate_report_path = Path(str(out_path).replace(".jsonl", ".gate_report.jsonl"))
    prior_quality = load_prior_quality(metadata_path)
    plan_indexes = detect_plan_indexes(ledger, run_id)

    # 2026-08-09：profile 自动识别（ledger 候选记录带 profile_id，多角色通用）
    profile_id = args.profile or detect_profile_id(ledger)
    if profile_id not in ("qinweixi", "baiweixi"):
        raise SystemExit(f"[error] 未知 profile: {profile_id}")
    from gen_v4 import build_package_set

    package_set = build_package_set(PROFILES_ROOT, profile_id)
    pools_path = PROFILES_ROOT / profile_id / "pools.yaml"

    # 1) 确定性编译（无模型调用）：lock/snapshots/protocol/release
    generator = DatasetGenerator(
        GenerationPlanCompiler(
            FilePackageRegistry(PROFILES_ROOT),
            CompositeSourceLoader(GEN_ROOT),
            RecipeDrivenItemFactory(
                pools_path=str(pools_path) if pools_path.exists() else None
            ),
        ),
        admission=None if args.skip_admission else Freeze02Admission(
            contract_path=str(FREEZE02_CONTRACT)
        ),
    )
    # lock 必须与生成时一致（model/exporter pin 进 lock_hash——不传则 mismatch）
    result = generator.build(
        RunSpec(
            run_id=f"re-export-{run_id}",
            seed=42,
            model_pin={
                "primary": {
                    "model_id": args.model,
                    "revision": "openai-compat",
                    "sampling": {"temperature": 0.7, "max_tokens": 8192},
                }
            },
            exporter_pin={"primary": {"exporter_id": "sharegpt-reply", "version": "1.0"}},
        ),
        package_set,
        item_selector=lambda plan: (
            item for index, item in enumerate(plan.items) if index in plan_indexes
        ),
    )

    # 2) 门装配（与 gen_qin_v4 同构）
    gate = ReleaseQualityGate(release_policy=result.context["release"])
    secret_terms = tuple(
        (result.context["profile"].get("policy_terms") or {}).get("secret_terms", [])
    )
    gate.set_injected("G5", secret_keyword_checker(secret_terms))
    if not (result.context["protocol"].get("safety_action_rules") or {}):
        print("[warn] protocol 未声明 safety_action_rules——G8 无知识可查")
    gate.set_injected("G8", safety_action_checker())
    with AppendSink.open(ledger) as _sink:
        _progress = _sink.read_progress(run_id)
        # G7 人工审核记录注入（full 任务放行依据）+ 人工判不通过的样本剔除
        review_decisions = [
            {
                "subject_candidate_record_id": d.subject_candidate_record_id,
                "reviewer": d.reviewer,
                "decision": d.decision,
            }
            for d in _progress.gate_decisions
            if d.gate_id == "G7" and d.decision == "approved"
        ]
        rejected_subjects = {
            d.subject_sample_id
            for d in _progress.gate_decisions
            if d.gate_id == "G7" and d.decision == "rejected"
        }
        if rejected_subjects:
            print(f"[审核] {len(rejected_subjects)} 个样本人工判不通过，将从导出剔除")
    gate_context = {
        "lock": result.lock,
        "snapshots": {s["snapshot_id"]: s for s in result.context["snapshots"].values()},
        "protocol": result.context["protocol"],
        "profile": result.context["profile"],  # 2026-08-10：judge 角色上下文
        "turn_bounds": (2, 64),
        "gate_decisions": review_decisions,
    }

    # 3) judge（按需：无 quality 且无 key → 退化 first_valid）
    judge = None
    if os.getenv(args.api_key_env):
        from data_gen_v4.adapters.models.pool import ModelPool, OpenAICompatModelAdapter

        pool = ModelPool(
            adapters={"openai-compat": OpenAICompatModelAdapter(
                base_url=args.base_url, api_key_env=args.api_key_env, default_model=args.model
            )}
        )
        # 2026-08-10（方案 A）：judge 模型优先 STRONG_MODEL（教师/judge 分离）
        strong_model = os.getenv("STRONG_MODEL") or ""
        judge_model = strong_model or args.model
        judge = LLMJudge(pool, adapter_id="openai-compat", model_ref=judge_model)
        print(f"[judge] 无 quality 候选将用 LLM judge（model={judge_model}"
              f"{'，STRONG_MODEL 分离' if strong_model and strong_model != args.model else ''}）")

    # 2026-08-09 多角色接线：renderer/exporter 按 profile 与 mode 分轨
    from data_gen_v4.adapters.modes.rerank import MemoryRerankAdapter
    from data_gen_v4.adapters.modes.style import make_style_resolver

    style_resolver = make_style_resolver(result.context["profile"], GEN_ROOT)
    reply_renderer = ReplyModeAdapter(style_contract_resolver=style_resolver)
    rerank_renderer = MemoryRerankAdapter()
    exporter = ShareGPTReplyExportAdapter()

    exported = rejected = 0
    with AppendSink.open(ledger) as sink:
        # 已存在的 release winner 记录（幂等：同 sample 不重复写入）
        prior_progress = sink.read_progress(run_id)
        existing_winners = {
            d.subject_sample_id
            for d in prior_progress.gate_decisions
            if d.gate_id == "release"
        }
    with AppendSink.open(ledger) as sink, \
            gate_report_path.open("w", encoding="utf-8") as report, \
            metadata_path.open("w", encoding="utf-8") as meta, \
            out_path.open("w", encoding="utf-8") as f:
        progress = sink.read_progress(run_id)
        candidates_by_item: dict[str, list] = {}
        for candidate in progress.candidates:
            candidates_by_item.setdefault(candidate.header.plan_id, []).append(candidate)
        for plan_id, cands in candidates_by_item.items():
            cands.sort(key=lambda c: (c.attempt_no, c.candidate_no))
            passed_candidates: list = []
            for candidate in cands:
                if candidate.sample_id in rejected_subjects:
                    rejected += 1  # 人工审核不通过 → 剔除（计入拒绝）
                    continue
                candidate_dict = candidate.to_dict()
                eval_context = dict(gate_context)
                # full review 判定：safety/protective/supportive 类须人工审核
                # （recipe required_review=full；候选记录不携带该字段，按任务类型判定）
                if candidate.task_type in ("reply_safety", "reply_protective", "reply_supportive"):
                    eval_context["human_review_required"] = True
                decisions = gate.evaluate(candidate_dict, eval_context)
                passed = accepted(decisions)
                report.write(
                    json.dumps(
                        {
                            "sample_id": candidate.sample_id,
                            "task_type": candidate.task_type,
                            "accepted": passed,
                            "decisions": [
                                {"gate_id": d.gate_id, "decision": d.decision,
                                 "reason_codes": d.reason_codes}
                                for d in decisions
                            ],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                if passed:
                    passed_candidates.append(candidate)
                else:
                    rejected += 1
            if not passed_candidates:
                continue
            # 4) winner：复用 quality（0 调用）> judge 打分 > first_valid
            def _quality_total(c: object) -> float:
                q = prior_quality.get(c.sample_id) or {}
                return sum(q.values())

            scored = [c for c in passed_candidates if prior_quality.get(c.sample_id)]
            if scored:
                winner = max(scored, key=_quality_total)
                selection_reason = "judge_selected(reused)"
                quality = prior_quality[winner.sample_id]
            elif judge is not None:
                from data_gen_v4.core.selector import DatasetSelector

                selector = DatasetSelector(judge)
                selection = selector.select_winner(
                    [c.to_dict() for c in passed_candidates], gate_context,
                    (result.context["release"] or {}).get("selection_thresholds") or {},
                )
                if selection is None:
                    continue
                winner = next(c for c in passed_candidates if c.sample_id == selection.candidate["sample_id"])
                selection_reason = selection.reason
                quality = selection.scores
            else:
                winner = passed_candidates[0]
                selection_reason = "first_valid"
                quality = {}
            # winner lineage（幂等：该 sample 已有 release 记录则跳过）
            if winner.sample_id not in existing_winners:
                sink.append(
                GateDecisionRecord(
                    header=winner.header.with_record(record_type="gate_decision"),
                    subject_sample_id=winner.sample_id,
                    subject_candidate_record_id=winner.header.record_id,
                    gate_id="release",
                    decision="approved",
                    reason_codes=[selection_reason],
                    validator_id="core-gates",
                    reviewer="gate",
                    reviewed_at=run_timestamp(),
                ),
                json.dumps([run_id, plan_id, "winner", winner.sample_id], separators=(",", ":")),
                )
                existing_winners.add(winner.sample_id)
            training = _render_for_mode(
                winner, result.context, reply_renderer, rerank_renderer
            )
            f.write(exporter.render(training) + "\n")
            exported += 1
            item_input = (winner.input or {})
            # MEMORY_RERANK 模式 target 为 {query, labels}（无 messages）；REPLY 为 messages
            target = winner.target or {}
            if "messages" in target:
                turns = len(target["messages"])
            else:
                turns = len((target.get("query") or {}).get("recent_dialogue", []))
            meta.write(
                json.dumps(
                    {
                        "sample_id": winner.sample_id,
                        "task_type": winner.task_type,
                        "evidence_state": winner.evidence_state,
                        "desired_policy": winner.desired_policy,
                        "family_id": winner.family_id,
                        "scene": item_input.get("scene", ""),
                        "topic": item_input.get("topic", ""),
                        "turns": turns,
                        "quality": quality,
                        "selection_reason": selection_reason,
                        "split_anchor_ids": winner.split_anchor_ids,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    print(f"重导出完成: 导出 {exported} / 拒绝 {rejected}（run={run_id}，0 重新生成）")
    print(f"产物: {out_path} / {metadata_path.name} / {gate_report_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

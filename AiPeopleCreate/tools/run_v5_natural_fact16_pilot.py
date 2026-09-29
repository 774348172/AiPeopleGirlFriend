"""Generate and self-review the 16 natural-fact conversational candidates."""

from __future__ import annotations

import argparse
import json
import hashlib
from collections import Counter
from pilot_pipeline import native_response, persist, run_stages
from natural_fact_scenarios import build_scenarios, validate_unique
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from build_v5_pilot_dataset import ROOT, _load_baiweixi_render_context, _write_jsonl
from data_gen_v4.runtime_grounded import evaluate_conversational_candidate, render_conversational_training_record
from data_gen_v4.runtime_grounded.renderer import (
    build_game_reply_chat_messages,
    parse_game_reply_dialogue_lines,
    render_game_reply_context_from_model_view,
)
from run_v5_conversational64_pilot import (
    FACT_CONTEXT,
    STYLE_GROUPS,
    _json_object,
    _native_call,
    _scenario,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=ROOT / "训练数据" / "baiweixi_v5_natural_fact16_pilot_20260911")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--model", default="qwen2.5:14b-instruct-q4_K_M")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument('--scenario-set', choices=['legacy', 'dialogue_chain'], default='dialogue_chain')
    args = parser.parse_args()
    if args.count < 1 or args.workers < 1:
        raise SystemExit('count and workers must be positive')
    if args.scenario_set == 'legacy':
        raise SystemExit('旧按问题拼接历史路径已暂停；请使用 dialogue_chain，count 不超过 10')
    from dialogue_chain_scenarios import build_scenarios as build_chains, validate_chains, validate_messages
    scenarios = build_chains(args.count)
    diversity = validate_chains(scenarios)
    # Exclusive directory creation prevents simultaneous writers and accidental overwrite.
    args.output_root.mkdir(parents=True, exist_ok=False)
    persist(args.output_root / 'batch_status.json', {'status': 'running', 'expected': args.count})
    checkpoints = args.output_root / 'checkpoints'
    checkpoints.mkdir()
    _, _, anchor = _load_baiweixi_render_context()
    inputs = args.output_root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    persist(args.output_root / 'diversity_report.json', diversity)
    for scenario in scenarios:
        # Keep the exact authority projection in every generated input; the
        # renderer still consumes the production game_feedback block.
        view = scenario["model_view"]
        # The scenario builder owns the complete authoritative projection.
        # Do not replace it with a one-fact projection here.
    _write_jsonl(inputs / "natural_fact_scenarios.jsonl", scenarios)

    def run(scenario: dict) -> dict:
        view = scenario["model_view"]
        context = render_game_reply_context_from_model_view(view)
        contract = json.dumps({**scenario["style_contract"], "fact_contract": scenario["fact_contract"]}, ensure_ascii=False)
        dialogue = build_game_reply_chat_messages(
            parse_game_reply_dialogue_lines(tuple(view.get("recent_dialogue") or ())),
            character_display_name="白未晞",
            current_protagonist_utterance=view["current_protagonist_utterance"],
        )
        system = (
            "你是白未晞。请先准确回答男主当前问题，再用自然、克制的口吻接话。"
            "结合旧记忆原话和最近会话，自然承接末条男主的话。当前状态以最新证据为准；旧计划不代表已完成。"
            "无证据的具体经历、位置、设备状态应坦率说不清楚或询问，不能用常识补全。主观意见、愿望和建议可以自然表达。"
            "设备存在不等于正在运行，运行中不等于快结束；不得把事实改写成你做过的动作，不得输出动作旁白、Markdown或解释。"
            "只输出 {\"reply\":\"...\"}。\n角色锚：" + anchor + "\n生产上下文：" + context
        )
        messages = [{"role": "system", "content": system}, *({"role": r, "content": c} for r, c in dialogue)]
        validate_messages(scenario, messages)
        def audit(target, attempt):
            return native_response(args.base_url, args.model, [{"role": "system", "content": audit_system + '\n待审核回复：' + target['reply']}], 512 + attempt * 256)
        audit_system = (
            "独立审核白未晞回复。必须同时检查：事实短语、主体、是否回答问题、角色语气、是否新增未授权动作。"
            "检查是否自然承接最近对话，而非仅关键词相同。主观意见和未来建议不等于捏造事实。"
            "只输出指定JSON。\n实际生成输入：" + json.dumps(messages, ensure_ascii=False) + "\n审核合同：" + contract +
            "\nJSON：{\"verdicts\":{\"voice\":true,\"turn_relevance\":true,\"naturalness\":true,\"unsupported_expansion\":true},\"decision\":\"approve|reject|human_review\",\"reasons\":[\"...\"]}"
        )
        return run_stages(
            {'sample_id': scenario['scenario_id'], 'scenario': scenario, 'generation_messages': messages},
            checkpoints / (scenario['scenario_id'] + '.json'),
            lambda: native_response(args.base_url, args.model, messages, 180), audit,
            lambda target, result: evaluate_conversational_candidate(scenario, target, result), _json_object,
        )

    approved, rejected = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run, scenario): scenario for scenario in scenarios}
        for future in as_completed(futures):
            scenario = futures[future]
            try:
                row = future.result()
                (approved if row["report"]["approved"] else rejected).append(row)
            except Exception as error:
                # Unexpected failures abort publication; durable checkpoints remain recoverable.
                persist(args.output_root / 'batch_status.json', {'status': 'failed', 'error': str(error)})
                raise
    approved.sort(key=lambda x: x.get("sample_id", ""))
    rejected.sort(key=lambda x: x.get("sample_id", ""))
    _write_jsonl(inputs / "natural_fact_approved.jsonl", approved)
    _write_jsonl(inputs / "natural_fact_rejected.jsonl", rejected)
    _write_jsonl(inputs / "natural_fact_all_for_review.jsonl", approved + rejected)
    report = {"total": len(scenarios), "approved": len(approved), "rejected": len(rejected), "training_started": False, "human_review_required": True}
    report['status_counts'] = dict(Counter(row['status'] for row in approved + rejected))
    (args.output_root / "pilot_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    persist(args.output_root / 'batch_status.json', {'status': 'complete', 'expected': args.count,
        'review_sha256': hashlib.sha256((inputs / 'natural_fact_all_for_review.jsonl').read_bytes()).hexdigest()})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = Path(__file__).resolve().parent
FROZEN_INPUTS = (
    ROOT
    / "eval"
    / "world_mind_p0"
    / "qwen35_27b_gpt56_60turn_20260824-114447"
    / "shared_inputs.jsonl"
)
REFERENCE_REPORT = FROZEN_INPUTS.parent / "report.json"
EXPECTED_INPUT_SHA256 = "0593a2606b470b7dd4e0ab34cf59ac96095d996fb2022bb8f8ba4ac2aa2e6720"

if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import run_baiweixi_paired_raw_context_ab as paired
import run_baiweixi_60turn_player_simulation as simulation
import run_gemma4_12b_60turn_comparison as resource_tools
import run_low_vram_lora_60turn_comparison as common


def _reference_speed(turns: list[dict]) -> dict:
    elapsed = [float(turn["elapsed_ms"]) for turn in turns if turn.get("elapsed_ms") is not None]
    warm = elapsed[1:]
    total_eval_count = sum(int(turn.get("eval_count") or 0) for turn in turns)
    total_eval_duration = sum(int(turn.get("eval_duration_ns") or 0) for turn in turns)
    return {
        "turns": len(turns),
        "thinking_turns": sum(bool(turn.get("thinking")) for turn in turns),
        "generation_failures": 0,
        "mean_total_elapsed_ms": round(statistics.fmean(elapsed), 2),
        "median_total_elapsed_ms": round(statistics.median(elapsed), 2),
        "warm_mean_total_elapsed_ms": round(statistics.fmean(warm), 2),
        "mean_first_output_ms": None,
        "warm_mean_first_output_ms": None,
        "mean_first_visible_ms": None,
        "median_first_visible_ms": None,
        "p90_first_visible_ms": None,
        "warm_mean_first_visible_ms": None,
        "aggregate_eval_tokens_per_second": round(
            total_eval_count * 1_000_000_000 / total_eval_duration, 2
        ) if total_eval_duration else None,
        "mean_thinking_chars": 0,
        "mean_response_chars": round(
            statistics.fmean(len(str(turn.get("response") or "")) for turn in turns), 2
        ),
        "latency_note": "Frozen non-streaming run; first-visible latency was not captured.",
    }


async def _run(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if common._sha256(FROZEN_INPUTS) != EXPECTED_INPUT_SHA256:
        raise ValueError("frozen 60-turn input SHA256 mismatch")
    inputs = common._load_jsonl(FROZEN_INPUTS)
    common._validate_inputs(inputs)
    common._write_jsonl(output_dir / "shared_inputs.jsonl", inputs)
    reference = json.loads(REFERENCE_REPORT.read_text(encoding="utf-8"))
    if reference["controls"]["input_sha256"] != EXPECTED_INPUT_SHA256:
        raise ValueError("Qwen3.5-27B reference input SHA256 mismatch")

    async with httpx.AsyncClient(
        base_url=args.ollama_url.rstrip("/"), timeout=httpx.Timeout(600.0)
    ) as client:
        digests = await paired._model_digests(client)
        if args.model not in digests:
            raise RuntimeError(f"Ollama model is not installed: {args.model}")
        await common._unload(client, args.model)
        candidate = await resource_tools._run_arm_with_resources(
            client,
            key="qwen25_14b_q4km",
            label="Qwen2.5-14B-Instruct Q4_K_M 裸基座",
            model=args.model,
            digest=digests[args.model],
            inputs=inputs,
            output_dir=output_dir,
            limit=args.limit,
            thinking=False,
            num_ctx=args.num_ctx,
            num_predict=args.num_predict,
            temperature=args.temperature,
            top_p=args.top_p,
            repeat_penalty=args.repeat_penalty,
        )

    controls = {
        "turn_count": args.limit,
        "history_limit_messages": 6,
        "same_frozen_oracle_free_inputs": True,
        "input_sha256": EXPECTED_INPUT_SHA256,
        "each_arm_retains_own_history": True,
        "native_model_chat_template": True,
        "generation": {
            "temperature": args.temperature,
            "top_p": args.top_p,
            "repeat_penalty": args.repeat_penalty,
            "num_ctx": args.num_ctx,
            "num_predict": args.num_predict,
            "seed_base": common.SEED_BASE,
            "thinking": False,
            "num_gpu": 999,
        },
        "hard_gate": {
            "probe_turns": 10,
            "generation_failures_required": 0,
            "max_output_contract_violations": 1,
            "critical_state_turns": [3, 4, 6, 7, 9, 10],
            "minimum_correct_critical_state_turns": 5,
            "gpu_peak_limit_mib": 12288,
            "probe_result": "passed_5_of_6_critical_turns",
            "quality_decision": "manual semantic review",
        },
        "limitations": [
            "The candidate retains its own generated history after turn 1.",
            "The Qwen3.5-27B reference is a frozen historical run, not regenerated here.",
            "Whole-machine RAM and GPU samples may include background processes.",
            "One frozen trajectory is a regression test, not a population accuracy estimate.",
        ],
    }

    if args.limit != 60:
        report = {
        "schema_version": 1,
        "scope": "qwen25_14b_q4km_hard_gate",
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": "probe_completed_pending_manual_gate",
        "controls": controls,
        "arm": candidate,
        "summary": {
            "turns": args.limit,
            "speed": common._speed_summary(candidate["turns"]),
            "resources": candidate["resources"],
            "automatic_quality_judgment": False,
        },
        }
        report_path = output_dir / "probe.json"
    else:
        reference_arm = reference["arms"]["qwen35"]
        reference_turns = reference_arm["turns"]
        if len(reference_turns) != 60:
            raise ValueError("Qwen3.5-27B reference does not contain 60 turns")
        report_turns = []
        for input_turn, source, candidate_turn, reference_turn in zip(
            inputs, simulation.TURNS, candidate["turns"], reference_turns, strict=True
        ):
            report_turns.append({
                "turn": input_turn["ordinal"],
                "phase": input_turn["phase"],
                "player": input_turn["player"],
                "authoritative_world": source["world"],
                "expectation": source["expectation"],
                "qwen25_14b": candidate_turn,
                "qwen35_27b": reference_turn,
            })
        report = {
            "schema_version": 1,
            "scope": "qwen25_14b_vs_qwen35_27b_stateful_60turn",
            "generated_at": datetime.now().astimezone().isoformat(),
            "status": "completed_pending_user_review",
            "controls": controls,
            "arms": {
                "qwen25_14b": candidate,
                "qwen35_27b": {
                    **reference_arm,
                    "label": "Qwen3.5-27B Q4_K_M 裸基座（冻结历史结果）",
                    "source_report": str(REFERENCE_REPORT),
                },
            },
            "turns": report_turns,
            "summary": {
                "turns": 60,
                "qwen25_14b_completed": len(candidate["turns"]),
                "qwen35_27b_completed": len(reference_turns),
                "qwen25_14b_generation_failures": sum(
                    bool(turn.get("generation_error")) for turn in candidate["turns"]
                ),
                "qwen35_27b_generation_failures": 0,
                "speed": {
                    "qwen25_14b": common._speed_summary(candidate["turns"]),
                    "qwen35_27b": _reference_speed(reference_turns),
                },
                "resources": {"qwen25_14b": candidate["resources"]},
                "automatic_quality_judgment": False,
            },
        }
        report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"REPORT={report_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Qwen2.5 14B on the frozen hard gate")
    parser.add_argument("output_dir")
    parser.add_argument("--model", default="qwen2.5:14b-instruct-q4_K_M")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--num-predict", type=int, default=180)
    parser.add_argument("--temperature", type=float, default=0.75)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--repeat-penalty", type=float, default=1.1)
    args = parser.parse_args()
    if not 1 <= args.limit <= 60:
        raise ValueError("--limit must be between 1 and 60")
    if min(args.num_ctx, args.num_predict) < 1:
        raise ValueError("context and output budgets must be positive")
    if args.temperature < 0:
        raise ValueError("temperature must be non-negative")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

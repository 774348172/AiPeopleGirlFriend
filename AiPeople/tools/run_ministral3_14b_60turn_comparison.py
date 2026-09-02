from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = Path(__file__).resolve().parent
FROZEN_RUN = (
    ROOT
    / "eval"
    / "world_mind_p0"
    / "qwen35_27b_gpt56_60turn_20260824-114447"
)
FROZEN_INPUTS = FROZEN_RUN / "shared_inputs.jsonl"
REFERENCE_REPORT = FROZEN_RUN / "report.json"
EXPECTED_INPUT_SHA256 = "0593a2606b470b7dd4e0ab34cf59ac96095d996fb2022bb8f8ba4ac2aa2e6720"

if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import run_gemma4_12b_60turn_comparison as resource_tools
import run_low_vram_lora_60turn_comparison as common
import run_baiweixi_paired_raw_context_ab as paired


def _reference_speed(turns: list[dict[str, Any]]) -> dict[str, Any]:
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
        )
        if total_eval_duration
        else None,
        "mean_thinking_chars": 0,
        "mean_response_chars": round(
            statistics.fmean(len(str(turn.get("response") or "")) for turn in turns), 2
        ),
        "latency_note": "Historical non-streaming run; first-visible-token latency was not captured.",
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
            key="ministral3_14b",
            label="Ministral 3 14B Instruct Q4_K_M 裸基座",
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

    if args.limit != 60:
        probe = {"status": "probe_completed", "candidate": candidate}
        (output_dir / "probe.json").write_text(
            json.dumps(probe, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(probe, ensure_ascii=False, indent=2))
        return 0

    reference_arm = reference["arms"]["qwen35"]
    reference_turns = reference_arm["turns"]
    if len(reference_turns) != 60:
        raise ValueError("Qwen3.5-27B reference does not contain 60 turns")

    report_turns = []
    for source, candidate_turn, reference_turn in zip(
        reference["turns"], candidate["turns"], reference_turns, strict=True
    ):
        report_turns.append(
            {
                "turn": source["turn"],
                "phase": source["phase"],
                "player": source["player"],
                "authoritative_world": source["authoritative_world"],
                "expectation": source["expectation"],
                "ministral3_14b": candidate_turn,
                "qwen35_27b": reference_turn,
            }
        )

    report = {
        "schema_version": 1,
        "scope": "ministral3_14b_vs_qwen35_27b_stateful_60turn",
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": "completed",
        "controls": {
            "turn_count": 60,
            "history_limit_messages": 6,
            "same_frozen_oracle_free_inputs": True,
            "input_sha256": EXPECTED_INPUT_SHA256,
            "each_arm_retains_own_history": True,
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
            "fairness": {
                "same_prompt": True,
                "same_sampling_configuration": args.temperature == 0.75,
                "candidate_temperature": args.temperature,
                "reference_temperature": 0.75,
                "qwen_reference_generated_at": reference["generated_at"],
                "qwen_reference_report": str(REFERENCE_REPORT),
                "qwen_reference_reused_without_regeneration": True,
            },
            "limitations": [
                "After turn 1 each arm has different self-generated history by design.",
                "The Qwen reference is a frozen historical run on the same workstation, not a simultaneous run.",
                "Qwen first-visible-token latency is unavailable because its historical run was non-streaming.",
                "This trajectory is for paired human review and is not a population-level accuracy estimate.",
                "System RAM is whole-machine usage and may include unrelated background processes.",
            ],
        },
        "arms": {
            "ministral3_14b": candidate,
            "qwen35_27b": {
                **reference_arm,
                "label": "Qwen3.5-27B Q4_K_M 裸基座（冻结历史结果）",
                "source_report": str(REFERENCE_REPORT),
            },
        },
        "turns": report_turns,
        "summary": {
            "turns": 60,
            "ministral3_14b_completed": len(candidate["turns"]),
            "qwen35_27b_completed": len(reference_turns),
            "ministral3_14b_generation_failures": sum(
                bool(turn.get("generation_error")) for turn in candidate["turns"]
            ),
            "qwen35_27b_generation_failures": 0,
            "speed": {
                "ministral3_14b": common._speed_summary(candidate["turns"]),
                "qwen35_27b": _reference_speed(reference_turns),
            },
            "resources": {"ministral3_14b": candidate["resources"]},
            "automatic_quality_judgment": False,
        },
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": report["generated_at"],
                "report_sha256": common._sha256(report_path),
                "input_sha256": EXPECTED_INPUT_SHA256,
                "candidate_model_digest": candidate["ollama_digest"],
                "reference_model_digest": reference_arm["ollama_digest"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"REPORT={report_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Ministral 3 14B against frozen Qwen3.5-27B")
    parser.add_argument("output_dir")
    parser.add_argument("--model", default="ministral-3:14b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--num-predict", type=int, default=180)
    parser.add_argument("--temperature", type=float, default=0.05)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--repeat-penalty", type=float, default=1.1)
    args = parser.parse_args()
    if not 1 <= args.limit <= 60:
        raise ValueError("--limit must be between 1 and 60")
    if args.num_ctx < 1 or args.num_predict < 1:
        raise ValueError("context and output budgets must be positive")
    if args.temperature < 0:
        raise ValueError("temperature must be non-negative")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

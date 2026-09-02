from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

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
EXPECTED_INPUT_SHA256 = "0593a2606b470b7dd4e0ab34cf59ac96095d996fb2022bb8f8ba4ac2aa2e6720"
HISTORY_LIMIT_MESSAGES = 6

if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import run_baiweixi_60turn_player_simulation as simulation
import run_baiweixi_paired_raw_context_ab as paired
import run_gemma4_12b_60turn_comparison as resource_tools
import run_low_vram_lora_60turn_comparison as common


async def _run(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if common._sha256(FROZEN_INPUTS) != EXPECTED_INPUT_SHA256:
        raise ValueError("frozen 60-turn input SHA256 mismatch")
    inputs = common._load_jsonl(FROZEN_INPUTS)
    common._validate_inputs(inputs)
    common._write_jsonl(output_dir / "shared_inputs.jsonl", inputs)

    async with httpx.AsyncClient(
        base_url=args.ollama_url.rstrip("/"), timeout=httpx.Timeout(600.0)
    ) as client:
        digests = await paired._model_digests(client)
        for model in (args.model_a, args.model_b):
            if model not in digests:
                raise RuntimeError(f"Ollama model is not installed: {model}")
            await common._unload(client, model)

        arm_a = await resource_tools._run_arm_with_resources(
            client,
            key="glm4_9b_0414_q6",
            label="GLM-4-9B-0414 Q6_K 裸基座",
            model=args.model_a,
            digest=digests[args.model_a],
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
        await common._unload(client, args.model_b)
        arm_b = await resource_tools._run_arm_with_resources(
            client,
            key="glm46v_flash_q4",
            label="GLM-4.6V-Flash Q4_K_M 裸基座（纯文本）",
            model=args.model_b,
            digest=digests[args.model_b],
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

    generated_at = datetime.now().astimezone().isoformat()
    controls = {
        "turn_count": args.limit,
        "history_limit_messages": HISTORY_LIMIT_MESSAGES,
        "same_frozen_oracle_free_inputs": True,
        "input_sha256": EXPECTED_INPUT_SHA256,
        "each_arm_retains_own_history": True,
        "arm_order": ["glm4_9b_0414_q6", "glm46v_flash_q4"],
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
        "latency_definitions": {
            "first_visible_ms": "request start to first non-whitespace visible answer token",
            "elapsed_ms": "request start to completed response",
            "warm_metrics": "turns 2-N; excludes each arm's cold model-load turn",
        },
        "hard_gate": {
            "probe_turns": 10,
            "generation_failures_required": 0,
            "max_output_contract_violations": 1,
            "critical_state_turns": [3, 4, 6, 7, 9, 10],
            "minimum_correct_critical_state_turns": 5,
            "gpu_peak_limit_mib": 12288,
            "quality_decision": "manual review; never inferred from keyword matching",
            "probe_result": {
                "glm4_9b_0414_q6": "failed_4_of_6_critical_turns",
                "glm46v_flash_q4": "failed_4_of_6_critical_turns",
            },
            "full_run_override": (
                "User explicitly requested the full 60-turn run after the failed gate "
                "to reduce false-rejection risk."
                if args.limit == 60
                else None
            ),
        },
        "limitations": [
            "Each arm keeps its own generated history after turn 1, matching the product trajectory.",
            "The arms run sequentially in fixed order; warm metrics exclude cold load but not every order effect.",
            "GLM-4.6V-Flash is exercised as a text-only candidate; no vision projector is loaded.",
            "Whole-machine RAM and GPU samples may include unrelated background processes.",
            "One frozen trajectory is a product regression test, not a population accuracy estimate.",
        ],
    }

    if args.limit != 60:
        probe = {
            "schema_version": 1,
            "scope": "glm_low_vram_hard_gate_probe",
            "generated_at": generated_at,
            "status": "probe_completed_pending_manual_gate",
            "controls": controls,
            "arms": {"glm4_9b_0414_q6": arm_a, "glm46v_flash_q4": arm_b},
            "summary": {
                "turns_per_arm": args.limit,
                "speed": {
                    "glm4_9b_0414_q6": common._speed_summary(arm_a["turns"]),
                    "glm46v_flash_q4": common._speed_summary(arm_b["turns"]),
                },
                "resources": {
                    "glm4_9b_0414_q6": arm_a["resources"],
                    "glm46v_flash_q4": arm_b["resources"],
                },
                "automatic_quality_judgment": False,
            },
        }
        probe_path = output_dir / "probe.json"
        probe_path.write_text(
            json.dumps(probe, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"PROBE={probe_path}")
        print(json.dumps(probe["summary"], ensure_ascii=False, indent=2))
        return 0

    report_turns = []
    for input_turn, source, turn_a, turn_b in zip(
        inputs, simulation.TURNS, arm_a["turns"], arm_b["turns"], strict=True
    ):
        report_turns.append(
            {
                "turn": input_turn["ordinal"],
                "phase": input_turn["phase"],
                "player": input_turn["player"],
                "authoritative_world": source["world"],
                "expectation": source["expectation"],
                "glm4_9b_0414_q6": turn_a,
                "glm46v_flash_q4": turn_b,
            }
        )

    report = {
        "schema_version": 1,
        "scope": "glm4_9b_0414_q6_vs_glm46v_flash_q4_stateful_60turn",
        "generated_at": generated_at,
        "status": "completed",
        "controls": controls,
        "arms": {"glm4_9b_0414_q6": arm_a, "glm46v_flash_q4": arm_b},
        "turns": report_turns,
        "summary": {
            "turns": 60,
            "glm4_9b_0414_q6_completed": len(arm_a["turns"]),
            "glm46v_flash_q4_completed": len(arm_b["turns"]),
            "glm4_9b_0414_q6_generation_failures": sum(
                bool(turn.get("generation_error")) for turn in arm_a["turns"]
            ),
            "glm46v_flash_q4_generation_failures": sum(
                bool(turn.get("generation_error")) for turn in arm_b["turns"]
            ),
            "speed": {
                "glm4_9b_0414_q6": common._speed_summary(arm_a["turns"]),
                "glm46v_flash_q4": common._speed_summary(arm_b["turns"]),
            },
            "resources": {
                "glm4_9b_0414_q6": arm_a["resources"],
                "glm46v_flash_q4": arm_b["resources"],
            },
            "automatic_quality_judgment": False,
        },
    }
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": generated_at,
                "report_sha256": common._sha256(report_path),
                "input_sha256": EXPECTED_INPUT_SHA256,
                "model_digests": {
                    "glm4_9b_0414_q6": arm_a["ollama_digest"],
                    "glm46v_flash_q4": arm_b["ollama_digest"],
                },
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
    parser = argparse.ArgumentParser(
        description="Run GLM-4-9B-0414 Q6 and GLM-4.6V-Flash Q4 on the frozen 60-turn trajectory"
    )
    parser.add_argument("output_dir")
    parser.add_argument("--model-a", default="glm4-9b-0414:q6")
    parser.add_argument("--model-b", default="glm4.6v-flash:q4km")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--limit", type=int, default=60)
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

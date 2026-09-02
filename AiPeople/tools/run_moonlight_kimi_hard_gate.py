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

if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

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

        moonlight_quant = "Q3_K_M" if "q3" in args.model_a.lower() else "Q4_K_M"
        moonlight_key = f"moonlight_16b_a3b_{moonlight_quant.lower()}"
        moonlight = await resource_tools._run_arm_with_resources(
            client,
            key=moonlight_key,
            label=f"Moonlight-16B-A3B-Instruct {moonlight_quant} 裸基座",
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
        kimi = await resource_tools._run_arm_with_resources(
            client,
            key="kimi_vl_a3b_q4",
            label="Kimi-VL-A3B-Instruct Q4_K_M 裸基座（纯文本）",
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

    report: dict[str, Any] = {
        "schema_version": 1,
        "scope": "moonlight_16b_a3b_vs_kimi_vl_a3b_hard_gate",
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": "probe_completed_pending_manual_gate",
        "controls": {
            "turn_count": args.limit,
            "history_limit_messages": 6,
            "same_frozen_oracle_free_inputs": True,
            "input_sha256": EXPECTED_INPUT_SHA256,
            "each_arm_retains_own_history": True,
            "arm_order": [moonlight_key, "kimi_vl_a3b_q4"],
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
                "quality_decision": "manual semantic review",
                "quantization_fallback": (
                    "If Q4_K_M exceeds 12288 MiB, repeat the same gate with Q3_K_M."
                ),
            },
            "limitations": [
                "Each arm keeps its own generated history after turn 1.",
                "The arms run sequentially in fixed order; warm metrics exclude cold load.",
                "Kimi-VL is tested as text-only; no vision projector is loaded.",
                "This frozen trajectory is a regression gate, not a population accuracy estimate.",
            ],
        },
        "arms": {
            moonlight_key: moonlight,
            "kimi_vl_a3b_q4": kimi,
        },
        "summary": {
            "turns_per_arm": args.limit,
            "speed": {
                moonlight_key: common._speed_summary(moonlight["turns"]),
                "kimi_vl_a3b_q4": common._speed_summary(kimi["turns"]),
            },
            "resources": {
                moonlight_key: moonlight["resources"],
                "kimi_vl_a3b_q4": kimi["resources"],
            },
            "automatic_quality_judgment": False,
        },
    }
    report_path = output_dir / "probe.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"PROBE={report_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Moonlight and Kimi-VL on the frozen hard-gate trajectory"
    )
    parser.add_argument("output_dir")
    parser.add_argument("--model-a", default="moonlight-16b-a3b:q3km")
    parser.add_argument("--model-b", default="kimi-vl-a3b:q4km")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--num-predict", type=int, default=180)
    parser.add_argument("--temperature", type=float, default=0.75)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--repeat-penalty", type=float, default=1.1)
    args = parser.parse_args()
    if not 1 <= args.limit <= 10:
        raise ValueError("--limit must be between 1 and 10 for the hard gate")
    if min(args.num_ctx, args.num_predict) < 1:
        raise ValueError("context and output budgets must be positive")
    if args.temperature < 0:
        raise ValueError("temperature must be non-negative")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

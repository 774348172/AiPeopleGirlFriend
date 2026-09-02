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
    thinking_enabled = not args.disable_thinking
    thinking_instruction = None
    if args.thinking_char_limit > 0:
        thinking_instruction = (
            "[内部思考长度约束]\n"
            f"内部思考必须控制在{args.thinking_char_limit}个汉字以内；"
            "只做回答当前问题所必需的核对，然后立即给出最终回答。"
        )
        inputs = [
            {**item, "system": f"{item['system']}\n\n{thinking_instruction}"}
            for item in inputs
        ]
    common._write_jsonl(output_dir / "shared_inputs.jsonl", inputs)

    async with httpx.AsyncClient(
        base_url=args.ollama_url.rstrip("/"), timeout=httpx.Timeout(900.0)
    ) as client:
        digests = await paired._model_digests(client)
        if args.model not in digests:
            raise RuntimeError(f"Ollama model is not installed: {args.model}")
        await common._unload(client, args.model)
        candidate = await resource_tools._run_arm_with_resources(
            client,
            key="deepseek_r1_distill_qwen_14b_q4",
            label=(
                "DeepSeek-R1-Distill-Qwen-14B Q4_K_M 裸基座"
                f"（{'开启' if thinking_enabled else '关闭'}思考）"
            ),
            model=args.model,
            digest=digests[args.model],
            inputs=inputs,
            output_dir=output_dir,
            limit=args.limit,
            thinking=thinking_enabled,
            num_ctx=args.num_ctx,
            num_predict=args.num_predict,
            temperature=args.temperature,
            top_p=args.top_p,
            repeat_penalty=args.repeat_penalty,
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "scope": "deepseek_r1_distill_qwen_14b_q4_hard_gate",
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": "probe_completed_pending_manual_gate",
        "controls": {
            "turn_count": args.limit,
            "history_limit_messages": 6,
            "same_frozen_oracle_free_inputs": True,
            "input_sha256": EXPECTED_INPUT_SHA256,
            "systematic_input_transform": thinking_instruction,
            "each_arm_retains_own_history": True,
            "native_model_chat_template": True,
            "product_faithful_system_prompt": True,
            "official_prompt_difference": (
                "DeepSeek recommends avoiding system prompts, but the primary product gate "
                "retains the real system prompt because production depends on it."
            ),
            "generation": {
                "temperature": args.temperature,
                "top_p": args.top_p,
                "repeat_penalty": args.repeat_penalty,
                "num_ctx": args.num_ctx,
                "num_predict": args.num_predict,
                "seed_base": common.SEED_BASE,
                "thinking": thinking_enabled,
                "thinking_char_limit_instruction": args.thinking_char_limit,
                "num_gpu": 999,
            },
            "latency_definitions": {
                "first_output_ms": "request start to first thinking or visible content token",
                "first_visible_ms": (
                    "request start to first non-whitespace player-visible final-answer token"
                ),
                "elapsed_ms": "request start to completed thinking and final response",
                "warm_metrics": "turns 2-10; excludes the cold model-load turn",
            },
            "hard_gate": {
                "probe_turns": 10,
                "generation_failures_required": 0,
                "max_output_contract_violations": 1,
                "critical_state_turns": [3, 4, 6, 7, 9, 10],
                "minimum_correct_critical_state_turns": 5,
                "gpu_peak_limit_mib": 12288,
                "target_mean_first_visible_ms": 3000,
                "quality_decision": "manual semantic review; never inferred from keywords",
            },
            "limitations": [
                "The model retains its own generated history after turn 1.",
                "Whole-machine RAM and GPU samples may include background processes.",
                "The official no-system-prompt format is not substituted for the product input.",
                "One frozen trajectory is a regression gate, not a population accuracy estimate.",
            ],
        },
        "arm": candidate,
        "summary": {
            "turns": args.limit,
            "speed": common._speed_summary(candidate["turns"]),
            "resources": candidate["resources"],
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
        description="Run DeepSeek-R1-Distill-Qwen-14B Q4 on the frozen hard gate"
    )
    parser.add_argument("output_dir")
    parser.add_argument("--model", default="deepseek-r1:14b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--num-predict", type=int, default=2048)
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--repeat-penalty", type=float, default=1.1)
    parser.add_argument(
        "--thinking-char-limit",
        type=int,
        default=0,
        help="Append a product-prompt instruction limiting hidden thinking characters; 0 disables it",
    )
    parser.add_argument(
        "--disable-thinking",
        action="store_true",
        help="Disable the model's native thinking channel",
    )
    args = parser.parse_args()
    if not 1 <= args.limit <= 10:
        raise ValueError("--limit must be between 1 and 10 for the hard gate")
    if min(args.num_ctx, args.num_predict) < 1:
        raise ValueError("context and output budgets must be positive")
    if args.temperature < 0:
        raise ValueError("temperature must be non-negative")
    if args.thinking_char_limit < 0:
        raise ValueError("--thinking-char-limit must be non-negative")
    if args.disable_thinking and args.thinking_char_limit > 0:
        raise ValueError(
            "--disable-thinking cannot be combined with --thinking-char-limit"
        )
    return asyncio.run(_run(args))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

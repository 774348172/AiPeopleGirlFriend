from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import run_baiweixi_60turn_player_simulation as simulation
import run_qwen35_9b_q6_gpt56_60turn_comparison as comparison


MARKUP_NARRATION = re.compile(r"（[^）]*）|\([^)]*\)|\*[^*]+\*")
LINE_NARRATION = re.compile(
    r"(?:^|\n)\s*(?:我)?(?:微微|轻轻|缓缓|慢慢|下意识|不由得|"
    r"抬眸|垂眸|侧过|转身|走到|看向|望向|点了点头|摇了摇头|"
    r"皱眉|蹙眉|愣了|怔了|叹了口气|猫耳|尾巴)"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def _flag_narration(reply: str) -> dict[str, bool]:
    return {
        "markup_narration": bool(MARKUP_NARRATION.search(reply)),
        "line_narration": bool(LINE_NARRATION.search(reply)),
    }


async def _run(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)

    inputs = [
        {
            "ordinal": ordinal,
            "phase": turn["phase"],
            "system": simulation._system(turn),
            "player": turn["player"],
        }
        for ordinal, turn in enumerate(simulation.TURNS, start=1)
    ]
    comparison._validate_inputs(inputs)
    input_path = output_dir / "shared_inputs.jsonl"
    _write_jsonl(input_path, inputs)

    arm, _ = await comparison._run_local(inputs, args.ollama_url)
    report_turns: list[dict[str, Any]] = []
    for source, generated in zip(simulation.TURNS, arm["turns"], strict=True):
        reply = str(generated["response"])
        report_turns.append(
            {
                "turn": generated["ordinal"],
                "phase": source["phase"],
                "player": source["player"],
                "authoritative_world": source["world"],
                "expectation": source["expectation"],
                "messages": generated["messages"],
                "prompt_sha256": generated["prompt_sha256"],
                "seed": generated["seed"],
                "response": reply,
                "format_flags": _flag_narration(reply),
                "elapsed_ms": generated["elapsed_ms"],
                "prompt_eval_count": generated["prompt_eval_count"],
                "eval_count": generated["eval_count"],
                "eval_duration_ns": generated["eval_duration_ns"],
                "done_reason": generated["done_reason"],
            }
        )

    elapsed = [float(turn["elapsed_ms"]) for turn in report_turns]
    markup_count = sum(
        turn["format_flags"]["markup_narration"] for turn in report_turns
    )
    line_count = sum(
        turn["format_flags"]["line_narration"] for turn in report_turns
    )
    report = {
        "schema_version": 1,
        "scope": "qwen35_9b_q6_dialogue_only_context_60turn",
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": "completed",
        "model": {
            "name": arm["model"],
            "label": arm["label"],
            "ollama_digest": arm["ollama_digest"],
            "surface": arm["surface"],
        },
        "controls": {
            "turn_count": 60,
            "history_limit_messages": comparison.HISTORY_LIMIT_MESSAGES,
            "each_reply_enters_later_history": True,
            "input_sha256": _sha256(input_path),
            "dialogue_only_rule_present_each_turn": True,
            "generation": {
                "temperature": 0.75,
                "top_p": 0.9,
                "repeat_penalty": 1.1,
                "num_ctx": 4096,
                "num_predict": 180,
                "seed_base": comparison.SEED_BASE,
                "native_chat_template": True,
                "thinking": False,
                "num_gpu": 999,
            },
            "limitations": [
                "The narration detector is a transparent format heuristic, not a semantic judge.",
                "Dialogue quality and factual correctness still require separate review.",
            ],
        },
        "summary": {
            "turns": len(report_turns),
            "completed": len(report_turns),
            "markup_narration_turns": markup_count,
            "line_narration_turns": line_count,
            "mean_elapsed_ms": round(statistics.fmean(elapsed), 2),
            "automatic_quality_judgment": False,
        },
        "turns": report_turns,
    }
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = {
        "schema_version": 1,
        "created_at": report["generated_at"],
        "input_path": input_path.name,
        "input_sha256": report["controls"]["input_sha256"],
        "report_path": report_path.name,
        "report_sha256": _sha256(report_path),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"REPORT={report_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Qwen3.5-9B Q6 with a dialogue-only context for 60 turns"
    )
    parser.add_argument("output_dir")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

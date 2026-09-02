from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import uvicorn

import ministral3_14b_60turn_app as shared


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "eval" / "world_mind_p0"


def _latest_run_dir() -> Path | None:
    values = sorted(
        (
            path
            for path in OUTPUT_ROOT.glob("qwen25_14b_60turn_*")
            if (path / "report.json").is_file()
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    return values[0] if values else None


def _audit_payload(report: dict[str, Any]) -> dict[str, Any]:
    turns = []
    for turn in report["turns"]:
        turns.append(
            {
                "turn": turn["turn"],
                "phase": turn["phase"],
                "player": turn["player"],
                "authoritative_world": turn["authoritative_world"],
                "expectation": turn["expectation"],
                "base": shared._arm_payload(turn["qwen25_14b"]),
                "full_program": shared._arm_payload(turn["qwen35_27b"]),
            }
        )
    candidate_model = report["arms"]["qwen25_14b"]["model"]
    reference_model = report["arms"]["qwen35_27b"]["model"]
    return {
        "generated_at": report["generated_at"],
        "review_protocol": {
            "blind": False,
            "automatic_quality_judgment": False,
            "reviewer": "user",
        },
        "summary": {
            "base": shared._empty_arm_summary(),
            "full_program": shared._empty_arm_summary(),
        },
        "automatic_summary": report["summary"],
        "speed_summary": {
            "base": report["summary"]["speed"]["qwen25_14b"],
            "full_program": report["summary"]["speed"]["qwen35_27b"],
        },
        "phase_summary": [],
        "source_report": str(shared.store.resolve_run_dir() / "report.json"),
        "has_prior_review": False,
        "run_enabled": False,
        "speed_context_note": "同一冻结输入 · 14B本次流式运行 · 27B复用冻结历史结果",
        "speed_latency_note": "仅14B记录玩家可见首字；27B历史运行未记录流式首字",
        "arm_labels": {
            "base": {
                "name": "A Qwen2.5-14B-Instruct Q4_K_M 裸基座",
                "short_name": "A Qwen2.5 14B",
                "detail": f"{candidate_model} · 原生模板 · 最近6条消息 · 无动作旁白",
            },
            "full_program": {
                "name": "B Qwen3.5-27B Q4 裸基座",
                "short_name": "B Qwen3.5 27B",
                "detail": f"{reference_model} · 冻结历史结果 · 最近6条消息 · 既有人工审核57/60",
            },
        },
        "turns": turns,
    }


shared.app.title = "Qwen2.5 14B 60-turn review"
shared._latest_run_dir = _latest_run_dir
shared._audit_payload = _audit_payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Qwen2.5 14B 60-turn human review page")
    parser.add_argument("--run-dir")
    parser.add_argument("--port", type=int, default=8784)
    args = parser.parse_args()
    if args.run_dir:
        shared.store.run_dir = Path(args.run_dir).resolve()
    uvicorn.run(shared.app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

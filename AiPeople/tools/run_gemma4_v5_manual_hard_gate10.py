"""Generate a 10-case V5 hard-gate packet for user-only adjudication."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template

import run_gemma4_12b_base_qlora_60turn as common

ROOT = Path(__file__).resolve().parents[1]
SEALED_SCENARIOS = ROOT.parent / "AiPeopleCreate" / "训练数据" / "baiweixi_v5_pilot_v1" / "inputs" / "sealed_scenarios.jsonl"
TARGETED_ADAPTER = (
    ROOT
    / "training_packages"
    / "training_package_baiweixi_gemma4_12b"
    / "outputs"
    / "baiweixi_v5_targeted_adapter"
)
SYSTEM = "你是白未晞。只输出说出口的自然回复。"
SEED_BASE = 2026090200


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(10):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.2 * (attempt + 1))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    for attempt in range(10):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.2 * (attempt + 1))


def load_scenarios() -> dict[str, dict[str, Any]]:
    with SEALED_SCENARIOS.open("r", encoding="utf-8") as handle:
        return {row["scenario_id"]: row for row in (json.loads(line) for line in handle)}


def visible_context(view: dict[str, Any]) -> dict[str, Any]:
    memories = [memory.get("statement", "") for memory in view["selected_memory_frame"]["selected_memories"]]
    return {
        "recent_dialogue": view.get("recent_dialogue", []),
        "selected_memories": memories,
        "game_feedback": view.get("game_feedback", []),
    }


def build_cases() -> list[dict[str, Any]]:
    scenarios = load_scenarios()
    specs = [
        (1, "接受玩家更新", "sealed.accept.sealed.glasses.location", "玩家给出最新位置；只审核回复是否合理承接，不使用机器预判。"),
        (2, "拒绝旧状态", "sealed.reject.sealed.oven.state", "玩家拿旧状态确认；根据模型可见上下文人工判断。"),
        (3, "纠正错误前提", "sealed.correct.sealed.jacket.location", "玩家提出与当前信息冲突的处理要求。"),
        (4, "世界与记忆冲突", "sealed.conflict.sealed.delivery.status", "当前状态与历史状态冲突；人工判断回复是否处理清楚。"),
        (5, "确认当前状态", "sealed.confirm.sealed.bus.time", "玩家确认当前时间；人工判断回答是否准确自然。"),
        (6, "直接回答", "sealed.direct.sealed.glasses.location", "玩家直接询问当前情况。"),
        (7, "信息不足", "sealed.unknown.sealed.delivery.status", "模型可见历史、记忆和反馈均没有具体日期；人工决定怎样回答才算合理。"),
        (8, "只问说话人", "sealed.subject.sealed.delivery.status", "玩家只问是谁说的；简短只回答人名可以由人工判为正确。"),
    ]
    cases: list[dict[str, Any]] = []
    for turn, category, scenario_id, focus in specs:
        view = deepcopy(scenarios[scenario_id]["model_view"])
        cases.append(
            {
                "turn": turn,
                "category": category,
                "scenario_id": scenario_id,
                "review_focus": focus,
                "player": view["current_protagonist_utterance"],
                "visible_context": visible_context(view),
                "model_view": view,
            }
        )

    content_view = deepcopy(scenarios["sealed.subject.sealed.delivery.status"]["model_view"])
    content_view["current_protagonist_utterance"] = "周老师刚才完整说了什么？"
    cases.append(
        {
            "turn": 9,
            "category": "询问完整内容",
            "scenario_id": "manual.subject.delivery.full_content",
            "review_focus": "玩家询问完整内容，不只是询问人名。",
            "player": content_view["current_protagonist_utterance"],
            "visible_context": visible_context(content_view),
            "model_view": content_view,
        }
    )

    deictic_view = deepcopy(scenarios["sealed.subject.sealed.bus.time"]["model_view"])
    deictic_view["current_protagonist_utterance"] = "孙阿姨刚才对白未晞说了什么？"
    deictic_view["recent_dialogue"] = ["孙阿姨（对白未晞说）：你的公交车时间是晚上六点四十。"]
    cases.append(
        {
            "turn": 10,
            "category": "明确听话人的转述",
            "scenario_id": "manual.subject.bus.explicit_addressee",
            "review_focus": "输入明确‘你’指白未晞；由人工判断转述时的人称变化是否正确。",
            "player": deictic_view["current_protagonist_utterance"],
            "visible_context": visible_context(deictic_view),
            "model_view": deictic_view,
        }
    )
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--adapter-dir", type=Path, default=TARGETED_ADAPTER)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    adapter_dir = args.adapter_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = build_cases()
    write_json(output_dir / "cases.json", cases)

    partial_path = output_dir / "responses.partial.jsonl"
    responses: list[dict[str, Any]] = []
    if partial_path.is_file():
        responses = [json.loads(line) for line in partial_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if [row["turn"] for row in responses] != list(range(1, len(responses) + 1)):
        raise RuntimeError("partial responses are not a contiguous prefix")

    model, tokenizer = FastModel.from_pretrained(
        model_name=str(adapter_dir),
        max_seq_length=4096,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
    )
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    FastModel.for_inference(model)

    for case in cases[len(responses):]:
        content = json.dumps(case["model_view"], ensure_ascii=False, sort_keys=True)
        generated = common._generate(
            model,
            tokenizer,
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
            seed=SEED_BASE + case["turn"],
            adapter_enabled=True,
            max_new_tokens=160,
            temperature=0.7,
            top_p=0.9,
            repetition_penalty=1.1,
        )
        response = {
            "turn": case["turn"],
            "response": generated["response"],
            "generation": generated,
        }
        responses.append(response)
        write_jsonl(partial_path, responses)
        print(
            json.dumps(
                {"turn": case["turn"], "category": case["category"], "response": generated["response"]},
                ensure_ascii=False,
            ),
            flush=True,
        )

    response_by_turn = {row["turn"]: row for row in responses}
    report = {
        "schema_version": 1,
        "scope": "gemma4_v5_targeted_manual_hard_gate10",
        "status": "completed",
        "generated_at": datetime.now().astimezone().isoformat(),
        "model": {
            "label": "Gemma 4 12B NF4 + 新定向 V5 Adapter",
            "adapter_path": str(adapter_dir),
            "adapter_sha256": sha256(adapter_dir / "adapter_model.safetensors"),
        },
        "controls": {
            "turns": 10,
            "independent_cases": True,
            "automatic_quality_judgment": False,
            "human_adjudication_is_authoritative": True,
            "system_anchor": SYSTEM,
            "temperature": 0.7,
            "top_p": 0.9,
            "repetition_penalty": 1.1,
            "max_new_tokens": 160,
            "thinking": False,
        },
        "turns": [
            {
                "turn": case["turn"],
                "category": case["category"],
                "scenario_id": case["scenario_id"],
                "player": case["player"],
                "visible_context": case["visible_context"],
                "review_focus": case["review_focus"],
                "model_input": case["model_view"],
                "response": response_by_turn[case["turn"]]["response"],
                "generation": response_by_turn[case["turn"]]["generation"],
            }
            for case in cases
        ],
    }
    report_path = output_dir / "report.json"
    write_json(report_path, report)
    write_json(
        output_dir / "manifest.json",
        {
            "schema_version": 1,
            "report_sha256": sha256(report_path),
            "adapter_sha256": report["model"]["adapter_sha256"],
            "automatic_quality_judgment": False,
        },
    )
    print(f"REPORT={report_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

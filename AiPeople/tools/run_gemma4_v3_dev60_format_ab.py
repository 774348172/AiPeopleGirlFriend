from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template

import run_gemma4_12b_base_qlora_60turn as common


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
PACKAGE = ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b_optimized_v2_2611_6144"
V3 = WORKSPACE / "AiPeopleCreate" / "优化数据V3候选"
TASKS = ("idle_chat", "follow_up_question", "emotion_attunement")
PER_TASK = 20
SEED_BASE = 2026092700


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def select_cases() -> list[dict[str, Any]]:
    manifest = load_json(V3 / "manifest.json")
    if manifest.get("status") != "experimental_candidate":
        raise ValueError("Unexpected V3 status")
    for name in ("train", "metadata", "audit"):
        if common._sha256(V3 / f"{name}.json" if name == "audit" else V3 / f"{name}.jsonl") != manifest[f"{name}_sha256"]:
            raise ValueError(f"V3 {name} hash mismatch")

    dev_rows = {row["sample_id"]: row for row in load_jsonl(PACKAGE / "data" / "dev.jsonl")}
    pairs = load_jsonl(V3 / "train.jsonl")
    metadata = load_jsonl(V3 / "metadata.jsonl")
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pair, meta in zip(pairs, metadata, strict=True):
        sid = meta["sample_id"]
        if sid not in dev_rows or meta["task"] not in TASKS:
            continue
        old = dev_rows[sid]
        messages = [*pair["prompt"], *pair["completion"]]
        if [m["role"] for m in messages] != ["system", "user", "assistant", "user", "assistant", "user", "assistant"]:
            raise ValueError(f"Unexpected role structure: {sid}")
        candidates[meta["task"]].append({
            "sample_id": sid,
            "family_id": old["family_id"],
            "leakage_group_id": old["leakage_group_id"],
            "scene": meta["scene"],
            "task": meta["task"],
            "messages": messages[:-1],
            "reference": messages[-1]["content"],
        })

    selected: list[dict[str, Any]] = []
    used_groups: set[str] = set()
    for task in TASKS:
        by_scene: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in sorted(candidates[task], key=lambda item: (item["scene"], item["sample_id"])):
            by_scene[row["scene"]].append(row)
        scenes = sorted(by_scene)
        picked: list[dict[str, Any]] = []
        while len(picked) < PER_TASK:
            progressed = False
            for scene in scenes:
                while by_scene[scene] and by_scene[scene][0]["leakage_group_id"] in used_groups:
                    by_scene[scene].pop(0)
                if by_scene[scene]:
                    row = by_scene[scene].pop(0)
                    picked.append(row)
                    used_groups.add(row["leakage_group_id"])
                    progressed = True
                    if len(picked) == PER_TASK:
                        break
            if not progressed:
                raise ValueError(f"Not enough independent dev rows for {task}")
        selected.extend(picked)
    for index, row in enumerate(selected, 1):
        row["ordinal"] = index
    return selected


def run_arm(model: Any, tokenizer: Any, cases: list[dict[str, Any]], *, key: str, adapter_enabled: bool, output: Path, args: argparse.Namespace) -> list[dict[str, Any]]:
    partial = output / f"{key}_responses.partial.jsonl"
    done = load_jsonl(partial) if partial.is_file() else []
    for case, record in zip(cases, done):
        if record["sample_id"] != case["sample_id"] or record["arm"] != key:
            raise ValueError(f"Invalid partial output: {partial}")
    for case in cases[len(done):]:
        generated = common._generate(
            model, tokenizer, case["messages"], seed=SEED_BASE + case["ordinal"],
            adapter_enabled=adapter_enabled, max_new_tokens=args.max_new_tokens,
            temperature=args.temperature, top_p=args.top_p,
            repetition_penalty=args.repetition_penalty,
        )
        record = {
            "arm": key, "sample_id": case["sample_id"], "ordinal": case["ordinal"],
            "messages": case["messages"], "seed": SEED_BASE + case["ordinal"], **generated,
        }
        done.append(record)
        write_jsonl(partial, done)
        print(json.dumps({"arm": key, "turn": case["ordinal"], "response": generated["response"]}, ensure_ascii=False), flush=True)
    return done


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--adapter-path", type=Path, required=True)
    parser.add_argument("--expected-adapter-sha256", required=True)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.75)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--repetition-penalty", type=float, default=1.1)
    args = parser.parse_args()
    output, adapter = args.output_dir.resolve(), args.adapter_path.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if common._sha256(adapter / "adapter_model.safetensors") != args.expected_adapter_sha256.lower():
        raise ValueError("Adapter hash mismatch")
    cases = select_cases()
    write_jsonl(output / "shared_inputs.jsonl", [{k: v for k, v in row.items() if k != "reference"} for row in cases])
    write_jsonl(output / "references.jsonl", [{"ordinal": row["ordinal"], "sample_id": row["sample_id"], "reference": row["reference"]} for row in cases])

    model, tokenizer = FastModel.from_pretrained(
        model_name=str(adapter), max_seq_length=6144, dtype=torch.bfloat16,
        load_in_4bit=True, text_only=True, trust_remote_code=True, use_exact_model_name=True,
    )
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    FastModel.for_inference(model)
    base = run_arm(model, tokenizer, cases, key="base", adapter_enabled=False, output=output, args=args)
    qlora = run_arm(model, tokenizer, cases, key="qlora", adapter_enabled=True, output=output, args=args)
    turns = []
    for case, a, b in zip(cases, base, qlora, strict=True):
        turns.append({
            "turn": case["ordinal"], "phase": f"{case['task']} / {case['scene']}",
            "player": case["messages"][-1]["content"],
            "authoritative_world": "完整 V3 dev 上下文见模型消息",
            "expectation": "按内容质量判断；参考答案未展示在审核页",
            "base": a, "qlora": b,
            "sample_id": case["sample_id"], "task": case["task"], "scene": case["scene"],
        })
    report = {
        "schema_version": 1, "scope": "gemma4_v3_exact_training_format_dev60_ab",
        "generated_at": datetime.now().astimezone().isoformat(), "status": "completed",
        "controls": {
            "held_out_dev_only": True, "turns": 60, "tasks": {task: PER_TASK for task in TASKS},
            "same_messages_between_arms": True, "independent_questions": True,
            "exact_training_role_structure": ["system", "user", "assistant", "user", "assistant", "user"],
            "json_reply_contract": True, "adapter_sha256": args.expected_adapter_sha256.lower(),
        },
        "arms": {"base": {"turns": base}, "qlora": {"turns": qlora}},
        "turns": turns,
        "summary": {
            "turns": 60, "base_completed": len(base), "qlora_completed": len(qlora),
            "base_generation_failures": sum(bool(x.get("generation_error")) for x in base),
            "qlora_generation_failures": sum(bool(x.get("generation_error")) for x in qlora),
            "speed": {"base": common._speed_summary(base), "qlora": common._speed_summary(qlora)},
            "automatic_quality_judgment": False,
        },
    }
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "manifest.json").write_text(json.dumps({
        "report_sha256": common._sha256(report_path),
        "inputs_sha256": common._sha256(output / "shared_inputs.jsonl"),
        "adapter_sha256": args.expected_adapter_sha256.lower(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

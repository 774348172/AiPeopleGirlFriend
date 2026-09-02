"""Evaluate clean base versus medium counterfactual DPO on disjoint held-out cases."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template

import run_gemma4_12b_base_qlora_60turn as common


ROOT = Path(__file__).resolve().parents[1]
CREATE_ROOT = ROOT.parent / "AiPeopleCreate"
DATA_ROOT = CREATE_ROOT / "训练数据" / "baiweixi_counterfactual_preference_medium_v2"
DEV_PATH = DATA_ROOT / "dev.jsonl"
CASES_PATH = DATA_ROOT / "generation_cases.json"
MANIFEST_PATH = DATA_ROOT / "manifest.json"
ADAPTER = (
    ROOT
    / "training_packages"
    / "training_package_baiweixi_gemma4_preference_medium"
    / "outputs"
    / "counterfactual_dpo_medium_v2"
)
EXPERIMENT_SCOPE = "clean_base_prompt_memory_vs_medium_counterfactual_dpo"
PREFERENCE_ARM_LABEL = "B 同一裸基座 + 中规模反事实 DPO Adapter"
TRAINING_METHOD = "DPO"
TRAINING_ROWS = 240
TRAINING_DATA_MANIFEST_PATH = MANIFEST_PATH
SEED_BASE = 2026092000
UNKNOWN_MARKERS = ("不清楚", "不确定", "不知道", "不记得", "没记住", "没有可靠", "没有能确认", "没有答案")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def speed_summary(values: list[dict[str, Any]]) -> dict[str, Any]:
    def mean(key: str) -> float | None:
        found = [float(item[key]) for item in values if item.get(key) is not None]
        return round(statistics.fmean(found), 2) if found else None

    return {
        "turns": len(values),
        "mean_first_visible_ms": mean("first_visible_ms"),
        "mean_elapsed_ms": mean("elapsed_ms"),
        "mean_eval_tokens_per_second": mean("eval_tokens_per_second"),
    }


def _completion_logp(model: Any, tokenizer: Any, row: dict[str, Any], field: str, adapter_enabled: bool) -> dict[str, float]:
    prompt_ids = tokenizer.apply_chat_template(
        row["prompt"], tokenize=True, add_generation_prompt=True, enable_thinking=False
    )
    full_ids = tokenizer.apply_chat_template(
        row["prompt"] + row[field], tokenize=True, add_generation_prompt=False, enable_thinking=False
    )
    if full_ids[: len(prompt_ids)] != prompt_ids:
        raise RuntimeError(f"chat-template prefix mismatch: {row['sample_id']} {field}")
    input_ids = torch.tensor([full_ids], device="cuda")
    context = nullcontext() if adapter_enabled else model.disable_adapter()
    with context, torch.inference_mode():
        logits = model(input_ids=input_ids).logits[:, :-1].float()
        targets = input_ids[:, 1:]
        token_logps = torch.log_softmax(logits, dim=-1).gather(-1, targets.unsqueeze(-1)).squeeze(-1)
    completion = token_logps[:, len(prompt_ids) - 1 :]
    return {
        "sum_logp": round(float(completion.sum().item()), 6),
        "mean_logp": round(float(completion.mean().item()), 6),
        "tokens": int(completion.numel()),
    }


def score_preferences(model: Any, tokenizer: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored: list[dict[str, Any]] = []
    for ordinal, row in enumerate(rows, 1):
        record: dict[str, Any] = {
            "ordinal": ordinal,
            "sample_id": row["sample_id"],
            "condition": row["condition"],
            "category": row["category"],
            "rejected_type": row["rejected_type"],
        }
        for arm, enabled in (("base", False), ("preference", True)):
            chosen = _completion_logp(model, tokenizer, row, "chosen", enabled)
            rejected = _completion_logp(model, tokenizer, row, "rejected", enabled)
            # Mean log-prob avoids a false advantage for the shorter completion.
            margin = chosen["mean_logp"] - rejected["mean_logp"]
            record[arm] = {
                "chosen": chosen,
                "rejected": rejected,
                "normalized_margin": round(margin, 6),
                "prefers_chosen": margin > 0,
            }
        record["margin_shift"] = round(
            record["preference"]["normalized_margin"] - record["base"]["normalized_margin"], 6
        )
        scored.append(record)
        print(
            json.dumps(
                {
                    "margin": ordinal,
                    "condition": row["condition"],
                    "base": record["base"]["normalized_margin"],
                    "preference": record["preference"]["normalized_margin"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    def summarize(arm: str, condition: str | None = None) -> dict[str, Any]:
        selected = [item for item in scored if condition is None or item["condition"] == condition]
        margins = [float(item[arm]["normalized_margin"]) for item in selected]
        chosen_logps = [float(item[arm]["chosen"]["mean_logp"]) for item in selected]
        rejected_logps = [float(item[arm]["rejected"]["mean_logp"]) for item in selected]
        return {
            "rows": len(selected),
            "preference_accuracy": round(sum(item[arm]["prefers_chosen"] for item in selected) / len(selected), 4),
            "mean_normalized_margin": round(statistics.fmean(margins), 6),
            "median_normalized_margin": round(statistics.median(margins), 6),
            "mean_chosen_logp": round(statistics.fmean(chosen_logps), 6),
            "mean_rejected_logp": round(statistics.fmean(rejected_logps), 6),
        }

    return {
        "note": "Normalized completion log-prob margin; positive means chosen is preferred. This is a learning-signal diagnostic, not a quality verdict.",
        "summary": {
            arm: {
                "all": summarize(arm),
                "known": summarize(arm, "known"),
                "unknown": summarize(arm, "unknown"),
            }
            for arm in ("base", "preference")
        },
        "mean_margin_shift": round(statistics.fmean(item["margin_shift"] for item in scored), 6),
        "rows": scored,
    }


def generation_observation(cases: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    unknown = [case for case in cases if case["condition"] == "unknown"]
    marker_hits = [case for case in unknown if any(marker in case["answers"][arm]["response"] for marker in UNKNOWN_MARKERS)]
    return {
        "unknown_cases": len(unknown),
        "unknown_acknowledgment_marker_hits": len(marker_hits),
        "warning": "Only a surface observation. Human review is authoritative and must check fabricated facts/actions and naturalness.",
    }


def visible_memory(messages: list[dict[str, str]]) -> str:
    system = messages[0]["content"]
    marker = "[相关记忆]\n"
    if marker not in system:
        return "未找到相关记忆分区"
    value = system.split(marker, 1)[1].split("\n\n[允许表达的动作]", 1)[0].strip()
    return value or "无"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--skip-margins", action="store_true")
    parser.add_argument("--max-cases", type=int)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not (ADAPTER / "adapter_model.safetensors").is_file():
        raise RuntimeError(f"medium Adapter missing: {ADAPTER}")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for name, path in (("dev.jsonl", DEV_PATH), ("generation_cases.json", CASES_PATH)):
        if manifest["files"][name] != sha256(path):
            raise RuntimeError(f"{name} hash differs from manifest")
    dev_rows = load_jsonl(DEV_PATH)
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if args.max_cases is not None:
        if args.max_cases < 1:
            raise RuntimeError("--max-cases must be positive")
        cases = cases[: args.max_cases]
    for case in cases:
        case["visible_memory"] = visible_memory(case["messages"])

    model, tokenizer = FastModel.from_pretrained(
        model_name=str(ADAPTER),
        max_seq_length=896,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
        attn_implementation="eager",
    )
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    FastModel.for_inference(model)

    margin_report = None if args.skip_margins else score_preferences(model, tokenizer, dev_rows)
    if margin_report is not None:
        write_json(output_dir / "heldout_margin_report.json", margin_report)

    results: list[dict[str, Any]] = []
    for case in cases:
        answers: dict[str, Any] = {}
        for arm, enabled in (("base", False), ("preference", True)):
            generated = common._generate(
                model,
                tokenizer,
                case["messages"],
                seed=SEED_BASE + int(case["ordinal"]),
                adapter_enabled=enabled,
                max_new_tokens=96,
                temperature=0.3,
                top_p=0.9,
                repetition_penalty=1.05,
            )
            answers[arm] = generated
            print(
                json.dumps(
                    {
                        "turn": case["ordinal"],
                        "arm": arm,
                        "condition": case["condition"],
                        "response": generated["response"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        results.append({**case, "answers": answers})
        write_json(output_dir / "report.partial.json", {"cases": results})

    report = {
        "schema_version": 2,
        "scope": EXPERIMENT_SCOPE,
        "status": "awaiting_human_review",
        "generated_at": datetime.now().astimezone().isoformat(),
        "automatic_quality_judgment": False,
        "human_adjudication_is_authoritative": True,
        "arms": {
            "base": "A 裸基座 + 正式角色 Prompt/记忆",
            "preference": PREFERENCE_ARM_LABEL,
        },
        "controls": {
            "same_base_model": True,
            "same_prompt_memory_and_runtime_sections": True,
            "same_generation_seed_per_case": True,
            "legacy_sft_adapter_loaded": False,
            "held_out_subject_families": True,
            "training_preference_rows": TRAINING_ROWS,
            "training_method": TRAINING_METHOD,
            "temperature": 0.3,
            "top_p": 0.9,
            "repetition_penalty": 1.05,
            "max_new_tokens": 96,
            "thinking": False,
            "generated_case_count": len(cases),
        },
        "precommitted_gate": manifest["generation_gate"]["acceptance_thresholds"],
        "gate_status": "pending_human_review",
        "adapter": {
            "path": str(ADAPTER.resolve()),
            "sha256": sha256(ADAPTER / "adapter_model.safetensors"),
        },
        "data_manifest": {
            "path": str(TRAINING_DATA_MANIFEST_PATH.resolve()),
            "sha256": sha256(TRAINING_DATA_MANIFEST_PATH),
        },
        "evaluation_case_manifest": {"path": str(MANIFEST_PATH.resolve()), "sha256": sha256(MANIFEST_PATH)},
        "heldout_margin": None if margin_report is None else margin_report["summary"],
        "mean_margin_shift": None if margin_report is None else margin_report["mean_margin_shift"],
        "speed": {
            arm: speed_summary([case["answers"][arm] for case in results]) for arm in ("base", "preference")
        },
        "surface_observation": {
            arm: generation_observation(results, arm) for arm in ("base", "preference")
        },
        "cases": results,
    }
    write_json(output_dir / "report.json", report)
    print(f"REPORT={output_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

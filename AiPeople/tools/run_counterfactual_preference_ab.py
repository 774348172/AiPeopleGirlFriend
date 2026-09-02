"""Run clean-base versus counterfactual-DPO A/B on held-out paired cases."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template

import run_gemma4_12b_base_qlora_60turn as common


ROOT = Path(__file__).resolve().parents[1]
CREATE_ROOT = ROOT.parent / "AiPeopleCreate"
sys.path.insert(0, str(CREATE_ROOT / "tools"))
import build_counterfactual_preference_pilot as data_builder  # noqa: E402


ADAPTER = (
    ROOT
    / "training_packages"
    / "training_package_baiweixi_gemma4_preference_pilot"
    / "outputs"
    / "counterfactual_dpo_adapter"
)
DATA_MANIFEST = (
    CREATE_ROOT / "训练数据" / "baiweixi_counterfactual_preference_pilot_v1" / "manifest.json"
)
SEED_BASE = 2026091000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def build_cases() -> list[dict[str, Any]]:
    character_prompt = data_builder.formal_character_prompt()
    cases: list[dict[str, Any]] = []
    ordinal = 0
    for family, fact, question, answer in data_builder.EVAL_FACTS:
        for condition, memory in (("known", fact), ("unknown", None)):
            ordinal += 1
            messages = data_builder.context_prompt(character_prompt, memory)
            messages.append({"role": "user", "content": question})
            cases.append(
                {
                    "ordinal": ordinal,
                    "case_id": f"heldout.{family}.{condition}",
                    "category": "有证据" if condition == "known" else "无证据",
                    "condition": condition,
                    "question": question,
                    "visible_memory": fact if memory else "无",
                    "reference_fact": answer if condition == "known" else None,
                    "review_focus": (
                        f"相关记忆明确给出答案：{answer}；审核是否正确承接。"
                        if condition == "known"
                        else "上下文没有答案；审核是否承认不知道，且没有编造具体事实或查询动作。"
                    ),
                    "messages": messages,
                }
            )

    persona = (
        ("persona.name", "你叫什么名字？", "审核是否保持白未晞身份。"),
        ("persona.home", "你愿意一直留在这里吗？", "审核角色早期关系与含蓄、克制风格。"),
        ("persona.box", "那个纸箱占地方，我直接扔了好不好？", "审核是否记得纸箱意义且自然回应。"),
        ("persona.action", "你现在在做什么？", "审核是否简短自然、无动作旁白和规则文本。"),
    )
    for case_id, question, focus in persona:
        ordinal += 1
        messages = data_builder.context_prompt(character_prompt, None)
        messages.append({"role": "user", "content": question})
        cases.append(
            {
                "ordinal": ordinal,
                "case_id": case_id,
                "category": "人格保持",
                "condition": "persona",
                "question": question,
                "visible_memory": "无额外情节记忆；角色正式 Prompt 仍完整提供",
                "reference_fact": None,
                "review_focus": focus,
                "messages": messages,
            }
        )
    return cases


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = build_cases()
    model, tokenizer = FastModel.from_pretrained(
        model_name=str(ADAPTER),
        max_seq_length=768,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
        attn_implementation="eager",
    )
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    FastModel.for_inference(model)
    results: list[dict[str, Any]] = []
    for case in cases:
        answers: dict[str, Any] = {}
        for arm, adapter_enabled in (("base", False), ("preference", True)):
            generated = common._generate(
                model,
                tokenizer,
                case["messages"],
                seed=SEED_BASE + case["ordinal"],
                adapter_enabled=adapter_enabled,
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

    base_generations = [case["answers"]["base"] for case in results]
    preference_generations = [case["answers"]["preference"] for case in results]
    report = {
        "schema_version": 1,
        "scope": "clean_base_prompt_memory_vs_counterfactual_dpo",
        "status": "awaiting_human_review",
        "generated_at": datetime.now().astimezone().isoformat(),
        "automatic_quality_judgment": False,
        "human_adjudication_is_authoritative": True,
        "arms": {
            "base": "A 裸基座 + 正式角色 Prompt/记忆",
            "preference": "B 裸基座 + 同一 Prompt/记忆 + 反事实 DPO Adapter",
        },
        "controls": {
            "same_base_model": True,
            "same_prompt_and_memory": True,
            "same_generation_seed_per_case": True,
            "legacy_sft_adapter_loaded": False,
            "held_out_from_preference_training": True,
            "temperature": 0.3,
            "top_p": 0.9,
            "repetition_penalty": 1.05,
            "max_new_tokens": 96,
            "thinking": False,
        },
        "adapter": {
            "path": str(ADAPTER.resolve()),
            "sha256": sha256(ADAPTER / "adapter_model.safetensors"),
        },
        "data_manifest": {
            "path": str(DATA_MANIFEST.resolve()),
            "sha256": sha256(DATA_MANIFEST),
        },
        "speed": {
            "base": speed_summary(base_generations),
            "preference": speed_summary(preference_generations),
        },
        "cases": results,
    }
    write_json(output_dir / "report.json", report)
    print(f"REPORT={output_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

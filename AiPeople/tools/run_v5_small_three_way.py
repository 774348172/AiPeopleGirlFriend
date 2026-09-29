"""Run a 12-row human-review pilot: base, old Adapter, and new Adapter.

The three arms receive byte-identical GAME_REPLY-style prompts. Models are
loaded one at a time so the comparison is usable on a 16 GB GPU. This script
does not train and does not assign an automatic quality score; the generated
JSON/HTML is for human adjudication.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import html
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
GEN_ROOT = ROOT.parent / "AiPeopleCreate"
if str(GEN_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(GEN_ROOT / "tools"))

from runtime.gemma_nf4_assets import LocalGemmaNF4Package
from build_v5_pilot_dataset import (
    Concept,
    _load_baiweixi_render_context,
    _reference_reply,
    _scenario,
)
from data_gen_v4.runtime_grounded import render_game_reply_context_from_model_view
from runtime.world_mind.game_reply_context import (
    build_game_reply_chat_messages,
    parse_game_reply_dialogue_lines,
)

BASE_MODEL_PATH = ROOT / "training_packages" / "models" / "Gemma-4-12B-it"
OLD_MANIFEST = ROOT / "local_runtime" / "gemma4_nf4_fallback_manifest.json"
NEW_MANIFEST = ROOT / "local_runtime" / "gemma4_nf4_runtime_manifest.json"

# These concepts are held out from the V5 pilot training concepts and prior
# sealed suites.  They are only for the 12-row manual comparison below.
EVALUATION_CONCEPTS = (
    Concept("library_card.location", "library_card", "借书证", "location", "书桌收纳盒", "外套口袋"),
    Concept("humidifier.state", "humidifier", "加湿器", "state", "已经关掉", "还开着"),
    Concept("clinic.visit", "protagonist", "你的复诊预约", "appointment_time", "星期六上午九点", "星期五下午三点", "protagonist_plan"),
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _small_scenarios() -> list[dict[str, Any]]:
    # Three held-out concepts x four contracts = 12 rows.  They are not part
    # of the V5 pilot training concepts, so the comparison checks production
    # input alignment rather than prompt memorisation.
    tasks = (
        "reply_insufficient_information",
        "reply_subject_attribution",
        "reply_accept_authoritative_update",
        "reply_resolve_world_memory_conflict",
    )
    scenarios: list[dict[str, Any]] = []
    for index, concept in enumerate(EVALUATION_CONCEPTS, start=7000):
        for task in tasks:
            scenarios.append(_scenario(concept, task, index, sealed=False))
    return scenarios


def _prompt(scenario: dict[str, Any], anchor: str) -> list[dict[str, str]]:
    view = scenario["model_view"]
    messages = [
        {
            "role": "system",
            "content": f"{anchor}\n\n{render_game_reply_context_from_model_view(view)}",
        }
    ]
    dialogue_turns = parse_game_reply_dialogue_lines(
        tuple(str(value) for value in (view.get("recent_dialogue") or ()))
    )
    dialogue_messages = build_game_reply_chat_messages(
        dialogue_turns,
        character_display_name=str(view.get("character_display_name") or "白未晞"),
        current_protagonist_utterance=str(view["current_protagonist_utterance"]),
    )
    messages.extend(
        {"role": role, "content": content}
        for role, content in dialogue_messages
    )
    return messages


def _load_arm(label: str, manifest: Path | None):
    if manifest is None:
        model_path = BASE_MODEL_PATH
        asset = None
    else:
        asset = LocalGemmaNF4Package.load(manifest)
        model_path = asset.adapter_root
    model, tokenizer = FastModel.from_pretrained(
        model_name=str(model_path),
        max_seq_length=4096,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
        attn_implementation="eager",
    )
    if asset is not None and not getattr(model, "peft_config", None):
        raise RuntimeError(f"{label} manifest requested Adapter but PEFT is not loaded")
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    FastModel.for_inference(model)
    return model, tokenizer, asset


def _generate(model: Any, tokenizer: Any, messages: list[dict[str, str]], seed: int) -> dict[str, Any]:
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    encoded = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        enable_thinking=False,
        return_tensors="pt",
        return_dict=True,
    )
    encoded = {key: value.to("cuda") for key, value in encoded.items()}
    started = time.perf_counter()
    with torch.inference_mode():
        output = model.generate(
            **encoded,
            max_new_tokens=48,
            do_sample=False,
            repetition_penalty=1.05,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=list(dict.fromkeys(
                token_id
                for token_id in (tokenizer.eos_token_id, tokenizer.eot_token_id)
                if token_id is not None
            )),
            use_cache=True,
        )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    prompt_tokens = int(encoded["input_ids"].shape[-1])
    generated_ids = output[0, prompt_tokens:]
    parsed = tokenizer.parse_response(generated_ids)
    response = str(parsed.get("content") or "").strip()
    if not response:
        response = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
    if not response:
        raise RuntimeError("empty response")
    return {
        "response": response,
        "thinking": str(parsed.get("thinking") or "").strip() or None,
        "elapsed_ms": elapsed_ms,
        "prompt_tokens": prompt_tokens,
        "generated_tokens": int(generated_ids.shape[-1]),
    }


def _release(model: Any, tokenizer: Any) -> None:
    del tokenizer
    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


def _html(report: dict[str, Any], output: Path) -> None:
    rows = []
    for case in report["cases"]:
        arms = case["arms"]
        rows.append(
            "<article><h2>第{ordinal}题 · {task}</h2>"
            "<p class='q'>{question}</p>"
            "<details><summary>审核依据（权威答案，仅供人工审核）</summary><pre>{expected}</pre></details>"
            "<section class='grid'>{cards}</section></article>".format(
                ordinal=case["ordinal"],
                task=case["task_type"],
                question=html.escape(case["question"]),
                expected=html.escape(case["expected_reply"]),
                cards="".join(
                    "<div class='card'><h3>{label}</h3><p>{reply}</p><small>{ms} ms · {tokens} tokens</small>"
                    "<label>人工判断 <select data-id='{id}' data-arm='{arm}'><option value=''>未判断</option><option>好</option><option>差</option></select></label></div>".format(
                        label=html.escape(label),
                        reply=html.escape(arm.get("response", "[失败]")),
                        ms=arm.get("elapsed_ms", "-"),
                        tokens=arm.get("generated_tokens", "-"),
                        id=case["sample_id"],
                        arm=arm_name,
                    )
                    for arm_name, label in (("base", "裸基座"), ("old_adapter", "旧 Adapter"), ("new_adapter", "新 Adapter"))
                    for arm in [arms[arm_name]]
                ),
            )
        )
    payload = json.dumps(report, ensure_ascii=False).replace("</", "<\\/")
    output.write_text(
        "<!doctype html><meta charset='utf-8'><title>GAME_REPLY_CONTEXT_V1 12题三路人工审核</title>"
        "<style>body{font:15px system-ui;max-width:1200px;margin:24px auto;background:#f5f6f8;color:#20242a}article{background:#fff;margin:16px 0;padding:18px;border:1px solid #ddd}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.card{border:1px solid #ddd;padding:12px;min-height:150px}.q{font-weight:600}.card p{white-space:pre-wrap;min-height:55px}select{margin-left:8px}@media(max-width:800px){.grid{grid-template-columns:1fr}}</style>"
        + "".join(rows)
        + f"<button id='download'>下载人工审核结果</button><script>const report={payload};document.querySelectorAll('select').forEach(x=>x.onchange=()=>{{const c=report.cases.find(c=>c.sample_id===x.dataset.id);c.human_review=c.human_review||{{}};c.human_review[x.dataset.arm]=x.value;localStorage.setItem('v5-small-review',JSON.stringify(report));}});document.getElementById('download').onclick=()=>{{const blob=new Blob([JSON.stringify(report,null,2)],{{type:'application/json'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='v5_small_three_way_user_review.json';a.click();}};</script>",
        encoding="utf-8",
    )


def _materialize_dataset(report: dict[str, Any], output_dir: Path) -> None:
    """Persist the review inputs as a small, auditable training candidate set."""
    anchor = str(report["controls"]["system_anchor"])
    rows = []
    for case in report["cases"]:
        prompt_messages = _prompt({"model_view": case["model_view"]}, anchor)
        rows.append({
            "sample_id": case["sample_id"],
            "task_type": case["task_type"],
            "messages": [
                *prompt_messages,
                {"role": "assistant", "content": case["expected_reply"]},
            ],
            "supervised_message_indexes": [len(prompt_messages)],
            "human_review_required": True,
        })
    with (output_dir / "small_train_candidates.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    (output_dir / "small_dataset_manifest.json").write_text(
        json.dumps({
            "schema_version": "aip.game_reply_context_v1_small_dataset_manifest.v1",
            "rows": len(rows),
            "task_counts": {task: sum(row["task_type"] == task for row in rows) for task in sorted({row["task_type"] for row in rows})},
            "label_source": "deterministic oracle reference; not a substitute for human review",
            "training_started": False,
            "complete_dataset_generation_allowed": False,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "eval" / "world_mind_p0" / "game_reply_context_v1_three_way_20260903")
    parser.add_argument("--materialize-only", action="store_true")
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.materialize_only:
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        _materialize_dataset(report, output_dir)
        _html(report, output_dir / "review.html")
        print(f"DATASET={output_dir / 'small_train_candidates.jsonl'}")
        return 0
    profile, protocol, anchor = _load_baiweixi_render_context()
    scenarios = _small_scenarios()
    arms = (("base", "裸基座", None), ("old_adapter", "旧 Adapter", OLD_MANIFEST), ("new_adapter", "新 Adapter", NEW_MANIFEST))
    cases = [
        {
            "ordinal": index + 1,
            "sample_id": scenario["scenario_id"],
            "task_type": scenario["task_type"],
            "question": scenario["model_view"]["current_protagonist_utterance"],
            "model_view": scenario["model_view"],
            "expected_reply": _reference_reply(scenario),
            "prompt_sha256": hashlib.sha256(_json(_prompt(scenario, anchor)).encode("utf-8")).hexdigest(),
            "arms": {},
        }
        for index, scenario in enumerate(scenarios)
    ]
    by_id = {case["sample_id"]: case for case in cases}
    for arm_key, label, manifest in arms:
        print(f"[arm] loading {label}", flush=True)
        model, tokenizer, asset = _load_arm(label, manifest)
        for index, scenario in enumerate(scenarios):
            try:
                result = _generate(model, tokenizer, _prompt(scenario, anchor), 20260902 + index)
            except Exception as error:  # noqa: BLE001
                result = {"response": "", "error": str(error)}
            by_id[scenario["scenario_id"]]["arms"][arm_key] = result
            print(json.dumps({"arm": arm_key, "turn": index + 1, "response": result.get("response", "")}, ensure_ascii=False), flush=True)
        _release(model, tokenizer)
    for case in cases:
        case["human_review"] = {"base": "", "old_adapter": "", "new_adapter": ""}
    report = {
        "schema_version": "aip.v5_small_three_way_review.v1",
        "status": "awaiting_human_review",
        "generated_at": datetime.now().astimezone().isoformat(),
        "automatic_quality_judgment": False,
        "human_adjudication_is_authoritative": True,
        "controls": {
            "rows": len(cases),
            "task_counts": {task: sum(case["task_type"] == task for case in cases) for task in {case["task_type"] for case in cases}},
            "same_prompt_sha256_across_arms": True,
            "system_anchor_sha256": hashlib.sha256(anchor.encode("utf-8")).hexdigest(),
            "system_anchor": anchor,
            "thinking": False,
            "temperature": 0.0,
            "greedy_decoding": True,
            "max_new_tokens": 48,
            "models_loaded_sequentially": True,
            "profile_id": profile.get("profile_id"),
            "context_renderer": "GAME_REPLY_CONTEXT_V1",
            "protocol_version": protocol.get("package_version"),
        },
        "arms": {
            "base": {"label": "Gemma 4 12B IT NF4 裸基座", "manifest": None, "adapter_loaded": False},
            "old_adapter": {"label": "Gemma 4 12B NF4 + V5 targeted Adapter", "manifest": str(OLD_MANIFEST), "adapter_loaded": True},
            "new_adapter": {"label": "Gemma 4 12B NF4 + V5 full mixed Adapter", "manifest": str(NEW_MANIFEST), "adapter_loaded": True},
        },
        "cases": cases,
    }
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _materialize_dataset(report, output_dir)
    _html(report, output_dir / "review.html")
    print(f"REPORT={output_dir / 'report.json'}")
    print(f"REVIEW={output_dir / 'review.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

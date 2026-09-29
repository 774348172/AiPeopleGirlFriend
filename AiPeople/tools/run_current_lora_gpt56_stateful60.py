"""Run the current production LoRA against GPT-5.6-sol on the latest 60-turn context."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import requests
import torch

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE_ROOT / "AiPeopleCreate/tools"))

from build_v5_pilot_dataset import _load_baiweixi_production_contract
from run_frontend_stateful60 import ROOT, TURNS, context
from run_gemma4_stateful_grounding16 import generate
from run_v5_small_three_way import _load_arm, _release


GPT_MODEL = "gpt-5.6-sol"
HISTORY_LIMIT_MESSAGES = 6
LOCAL_MANIFEST = ROOT / "local_runtime/gemma4_nf4_runtime_manifest.json"
EXCLUDED_CANDIDATE = (
    ROOT / "training_packages/outputs/baiweixi_frontend_full_v5_final_20260910"
)


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _prompt_sha(messages: list[dict[str, str]]) -> str:
    payload = json.dumps(
        messages, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha_bytes(payload)


def _messages(
    turn: dict[str, Any], anchor: str, history: list[dict[str, str]]
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": context(turn, anchor)},
        *history[-HISTORY_LIMIT_MESSAGES:],
        {"role": "user", "content": turn["player"]},
    ]


def _extract_response_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for output in payload.get("output", []):
        if not isinstance(output, dict) or output.get("type") != "message":
            continue
        for content in output.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    response = "".join(parts).strip()
    if not response:
        raise RuntimeError("GPT-5.6-sol returned no output_text")
    return response


def _generate_gpt(messages: list[dict[str, str]], ordinal: int) -> dict[str, Any]:
    base_url = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not base_url or not api_key:
        raise RuntimeError("OPENAI_BASE_URL and OPENAI_API_KEY are required")
    body = {
        "model": GPT_MODEL,
        "input": [
            {
                "role": message["role"],
                "content": [
                    {
                        "type": (
                            "output_text"
                            if message["role"] == "assistant"
                            else "input_text"
                        ),
                        "text": message["content"],
                    }
                ],
            }
            for message in messages
        ],
        "reasoning": {"effort": "low"},
        "max_output_tokens": 192,
        "metadata": {"experiment": "current-lora-gpt56-stateful60", "turn": str(ordinal)},
    }
    started = time.perf_counter()
    response = requests.post(
        base_url + "/responses",
        headers={"Authorization": "Bearer " + api_key},
        json=body,
        timeout=300,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    if not response.ok:
        raise RuntimeError(
            f"GPT-5.6-sol HTTP {response.status_code}: {response.text[:1000]}"
        )
    payload = response.json()
    return {
        "response": _extract_response_text(payload),
        "elapsed_ms": elapsed_ms,
        "response_id": payload.get("id"),
        "model": payload.get("model"),
        "status": payload.get("status"),
        "reasoning_effort": "low",
        "usage": payload.get("usage"),
    }


def _save(report: dict[str, Any], output: Path) -> None:
    temporary = output / "report.tmp"
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(output / "report.json")


def _render_review(report: dict[str, Any], output: Path) -> None:
    template = (ROOT / "tools/frontend_regression12_review.html").read_text(
        encoding="utf-8"
    )
    template = template.replace("白未晞 · 新训练三方审核", "当前 LoRA Q4 与 GPT-5.6-sol · 60轮审核")
    template = template.replace("frontend-regression12:", "current-lora-gpt56-stateful60:")
    template = template.replace(
        "saved.cases.length===12", "saved.cases.length===original.cases.length"
    )
    template = template.replace(
        "+' / 36'", "+' / '+(report.cases.length*arms.length)"
    ).replace("n===36", "n===report.cases.length*arms.length")
    template = template.replace(
        "frontend_full_regression12_user_review.json",
        "current_lora_gpt56_stateful60_user_review.json",
    )
    template = template.replace(
        "c.messages.map(m=>m.role+'\\n'+m.content).join('\\n\\n')",
        "c.arms[arms[0]].messages.map(m=>m.role+'\\n'+m.content).join('\\n\\n')",
    )
    template = template.replace(
        "repeat(3,minmax(0,1fr))", "repeat(2,minmax(0,1fr))"
    )
    template = template.replace(
        "box.append(title,text,time,label)",
        "const detail=document.createElement('details');"
        "const summary=document.createElement('summary');"
        "summary.textContent='本模型实际输入与历史';"
        "const pre=document.createElement('pre');"
        "pre.textContent=c.arms[arm].messages.map(m=>m.role+'\\n'+m.content).join('\\n\\n');"
        "detail.append(summary,pre);box.append(title,text,time,detail,label)",
    )
    (output / "review.html").write_text(
        template.replace(
            "__REPORT__", json.dumps(report, ensure_ascii=False).replace("<", "\\u003c")
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-local", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.resume:
        if not (output / "report.json").is_file():
            raise FileNotFoundError("Cannot resume without report.json")
    else:
        output.mkdir(parents=True, exist_ok=False)

    manifest = json.loads(LOCAL_MANIFEST.read_text(encoding="utf-8"))
    anchor, _ = _load_baiweixi_production_contract()
    candidate_hash = (
        _sha_file(EXCLUDED_CANDIDATE / "adapter_model.safetensors")
        if (EXCLUDED_CANDIDATE / "adapter_model.safetensors").is_file()
        else None
    )
    report: dict[str, Any] = {
        "schema_version": "aip.current_lora_gpt56_stateful60.v1",
        "status": "running",
        "generated_at": datetime.now().astimezone().isoformat(),
        "automatic_quality_judgment": False,
        "human_adjudication_is_authoritative": True,
        "controls": {
            "context_renderer": "GAME_REPLY_CONTEXT_V1",
            "role_anchor_sha256": _sha_bytes(anchor.encode("utf-8")),
            "turns": 60,
            "history_limit_messages": HISTORY_LIMIT_MESSAGES,
            "history_policy": "each arm keeps its own latest six generated messages",
            "same_external_state_and_player_inputs": True,
            "identical_history_after_turn_one": False,
            "oracle_excluded_from_generation": True,
            "thinking": False,
            "local_decoding": {
                "max_new_tokens": 96,
                "do_sample": False,
                "repetition_penalty": 1.05,
            },
            "gpt_decoding": {
                "reasoning_effort": "low",
                "max_output_tokens": 192,
                "surface": "Responses-compatible authenticated endpoint",
            },
            "scope": "scripted production renderer and state injection; not the unfinished formal game-world writeback API",
            "limitations": [
                "Each arm's generated history diverges after turn one by design.",
                "The GPT endpoint can include provider-side host instructions that are not byte-identical to Gemma's runtime.",
                "This historical 60-turn trajectory is not a new sealed generalization set.",
                "Human review is required; no automatic quality conclusion is generated.",
            ],
            "excluded_unpromoted_candidate": {
                "path": str(EXCLUDED_CANDIDATE),
                "adapter_sha256": candidate_hash,
                "reason": "latest-trained candidate was not promoted; latest human result was 55/60 versus current production 56/60",
            },
        },
        "arms": {
            "current_lora": {
                "label": "当前生产 LoRA Q4（Gemma NF4 + 全量混合 Adapter）",
                "model_id": manifest["model_id"],
                "base_model_id": manifest["base_model_id"],
                "revision": manifest["revision"],
                "adapter_id": manifest["adapter_id"],
                "adapter_sha256": next(
                    item["sha256"]
                    for item in manifest["adapter_files"]
                    if item["path"] == "adapter_model.safetensors"
                ),
                "manifest": str(LOCAL_MANIFEST.resolve()),
            },
            "gpt56": {
                "label": "GPT-5.6-sol（low reasoning）",
                "model_id": GPT_MODEL,
            },
        },
        "cases": [],
    }
    for ordinal, turn in enumerate(TURNS, 1):
        report["cases"].append(
            {
                "ordinal": ordinal,
                "sample_id": f"current.lora.gpt56.stateful60.{ordinal:02d}",
                "task_type": turn["category"],
                "question": turn["player"],
                "script_world": turn["world"],
                "review_expectation": turn["focus"],
                "arms": {},
                "human_review": {"current_lora": "", "gpt56": ""},
            }
        )
    if args.resume:
        existing = json.loads((output / "report.json").read_text(encoding="utf-8"))
        if existing.get("schema_version") != report["schema_version"]:
            raise ValueError("Resume report schema mismatch")
        if len(existing.get("cases", [])) != len(TURNS):
            raise ValueError("Resume report turn count mismatch")
        if (
            existing.get("arms", {}).get("current_lora", {}).get("adapter_sha256")
            != report["arms"]["current_lora"]["adapter_sha256"]
        ):
            raise ValueError("Resume report adapter mismatch")
        if existing.get("controls", {}).get("role_anchor_sha256") != report[
            "controls"
        ]["role_anchor_sha256"]:
            raise ValueError("Resume report role anchor mismatch")
        report = existing
        report["status"] = "running"
    _save(report, output)

    if not args.skip_local:
        print("LOADING current_lora", flush=True)
        model, tokenizer, asset = _load_arm("old_adapter", LOCAL_MANIFEST)
        history: list[dict[str, str]] = []
        try:
            for ordinal, turn in enumerate(TURNS, 1):
                messages = _messages(turn, anchor, history)
                result = generate(model, tokenizer, messages, 2026091100 + ordinal)
                result["messages"] = messages
                result["prompt_sha256"] = _prompt_sha(messages)
                report["cases"][ordinal - 1]["arms"]["current_lora"] = result
                history.extend(
                    [
                        messages[-1],
                        {"role": "assistant", "content": result["response"]},
                    ]
                )
                _save(report, output)
                print(
                    json.dumps(
                        {"arm": "current_lora", "turn": ordinal, "reply": result["response"]},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        finally:
            _release(model, tokenizer)
            gc.collect()
            torch.cuda.empty_cache()

    print("STARTING gpt56", flush=True)
    history = []
    for ordinal, turn in enumerate(TURNS, 1):
        messages = _messages(turn, anchor, history)
        saved = report["cases"][ordinal - 1]["arms"].get("gpt56")
        if saved:
            if saved.get("prompt_sha256") != _prompt_sha(messages):
                raise ValueError(f"GPT resume prompt mismatch at turn {ordinal}")
            history.extend(
                [messages[-1], {"role": "assistant", "content": saved["response"]}]
            )
            print(f"SKIPPING completed gpt56 turn {ordinal}", flush=True)
            continue
        result = _generate_gpt(messages, ordinal)
        result["messages"] = messages
        result["prompt_sha256"] = _prompt_sha(messages)
        report["cases"][ordinal - 1]["arms"]["gpt56"] = result
        history.extend(
            [messages[-1], {"role": "assistant", "content": result["response"]}]
        )
        _save(report, output)
        print(
            json.dumps(
                {"arm": "gpt56", "turn": ordinal, "reply": result["response"]},
                ensure_ascii=False,
            ),
            flush=True,
        )

    report["status"] = "awaiting_human_review"
    report["completed_at"] = datetime.now().astimezone().isoformat()
    _save(report, output)
    _render_review(report, output)
    print("COMPLETED 120/120", flush=True)
    print("REPORT=" + str(output / "report.json"), flush=True)
    print("REVIEW=" + str(output / "review.html"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import run_baiweixi_60turn_player_simulation as simulation
import run_baiweixi_paired_raw_context_ab as paired


LOCAL_MODEL = "qwen3.5-9b-q6:latest"
LOCAL_LABEL = "Qwen3.5-9B Q6_K"
GPT_MODEL = "gpt-5.6-sol"
HISTORY_LIMIT_MESSAGES = 6
SEED_BASE = 2026082200


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number} is not an object")
        values.append(value)
    return values


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_inputs(values: list[dict[str, Any]]) -> None:
    if len(values) != 60:
        raise ValueError(f"shared input count must be 60, got {len(values)}")
    for ordinal, value in enumerate(values, 1):
        if value.get("ordinal") != ordinal:
            raise ValueError(f"shared input ordinal mismatch at {ordinal}")
        for key in ("phase", "system", "player"):
            if not isinstance(value.get(key), str) or not value[key].strip():
                raise ValueError(f"shared input {ordinal} has invalid {key}")
        forbidden = {"expectation", "required_groups", "forbidden_facts"}
        if forbidden.intersection(value):
            raise ValueError(f"shared input {ordinal} contains Oracle fields")


def _validate_gpt(values: list[dict[str, Any]]) -> None:
    if len(values) != 60:
        raise ValueError(f"GPT response count must be 60, got {len(values)}")
    for ordinal, value in enumerate(values, 1):
        if value.get("ordinal") != ordinal:
            raise ValueError(f"GPT response ordinal mismatch at {ordinal}")
        if value.get("model") != GPT_MODEL:
            raise ValueError(f"GPT response {ordinal} has wrong model")
        if not isinstance(value.get("response"), str) or not value["response"].strip():
            raise ValueError(f"GPT response {ordinal} is empty")


def _messages(
    turn: dict[str, Any], history: list[dict[str, str]]
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": turn["system"]},
        *history[-HISTORY_LIMIT_MESSAGES:],
        {"role": "user", "content": turn["player"]},
    ]


async def _generate_local(
    client: httpx.AsyncClient,
    messages: list[dict[str, str]],
    seed: int,
    request_id: str,
) -> dict[str, Any]:
    body = {
        "model": LOCAL_MODEL,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {
            "num_ctx": 4096,
            "num_predict": 180,
            "temperature": 0.75,
            "top_p": 0.9,
            "repeat_penalty": 1.1,
            "seed": seed,
            "num_gpu": 999,
        },
    }
    started = time.perf_counter()
    response = await client.post(
        "/api/chat", json=body, headers={"X-Request-ID": request_id}
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    response.raise_for_status()
    payload = response.json()
    message = payload.get("message")
    reply = message.get("content") if isinstance(message, dict) else None
    if not isinstance(reply, str) or not reply.strip():
        raise RuntimeError(f"empty response for {request_id}")
    return {
        "response": reply.strip(),
        "thinking": message.get("thinking") if isinstance(message, dict) else None,
        "elapsed_ms": elapsed_ms,
        "load_duration_ns": payload.get("load_duration"),
        "prompt_eval_count": payload.get("prompt_eval_count"),
        "prompt_eval_duration_ns": payload.get("prompt_eval_duration"),
        "eval_count": payload.get("eval_count"),
        "eval_duration_ns": payload.get("eval_duration"),
        "done_reason": payload.get("done_reason"),
    }


async def _run_local(
    inputs: list[dict[str, Any]], base_url: str
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    history: list[dict[str, str]] = []
    turns: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"), timeout=httpx.Timeout(300.0)
    ) as client:
        digests = await paired._model_digests(client)
        if LOCAL_MODEL not in digests:
            raise RuntimeError(f"Ollama model is not installed: {LOCAL_MODEL}")
        for turn in inputs:
            ordinal = int(turn["ordinal"])
            messages = _messages(turn, history)
            prompt = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
            seed = SEED_BASE + ordinal
            generated = await _generate_local(
                client,
                messages=messages,
                seed=seed,
                request_id=f"qwen35-9b-q6-gpt56-60:qwen35:turn-{ordinal:02d}",
            )
            reply = str(generated["response"]).strip()
            turns.append(
                {
                    "ordinal": ordinal,
                    "messages": messages,
                    "prompt_sha256": paired._sha256_text(prompt),
                    "seed": seed,
                    "response": reply,
                    **{
                        key: value
                        for key, value in generated.items()
                        if key != "response"
                    },
                }
            )
            history.extend(
                (
                    {"role": "user", "content": turn["player"]},
                    {"role": "assistant", "content": reply},
                )
            )
            print(
                json.dumps(
                    {"turn": ordinal, "arm": "qwen35", "response": reply},
                    ensure_ascii=False,
                ),
                flush=True,
            )
    return (
        {
            "key": "qwen35",
            "label": LOCAL_LABEL,
            "model": LOCAL_MODEL,
            "ollama_digest": digests[LOCAL_MODEL],
            "surface": "Ollama local native chat template, thinking disabled",
            "turns": turns,
        },
        history,
    )


def _build_gpt_arm(
    inputs: list[dict[str, Any]], responses: list[dict[str, Any]]
) -> dict[str, Any]:
    history: list[dict[str, str]] = []
    turns: list[dict[str, Any]] = []
    for turn, candidate in zip(inputs, responses, strict=True):
        ordinal = int(turn["ordinal"])
        messages = _messages(turn, history)
        prompt = paired._chatml(messages)
        reply = candidate["response"].strip()
        turns.append(
            {
                "ordinal": ordinal,
                "messages": messages,
                "prompt_sha256": paired._sha256_text(prompt),
                "seed": None,
                "response": reply,
                "reasoning_effort": candidate.get("reasoning_effort", "low"),
                "elapsed_ms": None,
            }
        )
        history.extend(
            (
                {"role": "user", "content": turn["player"]},
                {"role": "assistant", "content": reply},
            )
        )
    return {
        "key": "gpt",
        "label": "GPT-5.6-sol",
        "model": GPT_MODEL,
        "surface": "60 fresh isolated Codex child-agent calls",
        "turns": turns,
    }


def _mean_elapsed(turns: list[dict[str, Any]]) -> float | None:
    values = [float(turn["elapsed_ms"]) for turn in turns if turn.get("elapsed_ms")]
    return round(statistics.fmean(values), 2) if values else None


async def _run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    input_path = run_dir / "shared_inputs.jsonl"
    manifest_path = run_dir / "input_manifest.json"
    gpt_path = run_dir / "gpt5_6_sol_responses_sequential.jsonl"
    provenance_path = run_dir / "gpt_sequential_provenance.json"
    for path in (input_path, manifest_path, gpt_path, provenance_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    inputs = _load_jsonl(input_path)
    gpt_responses = _load_jsonl(gpt_path)
    _validate_inputs(inputs)
    _validate_gpt(gpt_responses)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    actual_input_sha = _sha256(input_path)
    if manifest.get("input_sha256") != actual_input_sha:
        raise ValueError("input Manifest SHA256 mismatch")
    if provenance.get("input_sha256") != actual_input_sha:
        raise ValueError("GPT provenance input SHA256 mismatch")
    if provenance.get("future_turns_visible") is not False:
        raise ValueError("GPT provenance must prove future_turns_visible=false")
    if provenance.get("history_limit_messages") != HISTORY_LIMIT_MESSAGES:
        raise ValueError("GPT provenance history limit mismatch")
    if provenance.get("call_count") != 60:
        raise ValueError("GPT provenance must record 60 fresh isolated calls")

    local_arm, _ = await _run_local(inputs, args.ollama_url)
    gpt_arm = _build_gpt_arm(inputs, gpt_responses)
    report = {
        "schema_version": 1,
        "scope": "stateful_qwen35_9b_q6_vs_gpt56sol_codex_internal_60turn",
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": "completed",
        "controls": {
            "turn_count": 60,
            "history_limit_messages": HISTORY_LIMIT_MESSAGES,
            "same_oracle_free_turn_inputs": True,
            "input_sha256": actual_input_sha,
            "each_arm_retains_own_history": True,
            "qwen35_generation": {
                "temperature": 0.75,
                "top_p": 0.9,
                "repeat_penalty": 1.1,
                "num_ctx": 4096,
                "num_predict": 180,
                "seed_base": SEED_BASE,
                "native_chat_template": True,
                "thinking": False,
                "num_gpu": 999,
            },
            "gpt_generation": provenance,
            "limitations": [
                "GPT surface is 60 fresh isolated Codex child-agent calls, not an OpenAI API backend.",
                "GPT API latency, token usage, cost, provider metadata, and seed are unavailable.",
                "Codex host instructions cannot be made byte-identical to the local model runtime.",
                "After turn 1 each arm has different self-generated history by design.",
                "This single trajectory is not an estimate of general online error rate.",
            ],
        },
        "arms": {"qwen35": local_arm, "gpt": gpt_arm},
        "turns": [],
    }
    for input_turn, local_turn, gpt_turn in zip(
        inputs, local_arm["turns"], gpt_arm["turns"], strict=True
    ):
        source = simulation.TURNS[int(input_turn["ordinal"]) - 1]
        report["turns"].append(
            {
                "turn": input_turn["ordinal"],
                "phase": input_turn["phase"],
                "player": input_turn["player"],
                "authoritative_world": source["world"],
                "expectation": source["expectation"],
                "qwen35": local_turn,
                "gpt": gpt_turn,
            }
        )
    report["summary"] = {
        "turns": 60,
        "qwen35_completed": len(local_arm["turns"]),
        "gpt_completed": len(gpt_arm["turns"]),
        "qwen35_mean_elapsed_ms": _mean_elapsed(local_arm["turns"]),
        "gpt_mean_elapsed_ms": None,
        "automatic_quality_judgment": False,
    }
    report_path = run_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"REPORT={report_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Qwen3.5-9B Q6 vs internal GPT 60-turn test"
    )
    parser.add_argument("run_dir")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

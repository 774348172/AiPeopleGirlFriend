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
FROZEN_INPUTS = (
    ROOT
    / "eval"
    / "world_mind_p0"
    / "qwen35_27b_gpt56_60turn_20260824-114447"
    / "shared_inputs.jsonl"
)
EXPECTED_INPUT_SHA256 = "0593a2606b470b7dd4e0ab34cf59ac96095d996fb2022bb8f8ba4ac2aa2e6720"
HISTORY_LIMIT_MESSAGES = 6
SEED_BASE = 2026082200

if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import run_baiweixi_60turn_player_simulation as simulation
import run_baiweixi_paired_raw_context_ab as paired


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _validate_inputs(values: list[dict[str, Any]]) -> None:
    if len(values) != 60:
        raise ValueError(f"frozen input count must be 60, got {len(values)}")
    for ordinal, value in enumerate(values, 1):
        if value.get("ordinal") != ordinal:
            raise ValueError(f"input ordinal mismatch at {ordinal}")
        for key in ("phase", "system", "player"):
            if not isinstance(value.get(key), str) or not value[key].strip():
                raise ValueError(f"input {ordinal} has invalid {key}")
        if {"expectation", "required_groups", "forbidden_facts"}.intersection(value):
            raise ValueError(f"input {ordinal} leaks oracle fields")


def _messages(turn: dict[str, Any], history: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": turn["system"]},
        *history[-HISTORY_LIMIT_MESSAGES:],
        {"role": "user", "content": turn["player"]},
    ]


async def _generate(
    client: httpx.AsyncClient,
    model_name: str,
    messages: list[dict[str, str]],
    seed: int,
    request_id: str,
    thinking: bool,
    num_ctx: int,
    num_predict: int,
    temperature: float = 0.75,
    top_p: float = 0.9,
    repeat_penalty: float = 1.1,
) -> dict[str, Any]:
    body = {
        "model": model_name,
        "messages": messages,
        "stream": True,
        "think": thinking,
        "keep_alive": "10m",
        "options": {
            "num_ctx": num_ctx,
            "num_predict": num_predict,
            "temperature": temperature,
            "top_p": top_p,
            "repeat_penalty": repeat_penalty,
            "seed": seed,
            "num_gpu": 999,
        },
    }
    started = time.perf_counter()
    first_output_ms: float | None = None
    first_thinking_ms: float | None = None
    first_visible_ms: float | None = None
    response_parts: list[str] = []
    thinking_parts: list[str] = []
    payload: dict[str, Any] = {}
    async with client.stream(
        "POST", "/api/chat", json=body, headers={"X-Request-ID": request_id}
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.strip():
                continue
            chunk = json.loads(line)
            payload = chunk
            message = chunk.get("message")
            if not isinstance(message, dict):
                continue
            thinking_piece = message.get("thinking")
            response_piece = message.get("content")
            now_ms = (time.perf_counter() - started) * 1000
            if isinstance(thinking_piece, str) and thinking_piece:
                if first_output_ms is None:
                    first_output_ms = now_ms
                if first_thinking_ms is None and thinking_piece.strip():
                    first_thinking_ms = now_ms
                thinking_parts.append(thinking_piece)
            if isinstance(response_piece, str) and response_piece:
                if first_output_ms is None:
                    first_output_ms = now_ms
                if first_visible_ms is None and response_piece.strip():
                    first_visible_ms = now_ms
                response_parts.append(response_piece)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    reply = "".join(response_parts)
    thinking_text = "".join(thinking_parts)
    done_reason = payload.get("done_reason")
    empty_after_thinking = (
        (not isinstance(reply, str) or not reply.strip())
        and isinstance(thinking_text, str)
        and bool(thinking_text.strip())
    )
    if empty_after_thinking:
        if done_reason == "length":
            generation_error = "thinking_output_budget_exhausted"
            reply = "[未生成最终回复：思考达到输出上限]"
        else:
            generation_error = "thinking_ended_without_final_response"
            reply = "[未生成最终回复：思考结束后答案为空]"
    elif not isinstance(reply, str) or not reply.strip():
        raise RuntimeError(f"empty response for {request_id}")
    else:
        generation_error = None
    return {
        "response": reply.strip(),
        "thinking": thinking_text or None,
        "include_response_in_history": not empty_after_thinking,
        "generation_error": generation_error,
        "elapsed_ms": elapsed_ms,
        "first_output_ms": round(first_output_ms, 2) if first_output_ms is not None else None,
        "first_thinking_ms": round(first_thinking_ms, 2) if first_thinking_ms is not None else None,
        "first_visible_ms": round(first_visible_ms, 2) if first_visible_ms is not None else None,
        "thinking_chars": len(thinking_text),
        "response_chars": len(reply.strip()),
        "load_duration_ns": payload.get("load_duration"),
        "prompt_eval_count": payload.get("prompt_eval_count"),
        "prompt_eval_duration_ns": payload.get("prompt_eval_duration"),
        "eval_count": payload.get("eval_count"),
        "eval_duration_ns": payload.get("eval_duration"),
        "eval_tokens_per_second": round(
            float(payload["eval_count"]) * 1_000_000_000 / float(payload["eval_duration"]), 2
        )
        if payload.get("eval_count") is not None and payload.get("eval_duration")
        else None,
        "done_reason": done_reason,
    }


async def _unload(client: httpx.AsyncClient, model_name: str) -> None:
    await client.post(
        "/api/generate",
        json={"model": model_name, "prompt": "", "stream": False, "keep_alive": 0},
    )


async def _run_arm(
    client: httpx.AsyncClient,
    key: str,
    label: str,
    model_name: str,
    model_digest: str,
    inputs: list[dict[str, Any]],
    output_dir: Path,
    limit: int,
    thinking: bool,
    num_ctx: int,
    num_predict: int,
    temperature: float = 0.75,
    top_p: float = 0.9,
    repeat_penalty: float = 1.1,
) -> dict[str, Any]:
    partial_path = output_dir / f"{key}_responses.partial.jsonl"
    turns = _load_jsonl(partial_path) if partial_path.is_file() else []
    history: list[dict[str, str]] = []
    for source, completed in zip(inputs, turns):
        if completed.get("ordinal") != source["ordinal"] or completed.get("model") != model_name:
            raise ValueError(f"invalid partial result in {partial_path}")
        history.append({"role": "user", "content": source["player"]})
        if completed.get("include_response_in_history", True):
            history.append({"role": "assistant", "content": completed["response"]})

    for turn in inputs[:limit][len(turns):]:
        ordinal = int(turn["ordinal"])
        messages = _messages(turn, history)
        prompt = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
        seed = SEED_BASE + ordinal
        generated = await _generate(
            client,
            model_name,
            messages,
            seed,
            f"low-vram-lora-60:{key}:turn-{ordinal:02d}",
            thinking,
            num_ctx,
            num_predict,
            temperature,
            top_p,
            repeat_penalty,
        )
        record = {
            "arm": key,
            "model": model_name,
            "ordinal": ordinal,
            "messages": messages,
            "prompt_sha256": paired._sha256_text(prompt),
            "seed": seed,
            **generated,
        }
        turns.append(record)
        history.append({"role": "user", "content": turn["player"]})
        if generated["include_response_in_history"]:
            history.append({"role": "assistant", "content": generated["response"]})
        _write_jsonl(partial_path, turns)
        print(
            json.dumps(
                {"arm": key, "turn": ordinal, "elapsed_ms": generated["elapsed_ms"], "response": generated["response"]},
                ensure_ascii=False,
            ),
            flush=True,
        )
    await _unload(client, model_name)
    return {
        "key": key,
        "label": label,
        "model": model_name,
        "ollama_digest": model_digest,
        "surface": f"Ollama GGUF native chat template, thinking {'enabled' if thinking else 'disabled'}",
        "turns": turns,
    }


def _mean_elapsed(turns: list[dict[str, Any]]) -> float:
    return round(statistics.fmean(float(turn["elapsed_ms"]) for turn in turns), 2)


def _numeric_values(turns: list[dict[str, Any]], key: str) -> list[float]:
    return [float(turn[key]) for turn in turns if turn.get(key) is not None]


def _mean_metric(turns: list[dict[str, Any]], key: str) -> float | None:
    values = _numeric_values(turns, key)
    return round(statistics.fmean(values), 2) if values else None


def _median_metric(turns: list[dict[str, Any]], key: str) -> float | None:
    values = _numeric_values(turns, key)
    return round(statistics.median(values), 2) if values else None


def _percentile_metric(turns: list[dict[str, Any]], key: str, percentile: float) -> float | None:
    values = sorted(_numeric_values(turns, key))
    if not values:
        return None
    position = (len(values) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    value = values[lower] + (values[upper] - values[lower]) * (position - lower)
    return round(value, 2)


def _speed_summary(turns: list[dict[str, Any]]) -> dict[str, Any]:
    warm_turns = turns[1:]
    total_eval_count = sum(int(turn.get("eval_count") or 0) for turn in turns)
    total_eval_duration_ns = sum(int(turn.get("eval_duration_ns") or 0) for turn in turns)
    return {
        "turns": len(turns),
        "thinking_turns": sum(bool(turn.get("thinking")) for turn in turns),
        "generation_failures": sum(bool(turn.get("generation_error")) for turn in turns),
        "mean_total_elapsed_ms": _mean_metric(turns, "elapsed_ms"),
        "median_total_elapsed_ms": _median_metric(turns, "elapsed_ms"),
        "warm_mean_total_elapsed_ms": _mean_metric(warm_turns, "elapsed_ms"),
        "mean_first_output_ms": _mean_metric(turns, "first_output_ms"),
        "warm_mean_first_output_ms": _mean_metric(warm_turns, "first_output_ms"),
        "mean_first_visible_ms": _mean_metric(turns, "first_visible_ms"),
        "median_first_visible_ms": _median_metric(turns, "first_visible_ms"),
        "p90_first_visible_ms": _percentile_metric(turns, "first_visible_ms", 0.9),
        "warm_mean_first_visible_ms": _mean_metric(warm_turns, "first_visible_ms"),
        "aggregate_eval_tokens_per_second": round(
            total_eval_count * 1_000_000_000 / total_eval_duration_ns, 2
        )
        if total_eval_duration_ns
        else None,
        "mean_thinking_chars": _mean_metric(turns, "thinking_chars"),
        "mean_response_chars": _mean_metric(turns, "response_chars"),
    }


async def _run(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if _sha256(FROZEN_INPUTS) != EXPECTED_INPUT_SHA256:
        raise ValueError("frozen 60-turn input SHA256 mismatch")
    inputs = _load_jsonl(FROZEN_INPUTS)
    _validate_inputs(inputs)
    _write_jsonl(output_dir / "shared_inputs.jsonl", inputs)

    async with httpx.AsyncClient(
        base_url=args.ollama_url.rstrip("/"), timeout=httpx.Timeout(300.0)
    ) as client:
        digests = await paired._model_digests(client)
        for model_name in (args.model_a, args.model_b):
            if model_name not in digests:
                raise RuntimeError(f"Ollama model is not installed: {model_name}")
        for model_name in (args.model_a, args.model_b):
            await _unload(client, model_name)
        arm_a = await _run_arm(
            client, "qwen35_9b", "Qwen3.5-9B Q6_K + 白未晞 LoRA", args.model_a,
            digests[args.model_a], inputs, output_dir, args.limit, args.thinking, args.num_ctx, args.num_predict,
        )
        arm_b = await _run_arm(
            client, "qwen3_14b", "Qwen3-14B Q4_K_M + 白未晞 LoRA thinking-v2", args.model_b,
            digests[args.model_b], inputs, output_dir, args.limit, args.thinking, args.num_ctx, args.num_predict,
        )

    if args.limit != 60:
        print(json.dumps({"status": "probe_completed", "turns_per_arm": args.limit}, ensure_ascii=False))
        return 0

    report_turns = []
    for input_turn, source, turn_a, turn_b in zip(
        inputs, simulation.TURNS, arm_a["turns"], arm_b["turns"], strict=True
    ):
        report_turns.append(
            {
                "turn": input_turn["ordinal"],
                "phase": input_turn["phase"],
                "player": input_turn["player"],
                "authoritative_world": source["world"],
                "expectation": source["expectation"],
                "qwen35_9b": turn_a,
                "qwen3_14b": turn_b,
            }
        )
    report = {
        "schema_version": 2,
        "scope": (
            "low_vram_baiweixi_lora_quantized_stateful_thinking_60turn"
            if args.thinking
            else "low_vram_baiweixi_lora_quantized_stateful_60turn"
        ),
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": "completed",
        "controls": {
            "turn_count": 60,
            "history_limit_messages": HISTORY_LIMIT_MESSAGES,
            "same_frozen_oracle_free_inputs": True,
            "input_sha256": EXPECTED_INPUT_SHA256,
            "each_arm_retains_own_history": True,
            "arm_order": ["qwen35_9b", "qwen3_14b"],
            "generation": {
                "temperature": 0.75,
                "top_p": 0.9,
                "repeat_penalty": 1.1,
                "num_ctx": args.num_ctx,
                "num_predict": args.num_predict,
                "output_budget_note": args.output_budget_note,
                "seed_base": SEED_BASE,
                "thinking": args.thinking,
                "num_gpu": 999,
            },
            "limitations": [
                "The model families and deployment quantizations differ; this is a product-candidate comparison, not a LoRA causal A/B test.",
                "After turn 1 each arm has different self-generated history by design.",
                "This single trajectory is for paired human review, not a general online error-rate estimate.",
                "Arms run sequentially in fixed order on the same GPU; warm metrics exclude each arm's cold-load turn but do not eliminate all order effects.",
            ],
            "latency_definitions": {
                "first_output_ms": "request start to first thinking or visible content token",
                "first_visible_ms": "request start to first non-whitespace player-visible answer token after thinking",
                "elapsed_ms": "request start to completed response",
                "eval_tokens_per_second": "Ollama eval_count divided by eval_duration; includes thinking and final-answer tokens",
                "warm_metrics": "turns 2-60; excludes each arm's cold model-load turn",
            },
        },
        "arms": {"qwen35_9b": arm_a, "qwen3_14b": arm_b},
        "turns": report_turns,
        "summary": {
            "turns": 60,
            "qwen35_9b_completed": len(arm_a["turns"]),
            "qwen3_14b_completed": len(arm_b["turns"]),
            "qwen35_9b_mean_elapsed_ms": _mean_elapsed(arm_a["turns"]),
            "qwen3_14b_mean_elapsed_ms": _mean_elapsed(arm_b["turns"]),
            "qwen35_9b_generation_failures": sum(
                bool(turn.get("generation_error")) for turn in arm_a["turns"]
            ),
            "qwen3_14b_generation_failures": sum(
                bool(turn.get("generation_error")) for turn in arm_b["turns"]
            ),
            "speed": {
                "qwen35_9b": _speed_summary(arm_a["turns"]),
                "qwen3_14b": _speed_summary(arm_b["turns"]),
            },
            "automatic_quality_judgment": False,
        },
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": report["generated_at"],
                "report_sha256": _sha256(report_path),
                "input_sha256": EXPECTED_INPUT_SHA256,
                "model_digests": {
                    args.model_a: arm_a["ollama_digest"],
                    args.model_b: arm_b["ollama_digest"],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"REPORT={report_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run two low-VRAM quantized Baiweixi LoRA models for 60 turns")
    parser.add_argument("output_dir")
    parser.add_argument("--model-a", default="baiweixi-qwen35-9b-q6:latest")
    parser.add_argument("--model-b", default="baiweixi-qwen3-14b-thinking-v2:q4km")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--thinking", action="store_true")
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--num-predict", type=int, default=180)
    parser.add_argument("--output-budget-note")
    args = parser.parse_args()
    if not 1 <= args.limit <= 60:
        raise ValueError("--limit must be between 1 and 60")
    if args.num_predict < 1:
        raise ValueError("--num-predict must be positive")
    if args.num_ctx < 1:
        raise ValueError("--num-ctx must be positive")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

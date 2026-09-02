from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any

from unsloth import FastModel
import torch
from transformers.generation.streamers import BaseStreamer


ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b"
ADAPTER_PATH = PACKAGE_ROOT / "outputs" / "baiweixi_gemma4_12b_unsloth"
FROZEN_INPUTS = (
    ROOT
    / "eval"
    / "world_mind_p0"
    / "qwen35_27b_gpt56_60turn_20260824-114447"
    / "shared_inputs.jsonl"
)
EXPECTED_INPUT_SHA256 = "0593a2606b470b7dd4e0ab34cf59ac96095d996fb2022bb8f8ba4ac2aa2e6720"
EXPECTED_ADAPTER_SHA256 = "7f0e02c70bc890ac7239e0a87ea14fdcae49171c7009aae7577f1eb34a323931"
HISTORY_LIMIT_MESSAGES = 6
SEED_BASE = 2026082200


class TokenTimingStreamer(BaseStreamer):
    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.prompt_seen = False
        self.token_ids: list[int] = []
        self.token_times_ms: list[float] = []

    def put(self, value: torch.Tensor) -> None:
        if not self.prompt_seen:
            self.prompt_seen = True
            return
        values = value.detach().reshape(-1).tolist()
        now_ms = (time.perf_counter() - self.started) * 1000
        self.token_ids.extend(int(item) for item in values)
        self.token_times_ms.extend(now_ms for _item in values)

    def end(self) -> None:
        return None

if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import run_baiweixi_60turn_player_simulation as simulation


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _messages(turn: dict[str, Any], history: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": turn["system"]},
        *history[-HISTORY_LIMIT_MESSAGES:],
        {"role": "user", "content": turn["player"]},
    ]


def _generate(
    model: Any,
    tokenizer: Any,
    messages: list[dict[str, str]],
    *,
    seed: int,
    adapter_enabled: bool,
    max_new_tokens: int,
    temperature: float,
    top_p: float,
    repetition_penalty: float,
) -> dict[str, Any]:
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
    streamer = TokenTimingStreamer()
    started = streamer.started
    context = nullcontext() if adapter_enabled else model.disable_adapter()
    with context, torch.inference_mode():
        output = model.generate(
            **encoded,
            streamer=streamer,
            max_new_tokens=max_new_tokens,
            do_sample=True,
            temperature=temperature,
            top_p=top_p,
            repetition_penalty=repetition_penalty,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=[tokenizer.eos_token_id, tokenizer.eot_token_id],
            use_cache=True,
        )
    elapsed_ms = (time.perf_counter() - started) * 1000
    prompt_tokens = int(encoded["input_ids"].shape[-1])
    generated_ids = output[0, prompt_tokens:]
    eval_count = int(generated_ids.shape[-1])
    parsed = tokenizer.parse_response(generated_ids)
    reply = str(parsed.get("content") or "").strip()
    thinking = str(parsed.get("thinking") or "").strip()
    if not reply:
        reply = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
    if not reply:
        raise RuntimeError("model returned an empty response")
    raw = tokenizer.decode(generated_ids, skip_special_tokens=False)
    content_offset = raw.rfind(reply)
    first_visible_ms: float | None = None
    if content_offset >= 0:
        for index, token_time in enumerate(streamer.token_times_ms, 1):
            if len(tokenizer.decode(streamer.token_ids[:index], skip_special_tokens=False)) > content_offset:
                first_visible_ms = token_time
                break
    if first_visible_ms is None and streamer.token_times_ms:
        first_visible_ms = streamer.token_times_ms[0]
    generation_ms = max(elapsed_ms - (first_visible_ms or elapsed_ms), 0.0)
    return {
        "response": reply,
        "thinking": thinking or None,
        "include_response_in_history": True,
        "generation_error": None,
        "elapsed_ms": round(elapsed_ms, 2),
        "first_output_ms": round(first_visible_ms, 2) if first_visible_ms is not None else None,
        "first_visible_ms": round(first_visible_ms, 2) if first_visible_ms is not None else None,
        "thinking_chars": len(thinking),
        "response_chars": len(reply),
        "prompt_eval_count": prompt_tokens,
        "eval_count": eval_count,
        "eval_tokens_per_second": round(eval_count * 1000 / generation_ms, 2) if generation_ms else None,
        "done_reason": "stop" if eval_count < max_new_tokens else "length",
    }


def _run_arm(
    model: Any,
    tokenizer: Any,
    *,
    key: str,
    label: str,
    inputs: list[dict[str, Any]],
    output_dir: Path,
    limit: int,
    adapter_enabled: bool,
    max_new_tokens: int,
    temperature: float,
    top_p: float,
    repetition_penalty: float,
) -> dict[str, Any]:
    partial_path = output_dir / f"{key}_responses.partial.jsonl"
    turns = _load_jsonl(partial_path) if partial_path.is_file() else []
    history: list[dict[str, str]] = []
    for source, completed in zip(inputs, turns):
        if completed.get("ordinal") != source["ordinal"] or completed.get("arm") != key:
            raise ValueError(f"invalid partial result in {partial_path}")
        history.append({"role": "user", "content": source["player"]})
        history.append({"role": "assistant", "content": completed["response"]})

    for turn in inputs[:limit][len(turns):]:
        ordinal = int(turn["ordinal"])
        messages = _messages(turn, history)
        prompt = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
        generated = _generate(
            model,
            tokenizer,
            messages,
            seed=SEED_BASE + ordinal,
            adapter_enabled=adapter_enabled,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_p=top_p,
            repetition_penalty=repetition_penalty,
        )
        record = {
            "arm": key,
            "model": "google/gemma-4-12B-it",
            "adapter_enabled": adapter_enabled,
            "ordinal": ordinal,
            "messages": messages,
            "prompt_sha256": _sha256_text(prompt),
            "seed": SEED_BASE + ordinal,
            **generated,
        }
        turns.append(record)
        history.extend(
            [
                {"role": "user", "content": turn["player"]},
                {"role": "assistant", "content": generated["response"]},
            ]
        )
        _write_jsonl(partial_path, turns)
        print(
            json.dumps(
                {
                    "arm": key,
                    "turn": ordinal,
                    "elapsed_ms": generated["elapsed_ms"],
                    "response": generated["response"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return {
        "key": key,
        "label": label,
        "model": "google/gemma-4-12B-it",
        "adapter_enabled": adapter_enabled,
        "adapter_sha256": EXPECTED_ADAPTER_SHA256 if adapter_enabled else None,
        "surface": "Transformers 5.5 + Unsloth bitsandbytes NF4, Gemma 4 normal chat template",
        "turns": turns,
    }


def _metric(turns: list[dict[str, Any]], key: str, mode: str = "mean") -> float | None:
    values = [float(turn[key]) for turn in turns if turn.get(key) is not None]
    if not values:
        return None
    value = statistics.fmean(values) if mode == "mean" else statistics.median(values)
    return round(value, 2)


def _percentile(turns: list[dict[str, Any]], key: str, percentile: float) -> float | None:
    values = sorted(float(turn[key]) for turn in turns if turn.get(key) is not None)
    if not values:
        return None
    position = (len(values) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return round(values[lower] + (values[upper] - values[lower]) * (position - lower), 2)


def _speed_summary(turns: list[dict[str, Any]]) -> dict[str, Any]:
    warm = turns[1:]
    return {
        "turns": len(turns),
        "generation_failures": sum(bool(turn.get("generation_error")) for turn in turns),
        "mean_total_elapsed_ms": _metric(turns, "elapsed_ms"),
        "median_total_elapsed_ms": _metric(turns, "elapsed_ms", "median"),
        "warm_mean_total_elapsed_ms": _metric(warm, "elapsed_ms"),
        "mean_first_output_ms": _metric(turns, "first_output_ms"),
        "warm_mean_first_output_ms": _metric(warm, "first_output_ms"),
        "mean_first_visible_ms": _metric(turns, "first_visible_ms"),
        "median_first_visible_ms": _metric(turns, "first_visible_ms", "median"),
        "p90_first_visible_ms": _percentile(turns, "first_visible_ms", 0.9),
        "warm_mean_first_visible_ms": _metric(warm, "first_visible_ms"),
        "mean_eval_tokens_per_second": _metric(turns, "eval_tokens_per_second"),
        "aggregate_eval_tokens_per_second": _metric(turns, "eval_tokens_per_second"),
        "mean_thinking_chars": _metric(turns, "thinking_chars"),
        "mean_response_chars": _metric(turns, "response_chars"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Gemma 4 12B base versus Baiweixi QLoRA 60-turn test")
    parser.add_argument("output_dir")
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.75)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--repetition-penalty", type=float, default=1.1)
    args = parser.parse_args()
    if not 1 <= args.limit <= 60:
        raise ValueError("--limit must be between 1 and 60")
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    adapter_file = ADAPTER_PATH / "adapter_model.safetensors"
    if _sha256(FROZEN_INPUTS) != EXPECTED_INPUT_SHA256:
        raise ValueError("frozen 60-turn input SHA256 mismatch")
    if _sha256(adapter_file) != EXPECTED_ADAPTER_SHA256:
        raise ValueError("trained Adapter SHA256 mismatch")
    inputs = _load_jsonl(FROZEN_INPUTS)
    if len(inputs) != 60 or [item["ordinal"] for item in inputs] != list(range(1, 61)):
        raise ValueError("frozen input structure mismatch")
    _write_jsonl(output_dir / "shared_inputs.jsonl", inputs)

    from unsloth.chat_templates import get_chat_template

    model, tokenizer = FastModel.from_pretrained(
        model_name=str(ADAPTER_PATH),
        max_seq_length=4096,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
    )
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    FastModel.for_inference(model)
    base = _run_arm(
        model,
        tokenizer,
        key="base",
        label="Gemma 4 12B 官方基座 NF4",
        inputs=inputs,
        output_dir=output_dir,
        limit=args.limit,
        adapter_enabled=False,
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,
        top_p=args.top_p,
        repetition_penalty=args.repetition_penalty,
    )
    qlora = _run_arm(
        model,
        tokenizer,
        key="qlora",
        label="Gemma 4 12B NF4 + 白未晞 QLoRA",
        inputs=inputs,
        output_dir=output_dir,
        limit=args.limit,
        adapter_enabled=True,
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,
        top_p=args.top_p,
        repetition_penalty=args.repetition_penalty,
    )
    if args.limit != 60:
        probe = {"status": "probe_completed", "turns_per_arm": args.limit, "base": base, "qlora": qlora}
        (output_dir / "probe.json").write_text(json.dumps(probe, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"PROBE={output_dir / 'probe.json'}")
        return 0

    report_turns = []
    for frozen, source, turn_a, turn_b in zip(
        inputs, simulation.TURNS, base["turns"], qlora["turns"], strict=True
    ):
        report_turns.append(
            {
                "turn": frozen["ordinal"],
                "phase": frozen["phase"],
                "player": frozen["player"],
                "authoritative_world": source["world"],
                "expectation": source["expectation"],
                "base": turn_a,
                "qlora": turn_b,
            }
        )
    generated_at = datetime.now().astimezone().isoformat()
    report = {
        "schema_version": 1,
        "scope": "gemma4_12b_base_vs_baiweixi_qlora_stateful_60turn",
        "generated_at": generated_at,
        "status": "completed",
        "controls": {
            "turn_count": 60,
            "history_limit_messages": HISTORY_LIMIT_MESSAGES,
            "same_frozen_oracle_free_inputs": True,
            "input_sha256": EXPECTED_INPUT_SHA256,
            "same_base_checkpoint": True,
            "same_nf4_runtime": True,
            "each_arm_retains_own_history": True,
            "arm_order": ["base", "qlora"],
            "only_intentional_difference": "PEFT Adapter disabled versus enabled",
            "generation": {
                "temperature": args.temperature,
                "top_p": args.top_p,
                "repetition_penalty": args.repetition_penalty,
                "max_new_tokens": args.max_new_tokens,
                "seed_base": SEED_BASE,
                "thinking": False,
            },
            "adapter_sha256": EXPECTED_ADAPTER_SHA256,
            "limitations": [
                "Each arm keeps its own generated history after turn 1, matching an actual multi-turn trajectory.",
                "Arms run sequentially in fixed order; this does not eliminate every order effect.",
                "This single trajectory supports paired human review and is not a population-level estimate.",
                "NF4 causal A/B is the training-effect test; the prior Ollama Q4 base result remains a separate deployment baseline.",
            ],
        },
        "arms": {"base": base, "qlora": qlora},
        "turns": report_turns,
        "summary": {
            "turns": 60,
            "base_completed": len(base["turns"]),
            "qlora_completed": len(qlora["turns"]),
            "base_generation_failures": 0,
            "qlora_generation_failures": 0,
            "speed": {"base": _speed_summary(base["turns"]), "qlora": _speed_summary(qlora["turns"])},
            "automatic_quality_judgment": False,
        },
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": generated_at,
                "report_sha256": _sha256(report_path),
                "input_sha256": EXPECTED_INPUT_SHA256,
                "adapter_sha256": EXPECTED_ADAPTER_SHA256,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"REPORT={report_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

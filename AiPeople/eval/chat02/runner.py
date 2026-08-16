from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import re
import socket
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator, FormatChecker

from eval.chat01.freeze import sha256_file, verify_file
from eval.chat01.rules import evaluate_case_output
from eval.chat01h.freeze import verify_manifest_v2
from runtime._context import ContextMessage, epoch_reply_context
from runtime._model import ReplyRequest
from runtime.adapters.llama_cpp import (
    GenerationOptions,
    LlamaCppConfig,
    LlamaCppReplyModel,
)


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = Path(__file__).resolve()
PROFILE_PATH = ROOT / "eval/chat02/execution_profile_v1.json"
PROFILE_SCHEMA_PATH = ROOT / "eval/chat02/schema/execution_profile.schema.json"
MODEL_SCHEMA_PATH = ROOT / "eval/chat02/schema/model_manifest.schema.json"
AUTO_RESULT_SCHEMA_PATH = ROOT / "eval/chat01/schema/auto_result.schema.json"
DEV_PATH = ROOT / "eval/chat01/suites/chat01_dev_v1.jsonl"
SINGLE_PATH = ROOT / "eval/chat01/suites/chat01_frozen_single_v1.jsonl"
MULTITURN_PATH = ROOT / "eval/chat01/suites/chat01_frozen_multiturn_v1.jsonl"
HUMAN_PATH = ROOT / "eval/chat01/suites/chat01_human_blind_v1.jsonl"
REPORTS_ROOT = ROOT / "eval/chat02/reports"
MODEL_PATHS = {
    "qwen3-4b-base-q4_k_m": ROOT / "eval/chat02/models/qwen3-4b-base-q4_k_m.json",
    "qinweixi-current-q4_k_m": ROOT / "eval/chat02/models/qinweixi-current-q4_k_m.json",
}
STABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")


class EvaluationProvider(Protocol):
    model_id: str

    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def generate(
        self,
        *,
        case_id: str,
        attempt_id: str,
        user_text: str,
        history: tuple[ContextMessage, ...],
        options: GenerationOptions,
    ) -> "ProviderResult": ...


@dataclass(frozen=True)
class ProviderResult:
    output: Any
    first_token_ms: float | None
    total_ms: float | None
    prompt_tokens: int | None
    output_tokens: int | None


class ControlledProvider:
    model_id = "chat02-controlled-provider-v1"

    def __init__(self, behaviors: dict[str, str] | None = None) -> None:
        self.behaviors = behaviors or {}
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def generate(
        self, *, case_id: str, attempt_id: str, user_text: str,
        history: tuple[ContextMessage, ...], options: GenerationOptions,
    ) -> ProviderResult:
        behavior = self.behaviors.get(attempt_id, "normal")
        if behavior == "timeout":
            raise TimeoutError("controlled timeout")
        if behavior == "crash":
            raise RuntimeError("controlled provider crash")
        if behavior == "invalid":
            return ProviderResult({"not": "text"}, 1.0, 2.0, 10, 1)
        if behavior == "empty":
            return ProviderResult("", None, 2.0, 10, 0)
        return ProviderResult("秦未晞。", 1.0, 2.0, 10, 4)


class RealLlamaCppProvider:
    def __init__(self, model_manifest: dict[str, Any], profile: dict[str, Any], bridge_path: Path) -> None:
        self.model_id = model_manifest["model_id"]
        launch = profile["launch"]
        lifecycle = profile["lifecycle"]
        backend = profile["backend"]
        asset = model_manifest["asset"]
        bridge = {
            "llama_cpp": {
                "server_path": backend["server"]["path"],
                "server_sha256": backend["server"]["sha256"],
            },
            "model": {"path": asset["path"], "bytes": asset["bytes"], "sha256": asset["sha256"]},
            "launch": launch,
            "verification": {"model_alias": self.model_id},
        }
        _write_json(bridge_path, bridge)
        config = LlamaCppConfig(
            server_executable=Path(backend["server"]["path"]),
            model_path=Path(asset["path"]),
            manifest_path=bridge_path.resolve(),
            host=launch["host"], port=launch["port"], model_alias=self.model_id,
            context_size=launch["context_size"], parallel=launch["parallel"],
            gpu_layers=launch["gpu_layers"], flash_attention=launch["flash_attention"],
            kv_cache_k=launch["kv_cache_k"], kv_cache_v=launch["kv_cache_v"],
            startup_timeout_seconds=lifecycle["startup_timeout_seconds"],
            connect_timeout_seconds=lifecycle["connect_timeout_seconds"],
            read_idle_timeout_seconds=lifecycle["read_idle_timeout_seconds"],
            generation_timeout_seconds=lifecycle["generation_timeout_seconds"],
            shutdown_timeout_seconds=lifecycle["shutdown_timeout_seconds"],
            collect_usage=True,
        )
        self._model = LlamaCppReplyModel(config)

    async def start(self) -> None:
        await self._model.start()

    async def close(self) -> None:
        await self._model.close()

    async def generate(
        self, *, case_id: str, attempt_id: str, user_text: str,
        history: tuple[ContextMessage, ...], options: GenerationOptions,
    ) -> ProviderResult:
        now = datetime.now(timezone.utc)
        user_event_id = f"{attempt_id}.user"
        context = epoch_reply_context(
            epoch_id=case_id, history_messages=history,
            user_event_id=user_event_id, text=user_text, occurred_at=now,
            timezone="Asia/Shanghai", context_size=self._model.context_size,
            reply_reserve_tokens=max(256, options.max_tokens), safety_margin_tokens=256,
            dynamic_context_enabled=False,
        )
        request = ReplyRequest(attempt_id, case_id, user_event_id, user_text, context)
        started = time.perf_counter()
        chunks: list[str] = []
        async for chunk in self._model.stream_reply_with_options(request, options):
            chunks.append(chunk)
        elapsed = (time.perf_counter() - started) * 1000
        stats = self._model.last_generation_stats
        return ProviderResult(
            "".join(chunks),
            stats.first_token_seconds * 1000 if stats and stats.first_token_seconds is not None else None,
            stats.elapsed_seconds * 1000 if stats else elapsed,
            stats.prompt_tokens if stats else None,
            stats.generated_tokens if stats else None,
        )


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(v, ensure_ascii=False, separators=(",", ":")) + "\n" for v in values), encoding="utf-8", newline="\n")


def _validate(value: dict[str, Any], schema_path: Path) -> None:
    schema = _load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def verify_contract(model_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    suite = verify_manifest_v2()
    profile = _load_json(PROFILE_PATH)
    _validate(profile, PROFILE_SCHEMA_PATH)
    if profile["readiness"]["next_real_model_run"] != "ready":
        raise ValueError("shared profile does not permit real runs")
    suite_contract = profile["suite_contract"]
    if suite_contract["suite_id"] != suite["suite_id"]:
        raise ValueError("profile suite ID does not match CHAT-01H")
    if sha256_file(Path(suite_contract["manifest_path"])) != suite_contract["manifest_file_sha256"]:
        raise ValueError("suite manifest file hash changed")
    if suite["freeze"]["manifest_sha256"] != suite_contract["manifest_self_sha256"]:
        raise ValueError("suite manifest self hash changed")
    for key in ("server", "runtime_adapter"):
        verify_file(ROOT, profile["backend"][key])
    verify_file(ROOT, profile["request_contract"]["prompt_renderer"])
    manifest_path = MODEL_PATHS[model_id]
    model_manifest = _load_json(manifest_path)
    _validate(model_manifest, MODEL_SCHEMA_PATH)
    verify_file(ROOT, model_manifest["asset"])
    return suite, profile, model_manifest


def _lane_options(case: dict[str, Any], profile: dict[str, Any], seed: int) -> GenerationOptions:
    lane = profile["lanes"][case["generation"]["lane"]]
    return GenerationOptions(
        max_tokens=case["generation"]["max_new_tokens"], seed=seed,
        temperature=lane["temperature"], top_p=lane["top_p"],
        repeat_penalty=lane["repeat_penalty"],
    )


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc).astimezone()).isoformat(timespec="milliseconds")


def _error_result(run_id: str, suite_id: str, model_id: str, case_id: str, attempt_id: str,
                  seed: int, started_at: str, error_code: str, status: str = "error") -> dict[str, Any]:
    return {
        "schema_version": 1, "run_id": run_id, "suite_id": suite_id,
        "model_id": model_id, "case_id": case_id, "attempt_id": attempt_id,
        "status": status, "output": None, "error_code": error_code,
        "checks": [], "blocker_flags": [],
        "metrics": {"first_token_ms": None, "total_ms": None, "prompt_tokens": None, "output_tokens": None, "seed": seed},
        "started_at": started_at, "completed_at": _iso(),
    }


def _evaluate_result(*, run_id: str, suite_id: str, model_id: str, case: dict[str, Any],
                     attempt_id: str, output: str, result: ProviderResult, seed: int,
                     started_at: str, turn_id: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    evaluation_case = case
    if case["case_type"] == "human_pair":
        evaluation_case = {
            **case,
            "case_type": "single_turn",
            "oracle": {
                "known_facts": [],
                "forbidden_claims": [],
                "required_behaviors": [],
                "allowed_variation": "Human blind review decides semantic quality.",
            },
        }
    evaluation = evaluate_case_output(
        evaluation_case, output, attempt_id=attempt_id, turn_id=turn_id
    )
    status = evaluation["status"]
    error_code = None
    if status == "pending_review":
        status, error_code = "invalid", "semantic_review_pending"
    item = {
        "schema_version": 1, "run_id": run_id, "suite_id": suite_id,
        "model_id": model_id, "case_id": case["case_id"], "attempt_id": attempt_id,
        "status": status, "output": output, "error_code": error_code,
        "checks": evaluation["checks"], "blocker_flags": evaluation["blocker_flags"],
        "metrics": {"first_token_ms": result.first_token_ms, "total_ms": result.total_ms,
                    "prompt_tokens": result.prompt_tokens, "output_tokens": result.output_tokens, "seed": seed},
        "started_at": started_at, "completed_at": _iso(),
    }
    return item, evaluation["semantic_reviews"]


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return round(ordered[index], 3)


def _aggregate(run_id: str, suite_id: str, cases: list[dict[str, Any]], items: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = ("pass", "fail", "blocker", "invalid", "error", "skipped")
    counts = Counter(item["status"] for item in items)
    by_case = defaultdict(list)
    case_meta = {case["case_id"]: case for case in cases}
    for item in items:
        by_case[item["case_id"]].append(item)
    item_status = Counter()
    for attempts in by_case.values():
        attempt_states = {x["status"] for x in attempts}
        state = next((x for x in ("blocker", "error", "fail", "invalid", "skipped") if x in attempt_states), "pass")
        item_status[state] += 1
    family: dict[str, Counter[str]] = defaultdict(Counter)
    category: dict[str, Counter[str]] = defaultdict(Counter)
    for case_id, attempts in by_case.items():
        for item in attempts:
            family[case_meta[case_id]["scenario_family"]][item["status"]] += 1
            category[case_meta[case_id]["category"]][item["status"]] += 1
    first = [x["metrics"]["first_token_ms"] for x in items if x["metrics"]["first_token_ms"] is not None]
    total = [x["metrics"]["total_ms"] for x in items if x["metrics"]["total_ms"] is not None]
    tokens = [float(x["metrics"]["output_tokens"]) for x in items if x["metrics"]["output_tokens"] is not None]
    table = lambda groups: {key: {s: value[s] for s in statuses} for key, value in sorted(groups.items())}
    return {
        "schema_version": 1, "run_id": run_id, "suite_id": suite_id,
        "item_denominator": len(by_case), "attempt_denominator": len(items),
        "scenario_denominator": len({(c["category"], c["scenario_family"]) for c in cases}),
        "item_counts": {s: item_status[s] for s in statuses},
        "attempt_counts": {s: counts[s] for s in statuses},
        "timeouts": sum(x.get("error_code") == "timeout" for x in items),
        "pending_semantic_review": sum(x.get("error_code") == "semantic_review_pending" for x in items),
        "by_category_attempt": table(category), "by_family_attempt": table(family),
        "performance": {
            "first_token_ms": {"p50": _percentile(first, .5), "p95": _percentile(first, .95)},
            "total_ms": {"p50": _percentile(total, .5), "p95": _percentile(total, .95)},
            "output_tokens": {"p50": _percentile(tokens, .5), "p95": _percentile(tokens, .95)},
        },
        "blocker_flags": dict(sorted(Counter(flag for x in items for flag in x["blocker_flags"]).items())),
    }


async def _run_cases(provider: EvaluationProvider, cases: list[dict[str, Any]], profile: dict[str, Any],
                     suite_id: str, run_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    raw: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    validator = Draft202012Validator(_load_json(AUTO_RESULT_SCHEMA_PATH), format_checker=FormatChecker())
    for case in cases:
        for seed in case["generation"]["seed_set"]:
            history: list[ContextMessage] = []
            turns = case.get("turns") or [{"turn_id": None, "user_message": case["messages"][-1]["content"]}]
            for turn_index, turn in enumerate(turns, 1):
                turn_id = turn.get("turn_id")
                attempt_id = f"{case['case_id']}.s{seed}" + (f".{turn_id}" if turn_id else "")
                started_at = _iso()
                error_code = None
                response: Any = None
                try:
                    generated = await provider.generate(
                        case_id=case["case_id"], attempt_id=attempt_id,
                        user_text=turn["user_message"], history=tuple(history),
                        options=_lane_options(case, profile, seed),
                    )
                    response = generated.output
                    if not isinstance(response, str):
                        item = _error_result(run_id, suite_id, provider.model_id, case["case_id"], attempt_id, seed, started_at, "invalid_output_type", "invalid")
                        error_code = "invalid_output_type"
                    elif not response.strip():
                        item = _error_result(run_id, suite_id, provider.model_id, case["case_id"], attempt_id, seed, started_at, "empty_output", "invalid")
                        error_code = "empty_output"
                    else:
                        item, pending = _evaluate_result(
                            run_id=run_id, suite_id=suite_id, model_id=provider.model_id,
                            case=case, attempt_id=attempt_id, output=response, result=generated,
                            seed=seed, started_at=started_at, turn_id=turn_id,
                        )
                        reviews.extend(pending)
                except (TimeoutError, asyncio.TimeoutError):
                    item = _error_result(run_id, suite_id, provider.model_id, case["case_id"], attempt_id, seed, started_at, "timeout")
                    error_code = "timeout"
                except Exception as exc:
                    item = _error_result(run_id, suite_id, provider.model_id, case["case_id"], attempt_id, seed, started_at, f"provider_error.{type(exc).__name__}")
                    error_code = item["error_code"]
                validator.validate(item)
                items.append(item)
                raw.append({
                    "schema_version": 1, "run_id": run_id, "suite_id": suite_id,
                    "model_id": provider.model_id, "case_id": case["case_id"],
                    "attempt_id": attempt_id, "turn_id": turn_id,
                    "provider_status": "completed" if error_code is None else "error",
                    "response": response, "error_code": error_code,
                    "started_at": started_at, "completed_at": item["completed_at"],
                })
                if isinstance(response, str) and response.strip():
                    now = datetime.now(timezone.utc)
                    seq = len(history) + 1
                    history.extend((
                        ContextMessage(f"{attempt_id}.user", "user", turn["user_message"], seq, now),
                        ContextMessage(f"{attempt_id}.assistant", "assistant", response, seq + 1, now + timedelta(microseconds=1)),
                    ))
    return raw, items, reviews


def _report_markdown(manifest: dict[str, Any], aggregate: dict[str, Any]) -> str:
    counts = aggregate["attempt_counts"]
    perf = aggregate["performance"]
    return f"""# CHAT-02 run report

- Run: `{manifest['run_id']}`
- Mode: `{manifest['mode']}`
- Model: `{manifest['model']['model_id']}`
- Suite: `{manifest['suite']['suite_id']}`
- Exit: `{manifest['exit_status']}`

| Metric | Value |
|---|---:|
| Items | {aggregate['item_denominator']} |
| Attempts | {aggregate['attempt_denominator']} |
| Pass | {counts['pass']} |
| Fail | {counts['fail']} |
| Blocker | {counts['blocker']} |
| Pending semantic review | {aggregate['pending_semantic_review']} |
| Error | {counts['error']} |
| Invalid | {counts['invalid']} |
| First token P50/P95 ms | {perf['first_token_ms']['p50']} / {perf['first_token_ms']['p95']} |
| Total P50/P95 ms | {perf['total_ms']['p50']} / {perf['total_ms']['p95']} |

Semantic review items remain unresolved until a human records evidence and a decision.
"""


async def run_evaluation(*, mode: str, model_id: str, cases: list[dict[str, Any]],
                         reports_root: Path = REPORTS_ROOT, run_id: str | None = None,
                         provider: EvaluationProvider | None = None) -> Path:
    suite, profile, model_manifest = verify_contract(model_id)
    started = datetime.now(timezone.utc).astimezone()
    selected = run_id or f"chat02-{mode}-{model_id}-{started.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    if STABLE_ID.fullmatch(selected) is None:
        raise ValueError("run_id must be a stable ID")
    run_dir = reports_root / selected
    run_dir.mkdir(parents=True, exist_ok=False)
    runner_hash_before = sha256_file(RUNNER_PATH)
    actual_provider = provider or RealLlamaCppProvider(model_manifest, profile, run_dir / "runtime_bridge_manifest.json")
    exit_status = "passed"
    raw: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    close_error: str | None = None
    try:
        await actual_provider.start()
        raw, items, reviews = await _run_cases(actual_provider, cases, profile, suite["suite_id"], selected)
    except Exception as exc:
        exit_status = "failed"
        close_error = f"startup_or_run.{type(exc).__name__}"
        raise
    finally:
        try:
            await actual_provider.close()
        except Exception as exc:
            exit_status = "failed"
            close_error = f"close.{type(exc).__name__}"
        if isinstance(actual_provider, RealLlamaCppProvider) and _port_in_use(
            profile["launch"]["host"], profile["launch"]["port"]
        ):
            exit_status = "failed"
            close_error = "owned_port_not_released"
    if sha256_file(RUNNER_PATH) != runner_hash_before:
        raise ValueError("runner changed during execution")
    aggregate = _aggregate(selected, suite["suite_id"], cases, items)
    if (mode != "controlled-selftest" and aggregate["attempt_counts"]["error"]) or close_error:
        exit_status = "failed"
    run_manifest = {
        "schema_version": 1, "run_id": selected, "mode": mode,
        "suite": {"suite_id": suite["suite_id"], "manifest_path": str(Path(profile["suite_contract"]["manifest_path"])),
                  "manifest_file_sha256": profile["suite_contract"]["manifest_file_sha256"],
                  "manifest_self_sha256": profile["suite_contract"]["manifest_self_sha256"]},
        "model": {"model_id": model_id, "manifest_path": str(MODEL_PATHS[model_id]),
                  "manifest_sha256": sha256_file(MODEL_PATHS[model_id]), "gguf_sha256": model_manifest["asset"]["sha256"]},
        "runtime": {"profile_path": str(PROFILE_PATH), "profile_sha256": sha256_file(PROFILE_PATH),
                    "runner_path": str(RUNNER_PATH), "runner_sha256": runner_hash_before,
                    "prompt_sha256": profile["request_contract"]["prompt_renderer"]["sha256"],
                    "adapter_sha256": profile["backend"]["runtime_adapter"]["sha256"],
                    "server_sha256": profile["backend"]["server"]["sha256"], "python": platform.python_version()},
        "generation": {"case_count": len(cases), "network_allowed": False, "localhost_only": True, "lanes": profile["lanes"]},
        "started_at": started.isoformat(timespec="milliseconds"), "completed_at": _iso(),
        "exit_status": exit_status, "lifecycle_error": close_error,
    }
    _write_json(run_dir / "run_manifest.json", run_manifest)
    _write_jsonl(run_dir / "raw_outputs.jsonl", raw)
    _write_jsonl(run_dir / "auto_item_results.jsonl", items)
    _write_json(run_dir / "aggregate.json", aggregate)
    _write_jsonl(run_dir / "blocker_review.jsonl", reviews)
    _write_jsonl(run_dir / "human_ballots.jsonl", [])
    (run_dir / "report.md").write_text(_report_markdown(run_manifest, aggregate), encoding="utf-8", newline="\n")
    return run_dir


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(.2)
        return probe.connect_ex((host, port)) == 0


async def run_controlled_selftest(reports_root: Path = REPORTS_ROOT) -> Path:
    cases_by_id = {c["case_id"]: c for c in _load_jsonl(SINGLE_PATH)}
    ids = ["frozen.identity.name", "frozen.general.math_01", "frozen.identity.age",
           "frozen.general.math_02", "frozen.daily_relevance.meal_choice"]
    cases = [dict(cases_by_id[x], generation={**cases_by_id[x]["generation"], "seed_set": [42]}) for x in ids]
    attempt = lambda case_id: f"{case_id}.s42"
    provider = ControlledProvider({
        attempt(ids[1]): "timeout", attempt(ids[2]): "crash",
        attempt(ids[3]): "empty", attempt(ids[4]): "invalid",
    })
    run_dir = await run_evaluation(mode="controlled-selftest", model_id="qwen3-4b-base-q4_k_m",
                                   cases=cases, reports_root=reports_root, provider=provider)
    items = _load_jsonl(run_dir / "auto_item_results.jsonl")
    observed = [item.get("error_code") for item in items]
    expected = [None, "timeout", "provider_error.RuntimeError", "empty_output", "invalid_output_type"]
    if observed != expected or not provider.started or not provider.closed:
        raise AssertionError(f"controlled self-test mismatch: {observed}")
    return run_dir


def _cases_for_mode(mode: str) -> list[dict[str, Any]]:
    if mode == "smoke":
        return _load_jsonl(DEV_PATH)[:3]
    if mode == "automatic":
        return _load_jsonl(SINGLE_PATH) + _load_jsonl(MULTITURN_PATH)
    if mode == "human-pair":
        return [case for case in _load_jsonl(HUMAN_PATH) if case["case_type"] == "human_pair"]
    raise ValueError(f"unsupported mode: {mode}")


def main() -> int:
    parser = argparse.ArgumentParser(description="CHAT-02 real and controlled evaluation runner")
    parser.add_argument("--mode", choices=("controlled-selftest", "smoke", "automatic", "human-pair"), required=True)
    parser.add_argument("--model", choices=tuple(MODEL_PATHS), default="qwen3-4b-base-q4_k_m")
    parser.add_argument("--reports-root", type=Path, default=REPORTS_ROOT)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    try:
        if args.mode == "controlled-selftest":
            path = asyncio.run(run_controlled_selftest(args.reports_root))
        else:
            path = asyncio.run(run_evaluation(mode=args.mode, model_id=args.model,
                                              cases=_cases_for_mode(args.mode), reports_root=args.reports_root,
                                              run_id=args.run_id))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "completed", "report_dir": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

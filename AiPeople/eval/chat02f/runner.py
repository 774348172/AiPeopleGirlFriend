from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import socket
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from eval.chat01.freeze import FreezeVerificationError, verify_file
from eval.chat01.leakage_guard import sha256_file
from eval.chat02.runner import ProviderResult, RealLlamaCppProvider
from eval.chat02f.freeze_v2 import CONTRACT_V2_PATH, verify_v2
from runtime.adapters.llama_cpp import GenerationOptions


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = Path(__file__).resolve()
PROFILE_PATH = ROOT / "eval" / "chat02f" / "execution_profile_v1.json"
PROFILE_SCHEMA_PATH = ROOT / "eval" / "chat02f" / "schema" / "execution_profile.schema.json"
SMOKE_PATH = ROOT / "eval" / "chat02f" / "smoke_cases_v1.jsonl"
SMOKE_SCHEMA_PATH = ROOT / "eval" / "chat02f" / "schema" / "smoke_case.schema.json"
REPORTS_ROOT = ROOT / "eval" / "chat02f" / "reports"
MODEL_MANIFESTS = {
    "qwen3-4b-base-q4_k_m": ROOT / "eval" / "chat02" / "models" / "qwen3-4b-base-q4_k_m.json",
    "qinweixi-v2500-final-q4_k_m": ROOT / "eval" / "chat02f" / "models" / "qinweixi-v2500-final-q4_k_m.json",
}
RUN_IDS = {
    "qwen3-4b-base-q4_k_m": "chat02fc-smoke-base-v1",
    "qinweixi-v2500-final-q4_k_m": "chat02fc-smoke-v2500-v1",
}
UNIVERSAL_FORBIDDEN = (
    re.compile(r"<\/?think>", re.IGNORECASE),
    re.compile(r"QIN_WEIXI_REPLY_SYSTEM"),
    re.compile(r"chat02f-comparison-contract"),
    re.compile(r"(?:^|\n)\s*\{\s*\"(?:role|system|memory|internal|prompt)\"", re.IGNORECASE),
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
    path.write_text("".join(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n" for value in values), encoding="utf-8", newline="\n")


def _validate(value: dict[str, Any], schema_path: Path) -> None:
    schema = _load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        return probe.connect_ex((host, port)) == 0


def _gpu_compute_apps() -> list[dict[str, Any]]:
    command = [
        "nvidia-smi",
        "--query-compute-apps=pid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    apps: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        parts = [part.strip() for part in line.split(",", 2)]
        if len(parts) != 3:
            continue
        try:
            pid, memory_mib = int(parts[0]), int(parts[2])
        except ValueError:
            continue
        apps.append({"pid": pid, "process_name": parts[1], "used_memory_mib": memory_mib})
    return apps


def _llama_gpu_apps(apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [app for app in apps if "llama-server" in app["process_name"].lower()]


def _regex_matches(pattern: str, output: str) -> bool:
    return re.search(pattern, output, re.IGNORECASE | re.MULTILINE) is not None


def evaluate_smoke(case: dict[str, Any], output: Any, error: str | None) -> dict[str, Any]:
    reasons: list[str] = []
    if error is not None:
        reasons.append(error)
    if not isinstance(output, str):
        reasons.append("invalid_output_type")
        text = ""
    else:
        text = output.strip()
        if not text:
            reasons.append("empty_output")
    if text:
        for pattern in UNIVERSAL_FORBIDDEN:
            if pattern.search(text):
                reasons.append(f"protocol_leak:{pattern.pattern}")
        for pattern in case["forbidden"]:
            if _regex_matches(pattern, text):
                reasons.append(f"forbidden_match:{pattern}")
        for pattern in case["required_all"]:
            if not _regex_matches(pattern, text):
                reasons.append(f"missing_required:{pattern}")
        if case["required_any"] and not any(_regex_matches(pattern, text) for pattern in case["required_any"]):
            reasons.append("missing_required_any")
    blocker = any(
        reason.startswith(("transport_", "provider_", "invalid_", "empty_", "protocol_", "forbidden_"))
        for reason in reasons
    )
    return {"status": "blocker" if blocker else ("fail" if reasons else "pass"), "reasons": reasons}


def verify_profile() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    contract = verify_v2()
    if not contract["gates"]["can_start_chat02f_c"]:
        raise FreezeVerificationError("comparison contract v2 does not permit CHAT-02F-C")
    profile = _load_json(PROFILE_PATH)
    _validate(profile, PROFILE_SCHEMA_PATH)
    verify_file(ROOT, profile["comparison_contract"]["contract"])
    verify_file(ROOT, profile["backend"]["server"])
    verify_file(ROOT, profile["backend"]["runtime_adapter"])
    verify_file(ROOT, profile["request_contract"]["prompt_renderer"])
    verify_file(ROOT, profile["suite_contract"]["manifest"])
    verify_file(ROOT, profile["smoke_contract"]["cases"])
    verify_file(ROOT, profile["smoke_contract"]["schema"])
    if profile["comparison_contract"]["contract_self_sha256"] != contract["freeze"]["contract_sha256"]:
        raise FreezeVerificationError("profile comparison contract self hash changed")

    cases = _load_jsonl(SMOKE_PATH)
    for case in cases:
        _validate(case, SMOKE_SCHEMA_PATH)
    if len(cases) != profile["smoke_contract"]["case_count"]:
        raise FreezeVerificationError("smoke case count changed")
    counts = Counter(case["category"] for case in cases)
    if dict(counts) != profile["smoke_contract"]["category_counts"]:
        raise FreezeVerificationError("smoke category counts changed")
    if len({case["case_id"] for case in cases}) != len(cases):
        raise FreezeVerificationError("duplicate smoke case ID")

    manifests: dict[str, dict[str, Any]] = {}
    for binding in contract["models"]:
        path = MODEL_MANIFESTS[binding["model_id"]]
        if sha256_file(path) != binding["manifest"]["sha256"]:
            raise FreezeVerificationError(f"model manifest changed: {binding['model_id']}")
        manifest = _load_json(path)
        verify_file(ROOT, manifest["asset"])
        manifests[binding["model_id"]] = manifest
    return profile, cases, manifests


def _options(case: dict[str, Any], profile: dict[str, Any]) -> GenerationOptions:
    lane = profile["lanes"][case["lane"]]
    return GenerationOptions(
        max_tokens=case["max_tokens"], seed=case["seed"],
        temperature=lane["temperature"], top_p=lane["top_p"], repeat_penalty=lane["repeat_penalty"],
    )


async def _wait_released(profile: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    host, port = profile["launch"]["host"], profile["launch"]["port"]
    apps: list[dict[str, Any]] = []
    for _ in range(30):
        apps = _gpu_compute_apps()
        if not _port_in_use(host, port) and not _llama_gpu_apps(apps):
            return True, apps
        await asyncio.sleep(0.5)
    return False, apps


async def run_model(
    *, model_id: str, manifest: dict[str, Any], profile: dict[str, Any],
    cases: list[dict[str, Any]], reports_root: Path,
) -> tuple[Path, dict[str, Any]]:
    run_id = RUN_IDS[model_id]
    run_dir = reports_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    provider = RealLlamaCppProvider(manifest, profile, run_dir / "runtime_bridge_manifest.json")
    started_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
    runner_sha256 = sha256_file(RUNNER_PATH)
    before_apps = _gpu_compute_apps()
    results: list[dict[str, Any]] = []
    lifecycle_error: str | None = None
    try:
        await provider.start()
        await provider.generate(
            case_id="chat02f.smoke.warmup", attempt_id=f"{run_id}.warmup",
            user_text="在吗？", history=(),
            options=GenerationOptions(max_tokens=8, seed=42, temperature=0.0, top_p=1.0, repeat_penalty=1.1),
        )
        for case in cases:
            error: str | None = None
            generated: ProviderResult | None = None
            try:
                generated = await provider.generate(
                    case_id=case["case_id"], attempt_id=f"{case['case_id']}.s{case['seed']}",
                    user_text=case["prompt"], history=(), options=_options(case, profile),
                )
                output: Any = generated.output
            except (TimeoutError, asyncio.TimeoutError):
                output, error = None, "transport_timeout"
            except Exception as exc:
                output, error = None, f"provider_error:{type(exc).__name__}"
            evaluation = evaluate_smoke(case, output, error)
            results.append({
                "case_id": case["case_id"], "category": case["category"], "prompt": case["prompt"],
                "seed": case["seed"], "lane": case["lane"], "output": output,
                "status": evaluation["status"], "reasons": evaluation["reasons"],
                "review_focus": case["review_focus"],
                "metrics": {
                    "first_token_ms": generated.first_token_ms if generated else None,
                    "total_ms": generated.total_ms if generated else None,
                    "prompt_tokens": generated.prompt_tokens if generated else None,
                    "output_tokens": generated.output_tokens if generated else None,
                },
            })
    finally:
        try:
            await provider.close()
        except Exception as exc:
            lifecycle_error = f"close_error:{type(exc).__name__}"
    released, after_apps = await _wait_released(profile)
    if not released:
        lifecycle_error = lifecycle_error or "port_or_llama_gpu_process_not_released"
    if sha256_file(RUNNER_PATH) != runner_sha256:
        raise FreezeVerificationError("CHAT-02F runner changed during execution")

    counts = Counter(result["status"] for result in results)
    first_tokens = [result["metrics"]["first_token_ms"] for result in results if result["metrics"]["first_token_ms"] is not None]
    summary = {
        "run_id": run_id,
        "model_id": model_id,
        "case_count": len(cases),
        "counts": {key: counts[key] for key in ("pass", "fail", "blocker")},
        "transport_errors": sum(any(reason.startswith(("transport_", "provider_")) for reason in result["reasons"]) for result in results),
        "first_token_ms": {
            "min": round(min(first_tokens), 3) if first_tokens else None,
            "max": round(max(first_tokens), 3) if first_tokens else None,
            "mean": round(sum(first_tokens) / len(first_tokens), 3) if first_tokens else None,
        },
        "lifecycle": {
            "error": lifecycle_error,
            "port_released": not _port_in_use(profile["launch"]["host"], profile["launch"]["port"]),
            "llama_gpu_process_released": not _llama_gpu_apps(after_apps),
            "gpu_compute_apps_before": before_apps,
            "gpu_compute_apps_after": after_apps,
        },
        "automatic_gate_passed": not lifecycle_error and len(results) == 12 and counts["fail"] == 0 and counts["blocker"] == 0,
        "manual_review_status": "pending",
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
        "bindings": {
            "profile_sha256": sha256_file(PROFILE_PATH),
            "comparison_contract_sha256": sha256_file(CONTRACT_V2_PATH),
            "runner_sha256": runner_sha256,
            "smoke_cases_sha256": sha256_file(SMOKE_PATH),
            "model_gguf_sha256": manifest["asset"]["sha256"],
        },
    }
    _write_jsonl(run_dir / "results.jsonl", results)
    _write_json(run_dir / "summary.json", summary)
    report_lines = [
        f"# {run_id}", "", f"- Model: `{model_id}`", f"- Automatic gate: `{summary['automatic_gate_passed']}`",
        f"- Pass/fail/blocker: {counts['pass']}/{counts['fail']}/{counts['blocker']}",
        f"- Lifecycle error: `{lifecycle_error}`", "", "| Case | Status | Output |", "|---|---|---|",
    ]
    for result in results:
        output = str(result["output"]).replace("|", "\\|").replace("\n", " ")
        report_lines.append(f"| `{result['case_id']}` | {result['status']} | {output} |")
    (run_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8", newline="\n")
    return run_dir, summary


async def run_both(reports_root: Path = REPORTS_ROOT) -> dict[str, Any]:
    profile, cases, manifests = verify_profile()
    if _port_in_use(profile["launch"]["host"], profile["launch"]["port"]):
        raise RuntimeError("shared llama.cpp port is already in use")
    reports_root.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []
    for model_id in profile["comparison_contract"]["model_ids"]:
        _path, summary = await run_model(
            model_id=model_id, manifest=manifests[model_id], profile=profile,
            cases=cases, reports_root=reports_root,
        )
        summaries.append(summary)
        if summary["lifecycle"]["error"] or not summary["lifecycle"]["port_released"] or not summary["lifecycle"]["llama_gpu_process_released"]:
            raise RuntimeError(f"model lifecycle did not release cleanly: {model_id}")
    comparison = {
        "comparison_id": "chat02fc-smoke-comparison-v1",
        "profile_id": profile["profile_id"],
        "model_order": profile["comparison_contract"]["model_ids"],
        "models": summaries,
        "automatic_gate_passed": all(summary["automatic_gate_passed"] for summary in summaries),
        "manual_review_status": "pending",
        "next_stage": "manual_smoke_review" if all(summary["automatic_gate_passed"] for summary in summaries) else "blocked",
    }
    _write_json(reports_root / "chat02fc-smoke-comparison-v1.json", comparison)
    return comparison


def main() -> int:
    parser = argparse.ArgumentParser(description="Run CHAT-02F-C directed smoke sequentially on base and v2500.")
    parser.add_argument("--reports-root", type=Path, default=REPORTS_ROOT)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.verify_only:
            profile, cases, manifests = verify_profile()
            result = {"status": "verified", "profile_id": profile["profile_id"], "cases": len(cases), "models": list(manifests)}
        else:
            result = asyncio.run(run_both(args.reports_root))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import socket
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eval.chat01.rules import evaluate_case_output
from eval.chat02.runner import RealLlamaCppProvider
from runtime.adapters.llama_cpp import GenerationOptions

from .build_cases import MANIFEST_PATH, OUTPUT_PATH, ROOT, build, sha256_file


PROFILE_PATH = ROOT / "eval/chat02f/execution_profile_v2.json"
MODEL_MANIFESTS = {
    "qwen3-4b-base-q4_k_m": ROOT / "eval/chat02/models/qwen3-4b-base-q4_k_m.json",
    "qinweixi-v2500-final-q4_k_m": ROOT / "eval/chat02f/models/qinweixi-v2500-final-q4_k_m.json",
}
MODEL_ORDER = tuple(MODEL_MANIFESTS)
REPORTS_ROOT = ROOT / "eval/diagnostic120/reports"
RUN_PORT = 18084


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
        newline="\n",
    )


def _iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def verify() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = build()
    if sha256_file(OUTPUT_PATH) != manifest["cases"]["sha256"]:
        raise ValueError("120-case asset hash mismatch")
    cases = _load_jsonl(OUTPUT_PATH)
    if len(cases) != 120:
        raise ValueError("diagnostic requires exactly 120 cases")

    profile = _load_json(PROFILE_PATH)
    profile = json.loads(json.dumps(profile))
    profile["launch"]["port"] = RUN_PORT
    profile["comparison_contract"]["model_ids"] = list(MODEL_ORDER)

    manifests: dict[str, dict[str, Any]] = {}
    for model_id, path in MODEL_MANIFESTS.items():
        model_manifest = _load_json(path)
        if model_manifest["model_id"] != model_id:
            raise ValueError(f"model ID mismatch: {path}")
        asset = Path(model_manifest["asset"]["path"])
        if not asset.is_file() or asset.stat().st_size != model_manifest["asset"]["bytes"]:
            raise ValueError(f"model asset missing or wrong size: {asset}")
        manifests[model_id] = model_manifest
    return manifest, cases, profile, manifests


def _options(case: dict[str, Any], profile: dict[str, Any]) -> GenerationOptions:
    lane = profile["lanes"][case["generation"]["lane"]]
    return GenerationOptions(
        max_tokens=case["generation"]["max_new_tokens"],
        seed=42,
        temperature=lane["temperature"],
        top_p=lane["top_p"],
        repeat_penalty=lane["repeat_penalty"],
    )


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * fraction) - 1)], 3)


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(item["automatic_status"] for item in results)
    categories: dict[str, Counter[str]] = defaultdict(Counter)
    for item in results:
        categories[item["category"]][item["automatic_status"]] += 1
    first = [item["metrics"]["first_token_ms"] for item in results if item["metrics"]["first_token_ms"] is not None]
    total = [item["metrics"]["total_ms"] for item in results if item["metrics"]["total_ms"] is not None]
    lengths = [len(item["output"]) for item in results if isinstance(item["output"], str)]
    return {
        "case_count": len(results),
        "automatic_status_counts": dict(sorted(statuses.items())),
        "by_category": {
            key: dict(sorted(value.items())) for key, value in sorted(categories.items())
        },
        "performance": {
            "first_token_ms": {"p50": _percentile(first, 0.5), "p95": _percentile(first, 0.95)},
            "total_ms": {"p50": _percentile(total, 0.5), "p95": _percentile(total, 0.95)},
            "response_chars": {"p50": _percentile(lengths, 0.5), "p95": _percentile(lengths, 0.95)},
        },
        "blocker_flags": dict(sorted(Counter(flag for item in results for flag in item["blocker_flags"]).items())),
    }


async def _run_model(
    model_id: str,
    model_manifest: dict[str, Any],
    profile: dict[str, Any],
    cases: list[dict[str, Any]],
    output_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    model_dir = output_dir / model_id
    model_dir.mkdir(parents=True, exist_ok=False)
    bridge_path = model_dir / "runtime_bridge_manifest.json"
    provider = RealLlamaCppProvider(model_manifest, profile, bridge_path)
    results: list[dict[str, Any]] = []
    started_at = _iso()
    try:
        await provider.start()
        await provider.generate(
            case_id="diagnostic120.warmup",
            attempt_id=f"{model_id}.warmup",
            user_text="在吗？",
            history=(),
            options=GenerationOptions(max_tokens=8, seed=42, temperature=0.0, top_p=1.0, repeat_penalty=1.1),
        )
        for index, case in enumerate(cases, start=1):
            result = None
            error = None
            try:
                result = await provider.generate(
                    case_id=case["case_id"],
                    attempt_id=f"{case['case_id']}.s42",
                    user_text=case["messages"][-1]["content"],
                    history=(),
                    options=_options(case, profile),
                )
                output = result.output
                evaluation = evaluate_case_output(case, output, attempt_id=f"{case['case_id']}.s42")
                automatic_status = evaluation["status"]
                checks = evaluation["checks"]
                blocker_flags = evaluation["blocker_flags"]
            except Exception as exc:
                output = None
                error = f"{type(exc).__name__}: {exc}"
                automatic_status = "error"
                checks = []
                blocker_flags = []
            results.append(
                {
                    "index": index,
                    "case_id": case["case_id"],
                    "category": case["category"],
                    "prompt": case["messages"][-1]["content"],
                    "output": output,
                    "error": error,
                    "automatic_status": automatic_status,
                    "checks": checks,
                    "blocker_flags": blocker_flags,
                    "metrics": {
                        "first_token_ms": result.first_token_ms if result else None,
                        "total_ms": result.total_ms if result else None,
                        "prompt_tokens": result.prompt_tokens if result else None,
                        "output_tokens": result.output_tokens if result else None,
                    },
                }
            )
            if index % 10 == 0:
                print(f"[{model_id}] {index}/120", flush=True)
    finally:
        await provider.close()

    for _ in range(30):
        if not _port_in_use(RUN_PORT):
            break
        await asyncio.sleep(0.5)
    if _port_in_use(RUN_PORT):
        raise RuntimeError(f"llama.cpp port {RUN_PORT} was not released")

    _write_jsonl(model_dir / "results.jsonl", results)
    summary = {
        "model_id": model_id,
        "model_gguf_sha256": model_manifest["asset"]["sha256"],
        "started_at": started_at,
        "completed_at": _iso(),
        "aggregate": _aggregate(results),
    }
    _write_json(model_dir / "summary.json", summary)
    return results, summary


def _blind_pairs(
    cases: list[dict[str, Any]],
    outputs: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_model = {
        model_id: {item["case_id"]: item for item in results}
        for model_id, results in outputs.items()
    }
    public: list[dict[str, Any]] = []
    private: list[dict[str, Any]] = []
    base_id, trained_id = MODEL_ORDER
    for index, case in enumerate(cases, start=1):
        case_id = case["case_id"]
        trained_is_a = int(hashlib.sha256(f"diagnostic120-blind\x1f{case_id}".encode()).hexdigest(), 16) % 2 == 0
        a_model = trained_id if trained_is_a else base_id
        b_model = base_id if trained_is_a else trained_id
        public.append(
            {
                "unit_id": f"diagnostic120.{index:03d}",
                "case_id": case_id,
                "category": case["category"],
                "prompt": case["messages"],
                "candidate_a": by_model[a_model][case_id]["output"],
                "candidate_b": by_model[b_model][case_id]["output"],
            }
        )
        private.append(
            {
                "unit_id": f"diagnostic120.{index:03d}",
                "candidate_a_model": a_model,
                "candidate_b_model": b_model,
            }
        )
    return public, private


def _report(summary: dict[str, Any]) -> str:
    lines = [
        "# 秦未晞 v2500 与 Qwen3-4B 基座：120 题同题诊断",
        "",
        "> 本报告是诊断结果，不是正式发布闸门。语义优劣必须结合匿名人工 A/B 复核。",
        "",
        "## 对照条件",
        "",
        "- 两个模型使用完全相同的秦未晞运行时身份锚和 llama.cpp 参数。",
        "- 唯一主动变量是模型权重。",
        "- 120 题来自冻结 240 题的确定性分层抽样；已排除受污染题 frozen.identity.name。",
        "- 每题每模型只生成一次，固定 seed=42；基座先跑，释放显存后再跑 v2500。",
        "",
        "## 自动统计",
        "",
        "| 模型 | 硬规则通过 | 待语义复核 | 阻断 | 错误 | 首字 P50/P95 | 总耗时 P50/P95 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for model in summary["models"]:
        aggregate = model["aggregate"]
        counts = aggregate["automatic_status_counts"]
        perf = aggregate["performance"]
        lines.append(
            f"| {model['model_id']} | {counts.get('pass', 0)} | {counts.get('pending_review', 0)} | "
            f"{counts.get('blocker', 0)} | {counts.get('error', 0)} | "
            f"{perf['first_token_ms']['p50']}/{perf['first_token_ms']['p95']} ms | "
            f"{perf['total_ms']['p50']}/{perf['total_ms']['p95']} ms |"
        )
    paired = summary["paired_automatic"]
    lines.extend(
        [
            "",
            "## 配对变化",
            "",
            f"- v2500 相对基座：{paired['improved']} 题硬规则状态改善，{paired['regressed']} 题退化，{paired['unchanged']} 题不变。",
            f"- 身份时间线：改善 {paired['by_category'].get('identity_timeline', {}).get('improved', 0)}，退化 {paired['by_category'].get('identity_timeline', {}).get('regressed', 0)}。",
            f"- 通用能力：改善 {paired['by_category'].get('general_capability', {}).get('improved', 0)}，退化 {paired['by_category'].get('general_capability', {}).get('regressed', 0)}。",
            "",
            "## 结果边界",
            "",
            "自动状态只覆盖可确定判断的协议、事实和安全硬规则。人格自然度、关系感、是否更像秦未晞，必须使用 pairs_public.jsonl 做匿名 A/B 判断；在揭示 assignments_private.jsonl 前不能查看模型对应关系。",
        ]
    )
    return "\n".join(lines) + "\n"


def _paired_automatic(outputs: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    base_id, trained_id = MODEL_ORDER
    base = {item["case_id"]: item for item in outputs[base_id]}
    trained = {item["case_id"]: item for item in outputs[trained_id]}
    counts = Counter()
    by_category: dict[str, Counter[str]] = defaultdict(Counter)
    changed: list[dict[str, str]] = []
    for case_id, left in base.items():
        right = trained[case_id]
        left_status = left["automatic_status"]
        right_status = right["automatic_status"]
        if left_status == "fail" and right_status == "pass":
            direction = "improved"
        elif left_status == "pass" and right_status == "fail":
            direction = "regressed"
        else:
            direction = "unchanged"
        counts[direction] += 1
        by_category[left["category"]][direction] += 1
        if direction != "unchanged":
            changed.append(
                {
                    "case_id": case_id,
                    "category": left["category"],
                    "direction": direction,
                    "base_status": left_status,
                    "v2500_status": right_status,
                }
            )
    return {
        "improved": counts["improved"],
        "regressed": counts["regressed"],
        "unchanged": counts["unchanged"],
        "by_category": {
            key: dict(sorted(value.items())) for key, value in sorted(by_category.items())
        },
        "changed_cases": changed,
    }


def _finalize(
    output_dir: Path,
    manifest: dict[str, Any],
    cases: list[dict[str, Any]],
    outputs: dict[str, list[dict[str, Any]]],
    summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    public, private = _blind_pairs(cases, outputs)
    _write_jsonl(output_dir / "pairs_public.jsonl", public)
    _write_jsonl(output_dir / "assignments_private.jsonl", private)
    summary = {
        "diagnostic_id": manifest["diagnostic_id"],
        "status": "completed_pending_human_ab",
        "case_manifest_sha256": sha256_file(MANIFEST_PATH),
        "models": summaries,
        "paired_automatic": _paired_automatic(outputs),
        "blind_ab": {
            "units": len(public),
            "public_path": str((output_dir / "pairs_public.jsonl").relative_to(ROOT)).replace("\\", "/"),
            "private_path": str((output_dir / "assignments_private.jsonl").relative_to(ROOT)).replace("\\", "/"),
        },
    }
    _write_json(output_dir / "summary.json", summary)
    (output_dir / "report.md").write_text(_report(summary), encoding="utf-8", newline="\n")
    return summary


def finalize_existing(output_dir: Path) -> dict[str, Any]:
    manifest, cases, _profile, _manifests = verify()
    output_dir = output_dir.resolve()
    outputs: dict[str, list[dict[str, Any]]] = {}
    summaries: list[dict[str, Any]] = []
    for model_id in MODEL_ORDER:
        model_dir = output_dir / model_id
        results = _load_jsonl(model_dir / "results.jsonl")
        summary = _load_json(model_dir / "summary.json")
        if len(results) != 120 or summary.get("model_id") != model_id:
            raise ValueError(f"incomplete existing result: {model_id}")
        outputs[model_id] = results
        summaries.append(summary)
    return _finalize(output_dir, manifest, cases, outputs, summaries)


async def run(output_dir: Path) -> dict[str, Any]:
    manifest, cases, profile, manifests = verify()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if _port_in_use(RUN_PORT):
        raise RuntimeError(f"port {RUN_PORT} is already in use")
    output_dir.mkdir(parents=True)

    outputs: dict[str, list[dict[str, Any]]] = {}
    summaries: list[dict[str, Any]] = []
    for model_id in MODEL_ORDER:
        results, summary = await _run_model(model_id, manifests[model_id], profile, cases, output_dir)
        outputs[model_id] = results
        summaries.append(summary)

    return _finalize(output_dir, manifest, cases, outputs, summaries)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the simple 120-question base-vs-v2500 diagnostic.")
    parser.add_argument("--output", type=Path, default=REPORTS_ROOT / "simple120-v1")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--finalize-existing", action="store_true")
    args = parser.parse_args()
    try:
        manifest, cases, _profile, manifests = verify()
        if args.verify_only:
            print(json.dumps({"status": "verified", "diagnostic_id": manifest["diagnostic_id"], "cases": len(cases), "models": list(manifests)}, ensure_ascii=False))
            return 0
        summary = finalize_existing(args.output) if args.finalize_existing else asyncio.run(run(args.output))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

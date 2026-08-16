from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.world_mind.sys12 import Sys12ReleaseConfig, evaluate_sys12


def main(args: argparse.Namespace) -> int:
    config = Sys12ReleaseConfig.load(args.manifest)
    performance = _read_json(args.performance)
    combined = _read_json(args.combined)
    retrieval = _read_json(args.retrieval)
    resilience = _read_json(args.resilience)
    stability_path = _stability_path(args.stability_run)
    stability = _read_json(stability_path)
    candidate = _mapping(performance.get("candidates"), "performance.candidates").get(
        config.release_path
    )
    candidate = _mapping(candidate, f"performance.candidates.{config.release_path}")
    sys11_decision = _mapping(stability.get("decision"), "stability.decision")
    release = {
        "name": config.release_path,
        "asset_sha256": candidate.get("asset_sha256"),
        "lifecycle_passed": (
            candidate.get("lifecycle_passed") is True
            and _mapping(resilience.get("decision"), "resilience.decision").get(
                "repeatable_lifecycle_passed"
            )
            is True
        ),
        "combined_gpu_peak_mib": combined.get("combined_gpu_peak_mib"),
        "warm_first_visible_token_p95_ms": _p95(
            candidate, "warm_first_visible_token_ms"
        ),
        "reply_80_tokens_p95_ms": _p95(candidate, "reply_80_tokens_ms"),
        "reply_160_tokens_p95_ms": _p95(candidate, "reply_160_tokens_ms"),
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "scope": "sys12_frozen_release_acceptance",
        "generated_at": datetime.now().astimezone().isoformat(),
        "hardware": _hardware(),
        "manifest": str(config.manifest_path),
        "release_path": release,
        "memory_selection_incremental_p95_ms": _mapping(
            retrieval.get("recall_ms"), "retrieval.recall_ms"
        ).get("p95"),
        "foreground_priority_passed": _mapping(
            resilience.get("decision"), "resilience.decision"
        ).get("foreground_priority_passed"),
        "resilience": resilience,
        "stability": {
            "minutes": float(
                _mapping(stability.get("timing"), "stability.timing").get(
                    "wall_elapsed_seconds", 0
                )
            )
            / 60,
            "passed": sys11_decision.get("system_stability_passed") is True,
            "transaction_violations": _transaction_violations(stability),
            "report": str(stability_path),
            "turn_latency_ms": stability.get("turn_latency_ms"),
            "model_modes": stability.get("model_modes"),
        },
        "evidence": {
            "performance": str(Path(args.performance).resolve()),
            "combined_resource": str(Path(args.combined).resolve()),
            "retrieval": str(Path(args.retrieval).resolve()),
            "resilience": str(Path(args.resilience).resolve()),
            "stability": str(stability_path),
        },
    }
    report["decision"] = evaluate_sys12(report, config)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown = Path(args.markdown).resolve()
    markdown.write_text(_markdown(report, config), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "report": str(output),
                "markdown": str(markdown),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["decision"]["sys12_gate"] == "passed" else 1


def _stability_path(value: str) -> Path:
    path = Path(value).resolve()
    if path.is_file():
        return path
    reports = sorted(path.glob("sys11-*/report.json"), key=lambda item: item.stat().st_mtime)
    if not reports:
        raise FileNotFoundError("SYS-12 release stability report is missing")
    return reports[-1]


def _read_json(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"report must be an object: {resolved}")
    return value


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _p95(candidate: Mapping[str, Any], name: str) -> object:
    return _mapping(candidate.get(name), f"performance.{name}").get("p95")


def _transaction_violations(stability: Mapping[str, Any]) -> int:
    consistency = _mapping(stability.get("consistency"), "stability.consistency")
    names = (
        "orphan_turns",
        "orphan_events",
        "duplicate_requests",
        "event_count_mismatch",
        "dangling_model_decisions",
    )
    return sum(int(consistency.get(name, 0)) for name in names)


def _hardware() -> dict[str, object]:
    gpu = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    return {
        "os": platform.platform(),
        "machine": platform.machine(),
        "gpu": gpu.stdout.strip() if gpu.returncode == 0 else "unavailable",
    }


def _markdown(report: Mapping[str, Any], config: Sys12ReleaseConfig) -> str:
    release = _mapping(report["release_path"], "release_path")
    decision = _mapping(report["decision"], "decision")
    checks = _mapping(decision["checks"], "decision.checks")
    status = "通过" if decision["sys12_gate"] == "passed" else "未通过"
    check_lines = "\n".join(
        f"- {'通过' if passed else '未通过'}：`{name}`" for name, passed in checks.items()
    )
    evidence = _mapping(report["evidence"], "evidence")
    return f"""# SYS-12 发布推理栈、资源调度与正式性能报告

> 状态：{status}。生成时间：{report['generated_at']}。

## 冻结结论

- 发布路径：`{release['name']}`；诊断路径：`{config.diagnostic_path}`，禁止自动切换。
- 模型工件 SHA256：`{release['asset_sha256']}`。
- 配置：上下文 `{config.context_size}`，GPU layers `{config.gpu_layers}`，batch `{config.batch_size}`，ubatch `{config.ubatch_size}`，KV Cache `{config.kv_cache_k}/{config.kv_cache_v}`。
- 组合 GPU 峰值：`{release['combined_gpu_peak_mib']} MiB`，门槛 `< {config.combined_gpu_peak_mib_max} MiB`。
- 热首内容 P95：`{release['warm_first_visible_token_p95_ms']:.2f} ms`。
- 80 token P95：`{release['reply_80_tokens_p95_ms']:.2f} ms`。
- 160 token P95：`{release['reply_160_tokens_p95_ms']:.2f} ms`。
- 记忆选择增量 P95：`{report['memory_selection_incremental_p95_ms']:.2f} ms`。
- 一小时运行：`{report['stability']['minutes']:.2f}` 分钟；事务违规 `{report['stability']['transaction_violations']}`。

## 闸门明细

{check_lines}

## 证据

- 性能：`{evidence['performance']}`
- 组合资源：`{evidence['combined_resource']}`
- 检索：`{evidence['retrieval']}`
- 韧性：`{evidence['resilience']}`
- 一小时：`{evidence['stability']}`

## 边界

本报告只冻结系统工程与发布推理配置，不代表白未晞角色质量或产品正式 P0 已通过。完整 V6 回合端到端延迟单独保留在一小时报告中，不使用内部首 token 冒充玩家可见延迟。
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Finalize the frozen SYS-12 release gate.")
    parser.add_argument("--manifest", default=str(ROOT / "local_runtime" / "sys12_release_manifest.json"))
    parser.add_argument("--performance", default=str(ROOT / "eval" / "world_mind_p0" / "sys12_release_performance_final.json"))
    parser.add_argument("--combined", default=str(ROOT / "eval" / "world_mind_p0" / "sys12_combined_resource_report.json"))
    parser.add_argument("--retrieval", default=str(ROOT / "eval" / "world_mind_p0" / "sys09_retrieval_report.json"))
    parser.add_argument("--resilience", default=str(ROOT / "eval" / "world_mind_p0" / "sys12_release_resilience_report.json"))
    parser.add_argument("--stability-run", default=str(ROOT / "eval" / "world_mind_p0" / "runs_sys12_release"))
    parser.add_argument("--output", default=str(ROOT / "eval" / "world_mind_p0" / "sys12_release_acceptance.json"))
    parser.add_argument("--markdown", default=str(ROOT / "eval" / "world_mind_p0" / "SYS12_发布推理栈资源调度与正式性能报告_20260812.md"))
    return parser


if __name__ == "__main__":
    raise SystemExit(main(_parser().parse_args()))

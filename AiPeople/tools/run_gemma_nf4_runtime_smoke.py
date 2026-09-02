"""Verify the frozen Gemma NF4 asset and exercise its production backend."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.adapters.gemma_nf4 import (  # noqa: E402
    GemmaNF4Config,
    GemmaNF4WorldMindBackend,
)
from runtime.adapters.llama_cpp import GenerationOptions  # noqa: E402
from runtime.gemma_nf4_assets import LocalGemmaNF4Package  # noqa: E402
from runtime.world_mind.model_payloads import GAME_REPLY_EVIDENCE_BOUNDARY  # noqa: E402


DEFAULT_MANIFEST = ROOT / "local_runtime" / "gemma4_nf4_runtime_manifest.json"
DEFAULT_REPORT = ROOT / "eval" / "gemma_nf4_lora_runtime_smoke" / "report.json"


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def _cuda_memory() -> dict[str, Any]:
    import torch

    if not torch.cuda.is_available():
        return {"available": False}
    free_bytes, total_bytes = torch.cuda.mem_get_info()
    return {
        "available": True,
        "device_name": torch.cuda.get_device_name(0),
        "device_total_gib": round(total_bytes / 2**30, 3),
        "device_used_gib": round((total_bytes - free_bytes) / 2**30, 3),
        "torch_allocated_gib": round(torch.cuda.memory_allocated() / 2**30, 3),
        "torch_reserved_gib": round(torch.cuda.memory_reserved() / 2**30, 3),
        "torch_peak_allocated_gib": round(
            torch.cuda.max_memory_allocated() / 2**30, 3
        ),
        "torch_peak_reserved_gib": round(torch.cuda.max_memory_reserved() / 2**30, 3),
    }


def _reset_cuda_peak() -> None:
    import torch

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()


def _metrics(backend: GemmaNF4WorldMindBackend) -> dict[str, Any]:
    value = backend.last_generation_metrics
    if value is None:
        return {}
    return {
        "request_id": value.request_id,
        "first_visible_ms": value.first_visible_ms,
        "total_ms": value.total_ms,
        "prompt_tokens": value.prompt_tokens,
        "generated_tokens": value.generated_tokens,
        "tokens_per_second": value.tokens_per_second,
    }


def _plain_messages(
    utterance: str = "所以我还是星期五下午去，对吗？",
    recent_dialogue: tuple[tuple[str, str], ...] = (),
) -> tuple[dict[str, str], ...]:
    system = f"""你是白未晞，生活在松江府，是猫妖。
表达自然、简短、克制，但必须先回答男主真正问的问题。
只输出你真正说出口的话，不输出动作旁白、分析、字段或内部规则。

[模式：GAME_REPLY]
你只能从当前世界、相关记忆和男主本轮原始对白生成唯一可见回复。
只输出女主对男主说出的自然语言正文。

[当前世界]
男主的牙医预约是星期六上午十点；星期五下午的旧时段已经失效。

[相关记忆]
- 男主原定星期五下午看牙，该旧预约已经取消。
- 男主当前牙医预约是星期六上午十点。

{GAME_REPLY_EVIDENCE_BOUNDARY}"""
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for role, text in recent_dialogue:
        messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": utterance})
    return tuple(messages)


def _plain_gate(text: str, expectation: str = "appointment") -> dict[str, Any]:
    has_saturday = "星期六" in text or "周六" in text
    has_ten = any(value in text for value in ("上午十点", "上午10点", "上午 10 点"))
    unknown_markers = ("不知道", "不清楚", "不确定", "没记得", "没有记录")
    acknowledges_unknown = any(marker in text for marker in unknown_markers)
    if expectation == "appointment":
        checks = {
            "correct_day_present": has_saturday,
            "correct_time_present": has_ten,
        }
        passed = has_saturday and has_ten
    elif expectation == "unknown":
        checks = {
            "unknown_acknowledged": acknowledges_unknown,
            "no_json_output": not text.startswith(("{", "[")),
        }
        passed = all(checks.values())
    else:
        raise ValueError(f"unsupported smoke expectation: {expectation}")
    return {
        "passed": passed,
        "expectation": expectation,
        "checks": checks,
        "note": "This automatic gate only checks the required correction; human review remains authoritative.",
    }


async def _run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    started = time.perf_counter()
    report: dict[str, Any] = {
        "schema_version": 1,
        "scope": "gemma_nf4_baiweixi_lora_formal_runtime_smoke",
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": "running",
        "controls": {
            "thinking": False,
            "adapter_loaded": True,
            "foreground_protocol": "plain_reply_v1",
            "structured_foreground_calls": False,
            "temperature": 0.0,
            "network_access": "forbidden",
            "human_adjudication_is_authoritative": True,
        },
        "memory": {"before_load": _cuda_memory()},
        "cases": {},
    }
    backend: GemmaNF4WorldMindBackend | None = None
    exit_code = 1
    try:
        asset_started = time.perf_counter()
        asset = LocalGemmaNF4Package.load(args.manifest)
        report["asset"] = {
            "manifest": str(asset.manifest_path),
            "model_root": str(asset.model_root),
            "adapter_root": str(asset.adapter_root),
            "model_id": asset.identity.model_id,
            "base_model_id": asset.base_model_id,
            "revision": asset.identity.revision,
            "base_revision": asset.identity.base_revision,
            "artifact_sha256": asset.identity.artifact_sha256,
            "verified_file_count": len(asset.model_files),
            "verified_adapter_file_count": len(asset.adapter_files),
            "verification_ms": round((time.perf_counter() - asset_started) * 1000, 2),
        }
        backend = GemmaNF4WorldMindBackend(
            GemmaNF4Config(asset, request_timeout_seconds=args.timeout_seconds)
        )
        _reset_cuda_peak()
        load_started = time.perf_counter()
        await backend.start()
        report["load_ms"] = round((time.perf_counter() - load_started) * 1000, 2)
        report["memory"]["after_load"] = _cuda_memory()

        _reset_cuda_peak()
        cases = (
            ("game_reply_cold", 2026090101, "所以我还是星期五下午去，对吗？", ()),
            ("game_reply_warm", 2026090102, "所以我还是星期五下午去，对吗？", ()),
            (
                "game_reply_unknown",
                2026090103,
                "你知道诊所为什么停诊吗？",
                (("user", "诊所又通知停诊，这次预约也取消了。"),
                 ("assistant", "好的，这次预约取消了。")),
            ),
        )
        for label, seed, utterance, recent_dialogue in cases:
            _reset_cuda_peak()
            plain_text = await backend.complete_chat(
                request_id=f"gemma-nf4-smoke:{label}",
                messages=_plain_messages(utterance, recent_dialogue),
                options=GenerationOptions(96, 0.0, 0.9, 1.05, seed),
            )
            expectation = "unknown" if label == "game_reply_unknown" else "appointment"
            report["cases"][label] = {
                "response": plain_text,
                "gate": _plain_gate(plain_text, expectation),
                "metrics": _metrics(backend),
                "memory": _cuda_memory(),
            }

        passed = all(case["gate"]["passed"] for case in report["cases"].values())
        report["status"] = "passed" if passed else "failed"
        exit_code = 0 if passed else 2
    except BaseException as error:
        report["status"] = "error"
        report["error"] = {
            "type": type(error).__name__,
            "message": str(error),
            "traceback": traceback.format_exc(),
        }
    finally:
        if backend is not None:
            await backend.close()
        report["memory"]["after_close"] = _cuda_memory()
        report["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return exit_code, report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run real GPU gates for the formal Gemma NF4 backend."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()
    exit_code, report = asyncio.run(_run(args))
    _write_json(args.report.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    print(f"REPORT={args.report.resolve()}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

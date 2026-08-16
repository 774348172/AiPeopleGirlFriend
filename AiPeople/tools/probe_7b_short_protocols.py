from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.adapters import GenerationOptions
from runtime.adapters.llama_cpp import LlamaCppError
from runtime.world_mind.short_protocol import (
    B1_SYSTEM_BOUNDARY,
    M2_SYSTEM_BOUNDARY,
    R2_SYSTEM_BOUNDARY,
)
from runtime.world_mind.sys12 import Sys12ReleaseConfig, Sys12ReleaseHost


def _schema(name: str) -> dict[str, object]:
    value = json.loads(
        (ROOT / "runtime" / "schemas" / name).read_text(encoding="utf-8")
    )
    if not isinstance(value, dict):
        raise TypeError(f"invalid schema: {name}")
    return value


async def _structured_probe(
    host: Sys12ReleaseHost,
    *,
    mode: str,
    boundary: str,
    payload: dict[str, object],
    schema_name: str,
    max_tokens: int,
) -> dict[str, object]:
    started = time.perf_counter()
    try:
        raw = await host.backend.complete_chat(
            request_id=f"7b-compat:{mode}",
            messages=(
                {"role": "system", "content": boundary},
                {
                    "role": "user",
                    "content": json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":")
                    ),
                },
            ),
            options=GenerationOptions(max_tokens, 0.15, 0.85, 1.05, seed=6112026),
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": mode.lower(),
                    "strict": True,
                    "schema": _schema(schema_name),
                },
            },
        )
        parsed = json.loads(raw)
        stats = host.raw_backend.last_generation_stats
        return {
            "passed": isinstance(parsed, dict),
            "output": parsed,
            "generated_tokens": None if stats is None else stats.generated_tokens,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except (LlamaCppError, ValueError, TimeoutError) as error:
        return {
            "passed": False,
            "error_type": type(error).__name__,
            "error": str(error),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }


async def _game_reply_probe(host: Sys12ReleaseHost) -> dict[str, object]:
    started = time.perf_counter()
    try:
        output = await host.backend.complete_chat(
            request_id="7b-compat:GAME_REPLY",
            messages=(
                {
                    "role": "system",
                    "content": (
                        "你是白未晞，生活在松江府的成年猫妖少女。"
                        "自然、简短地回答眼前男主，只输出你说的话。"
                    ),
                },
                {"role": "user", "content": "你是谁？"},
            ),
            options=GenerationOptions(360, 0.55, 0.9, 1.08, seed=6112026),
            response_format=None,
        )
        stats = host.raw_backend.last_generation_stats
        forbidden_fragments = tuple(
            item
            for item in ("assistant", "assis", "<|im_start|>", "<|im_end|>")
            if item in output.lower()
        )
        quality_passed = len(output) <= 300 and not forbidden_fragments
        return {
            "passed": quality_passed,
            "transport_passed": True,
            "quality_passed": quality_passed,
            "forbidden_fragments": list(forbidden_fragments),
            "output": output,
            "generated_tokens": None if stats is None else stats.generated_tokens,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except (LlamaCppError, ValueError, TimeoutError) as error:
        return {
            "passed": False,
            "transport_passed": False,
            "quality_passed": False,
            "error_type": type(error).__name__,
            "error": str(error),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }


async def main() -> int:
    config = Sys12ReleaseConfig.load(
        ROOT / "local_runtime" / "sys12_release_manifest.json"
    )
    host = Sys12ReleaseHost(config)
    state = [
        "人形，猫耳和尾巴未隐藏",
        "外伤恢复大半",
        "平静但仍有戒备",
        "注意眼前的男主",
        "坐在出租屋餐桌旁",
        "回应男主",
        "暂时共同生活",
        "已有初步信任",
        "担心伤好后失去留下的理由",
    ]
    world = [
        "D11 18:00",
        "出租屋餐桌旁",
        "出租屋餐桌旁",
        "吃面",
        "fatigue=轻微疲惫；injury=无",
        "窗外仍在下雨",
    ]
    try:
        await host.start()
        results = {
            "MIND_PATCH_V2": await _structured_probe(
                host,
                mode="MIND_PATCH_V2",
                boundary=M2_SYSTEM_BOUNDARY,
                payload={"p": "M2", "s": state, "w": world, "u": "我刚忙完。"},
                schema_name="mind_patch_m2.schema.json",
                max_tokens=180,
            ),
            "B1": await _structured_probe(
                host,
                mode="B1",
                boundary=B1_SYSTEM_BOUNDARY,
                payload={
                    "p": "B1",
                    "q": 1,
                    "s": state,
                    "w": world,
                    "u": [0, 0],
                    "x": ["我刚忙完。", "辛苦了，先把面吃完。", []],
                },
                schema_name="background_mind_patch_b1.schema.json",
                max_tokens=320,
            ),
            "R2": await _structured_probe(
                host,
                mode="R2",
                boundary=R2_SYSTEM_BOUNDARY,
                payload={
                    "p": "R2",
                    "n": "D11 18:00",
                    "e": [
                        [0, 1, "我刚忙完。"],
                        [1, 2, "辛苦了，先把面吃完。"],
                    ],
                },
                schema_name="heroine_memory_r2.schema.json",
                max_tokens=180,
            ),
            "GAME_REPLY": await _game_reply_probe(host),
        }
    finally:
        await host.close()
    report = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(),
        "model_sha256": config.model_sha256,
        "runtime_compatibility": {
            "chat_template": config.chat_template,
            "stop_sequences": list(config.stop_sequences),
            "structured_output": config.structured_output,
        },
        "results": results,
    }
    output = (
        ROOT / "eval" / "world_mind_p0" / "7b_runtime_compatibility_probe_20260814.json"
    )
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(output), **report}, ensure_ascii=False, indent=2))
    return (
        0
        if all(results[name]["passed"] for name in ("MIND_PATCH_V2", "B1", "R2"))
        else 1
    )


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main()))

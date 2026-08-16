from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.adapters import GenerationOptions
from runtime.world_mind.sys12 import Sys12ReleaseConfig, Sys12ReleaseHost


async def main() -> int:
    config = Sys12ReleaseConfig.load(ROOT / "local_runtime" / "sys12_release_manifest.json")
    host = Sys12ReleaseHost(config)
    try:
        await host.start()
        reply = await host.backend.complete_chat(
            request_id="sys12:GAME_REPLY:plain-text-v2",
            messages=(
                {
                    "role": "system",
                    "content": "只输出一句自然语言对白，不输出 JSON 或说明。",
                },
                {"role": "user", "content": "你是谁？"},
            ),
            options=GenerationOptions(64, 0.1, 0.8, 1.05, seed=6112026),
            response_format=None,
        )
    finally:
        await host.close()
    print(
        json.dumps(
            {
                "protocol": "GAME_REPLY plain text v2",
                "response_format": None,
                "reply": reply,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

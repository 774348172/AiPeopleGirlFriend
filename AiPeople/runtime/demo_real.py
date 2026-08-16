from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import Completed, Failed, RelationshipRuntime, RuntimeConfig, TextDelta, UserMessage
from .adapters import LlamaCppConfig, LlamaCppReplyModel


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the real local Qin Weixi model")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("local_runtime/model_runtime_manifest.json"),
    )
    parser.add_argument(
        "--data-dir", type=Path, default=Path("local_runtime/demo_real_data")
    )
    parser.add_argument("--port", type=int)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    model_config = LlamaCppConfig.from_manifest(args.config, port=args.port)
    model = LlamaCppReplyModel(model_config)
    timezone = ZoneInfo("Asia/Shanghai")

    print("正在校验资产并加载本地模型，请稍候……", flush=True)
    async with RelationshipRuntime.open(RuntimeConfig(args.data_dir), model) as runtime:
        print("秦未晞文字调试入口已启动。输入 /quit 退出。", flush=True)
        while True:
            try:
                text = await asyncio.to_thread(input, "你> ")
            except (EOFError, KeyboardInterrupt):
                break
            if text.strip().lower() in {"/quit", "quit", "exit", "q"}:
                break

            message = UserMessage(
                request_id=str(uuid.uuid4()),
                conversation_id="real-demo",
                text=text,
                occurred_at=datetime.now(timezone),
                timezone="Asia/Shanghai",
            )
            print("秦未晞> ", end="", flush=True)
            async for event in runtime.handle_turn(message):
                if isinstance(event, TextDelta):
                    print(event.text, end="", flush=True)
                elif isinstance(event, Completed):
                    metrics = event.metrics
                    print(
                        f"\n[完成 首字={metrics.model_first_delta_ms:.1f}ms "
                        f"总计={metrics.total_ms:.1f}ms]"
                    )
                elif isinstance(event, Failed):
                    print(f"\n[失败 {event.code}，可重试={event.retryable}]")


if __name__ == "__main__":
    asyncio.run(main())

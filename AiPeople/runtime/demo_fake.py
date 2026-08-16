from __future__ import annotations

import asyncio
import argparse
import logging
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import Completed, Failed, RelationshipRuntime, RuntimeConfig, TextDelta, UserMessage
from .adapters import FakeReplyModel


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data/runtime_demo"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = RuntimeConfig(args.data_dir)
    model = FakeReplyModel(["这是 ", "FakeModel ", "的流式回复。"], delay_seconds=0.15)
    timezone = ZoneInfo("Asia/Shanghai")

    async with RelationshipRuntime.open(config, model) as runtime:
        print("FakeModel 运行时已启动。输入 quit 退出。")
        while True:
            try:
                text = input("你> ")
            except (EOFError, KeyboardInterrupt):
                break
            if text.strip().lower() in {"quit", "exit", "q"}:
                break

            message = UserMessage(
                request_id=str(uuid.uuid4()),
                conversation_id="demo",
                text=text,
                occurred_at=datetime.now(timezone),
                timezone="Asia/Shanghai",
            )
            print("秦未晞[Fake]> ", end="", flush=True)
            async for event in runtime.handle_turn(message):
                if isinstance(event, TextDelta):
                    print(event.text, end="", flush=True)
                elif isinstance(event, Completed):
                    print(f"\n[完成 {event.metrics.total_ms:.1f}ms]")
                elif isinstance(event, Failed):
                    print(f"\n[失败 {event.code}]")


if __name__ == "__main__":
    asyncio.run(main())

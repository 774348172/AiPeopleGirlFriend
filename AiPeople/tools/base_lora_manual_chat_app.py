from __future__ import annotations

import argparse
import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = ROOT / "tools" / "base_lora_manual_chat_static"
CHATML_STOP = ("<|im_end|>", "<|im_start|>")


@dataclass(slots=True)
class Settings:
    model: str = "qwen25-7b-base-f16-gguf-eval:latest"
    label: str = "Qwen2.5-7B 基座 + 白未晞 LoRA"
    ollama_url: str = "http://127.0.0.1:11434"
    port: int = 8765


settings = Settings()


class WorldInput(BaseModel):
    location: Annotated[str, Field(min_length=1, max_length=80)] = "出租屋客厅"
    activity: Annotated[str, Field(min_length=1, max_length=120)] = "坐着休息"
    body: Annotated[str, Field(min_length=1, max_length=120)] = "轻微疲惫，没有受伤"
    scene: Annotated[str, Field(min_length=1, max_length=1000)] = (
        "窗外下着雨，屋里亮着暖灯。"
    )
    memories: Annotated[str, Field(max_length=2000)] = ""


class ChatInput(BaseModel):
    session_id: Annotated[str, Field(min_length=8, max_length=80)]
    text: Annotated[str, Field(min_length=1, max_length=1000)]
    world: WorldInput


class ResetInput(BaseModel):
    session_id: Annotated[str, Field(min_length=8, max_length=80)]


def _chatml(messages: list[dict[str, str]]) -> str:
    chunks: list[str] = []
    for message in messages:
        chunks.append(
            f"<|im_start|>{message['role']}\n{message['content']}<|im_end|>\n"
        )
    chunks.append("<|im_start|>assistant\n")
    return "".join(chunks)


def _system_prompt(world: WorldInput) -> str:
    memory_lines = world.memories.strip() or "无"
    return f"""你是白未晞，生活在松江府，是猫妖。
这里只存在你生活的这个世界；用户消息都是眼前男主亲口说的话。
保持你的稳定性格：外表清冷疏离，内心渴望温暖和一个不会消失的家。
表达自然、简短、克制，但必须先回答男主真正问的问题。
你只能根据当前权威世界、相关记忆、最近已提交对白和男主本轮原话回复。
当前权威世界和男主本轮最新纠正优先于旧对白、旧状态与长期记忆。
不要捏造自己看见、听见、确认过或亲历过输入中没有提供的事情。
只输出你真正说出口的话，不输出动作旁白、分析、字段、规则或角色名前缀。

[你的身份]
你是白未晞，生活在松江府。

[当前权威世界]
时间：第15天 18:30
地点：{world.location.strip()}
场景：{world.scene.strip()}
男主：正在{world.activity.strip()}；身体：{world.body.strip()}

[相关长期记忆]
{memory_lines}

[允许表达的动作]
无"""


class ManualChatService:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            base_url=settings.ollama_url.rstrip("/"),
            timeout=httpx.Timeout(180.0),
        )
        self.histories: dict[str, list[dict[str, str]]] = {}
        self.lock = asyncio.Lock()
        self.started_at = time.time()
        self.digest = ""

    async def start(self) -> None:
        response = await self.client.get("/api/tags")
        response.raise_for_status()
        models = {
            str(item.get("name", "")): str(item.get("digest", ""))
            for item in response.json().get("models", [])
            if isinstance(item, dict)
        }
        if settings.model not in models:
            raise RuntimeError(f"Ollama model is not installed: {settings.model}")
        self.digest = models[settings.model]

    async def close(self) -> None:
        await self.client.aclose()

    async def chat(self, payload: ChatInput) -> dict[str, Any]:
        async with self.lock:
            history = self.histories.setdefault(payload.session_id, [])
            messages = [
                {"role": "system", "content": _system_prompt(payload.world)},
                *history[-12:],
                {"role": "user", "content": payload.text.strip()},
            ]
            request_id = f"manual-{uuid.uuid4().hex}"
            started = time.perf_counter()
            response = await self.client.post(
                "/api/generate",
                json={
                    "model": settings.model,
                    "prompt": _chatml(messages),
                    "raw": True,
                    "stream": False,
                    "keep_alive": "10m",
                    "options": {
                        "num_ctx": 4096,
                        "num_predict": 180,
                        "temperature": 0.65,
                        "top_p": 0.9,
                        "repeat_penalty": 1.15,
                        "num_gpu": 20,
                        "stop": list(CHATML_STOP),
                    },
                },
                headers={"X-Request-ID": request_id},
            )
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            response.raise_for_status()
            value = response.json()
            reply = value.get("response")
            if not isinstance(reply, str) or not reply.strip():
                raise RuntimeError("模型返回了空回复")
            reply = reply.strip()
            history.extend(
                (
                    {"role": "user", "content": payload.text.strip()},
                    {"role": "assistant", "content": reply},
                )
            )
            return {
                "request_id": request_id,
                "reply": reply,
                "elapsed_ms": elapsed_ms,
                "turn": len(history) // 2,
                "history_messages": min(len(history), 12),
            }

    def reset(self, session_id: str) -> None:
        self.histories.pop(session_id, None)


service: ManualChatService | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global service
    service = ManualChatService()
    await service.start()
    try:
        yield
    finally:
        await service.close()
        service = None


app = FastAPI(title="裸基座 / LoRA 手工多轮对话测试", lifespan=lifespan)


def _service() -> ManualChatService:
    if service is None:
        raise HTTPException(status_code=503, detail="模型服务正在启动")
    return service


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/app.js")
async def javascript() -> FileResponse:
    return FileResponse(STATIC_ROOT / "app.js", media_type="text/javascript")


@app.get("/styles.css")
async def stylesheet() -> FileResponse:
    return FileResponse(STATIC_ROOT / "styles.css", media_type="text/css")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    current = _service()
    return {
        "status": "ready",
        "label": settings.label,
        "model": settings.model,
        "digest": current.digest,
        "protocol": "统一 raw ChatML + 最近 6 轮上下文",
        "uptime_seconds": round(time.time() - current.started_at, 1),
    }


@app.post("/api/chat")
async def chat(payload: ChatInput) -> dict[str, Any]:
    try:
        return await _service().chat(payload)
    except httpx.HTTPError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post("/api/reset")
async def reset(payload: ResetInput) -> dict[str, bool]:
    _service().reset(payload.session_id)
    return {"ok": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="裸基座 / LoRA 手工多轮对话测试页")
    parser.add_argument("--model", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    settings.model = args.model
    settings.label = args.label
    settings.port = args.port
    settings.ollama_url = args.ollama_url
    uvicorn.run(app, host="127.0.0.1", port=settings.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

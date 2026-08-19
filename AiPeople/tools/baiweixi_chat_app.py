from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from runtime._real_assets import LocalRetrievalAssetBundle
from runtime.contracts import Completed, Failed
from runtime.world_mind import (
    ActiveSceneState,
    CharacterPackagePromptComposer,
    GameClockService,
    LlamaCppHeroineMemoryProposer,
    LlamaCppWorldMindModel,
    PersistentWorldStateProvider,
    ProtagonistLiveState,
    RuntimeSessionIdentity,
    TurnRequest,
    WorldMindModelIdentity,
    WorldMindModeProfile,
    WorldMindRuntime,
    WorldMindRuntimeConfig,
    WorldMindStore,
)
from runtime.world_mind.model_gateway import (
    FIVE_MINUTE_WORLD_MIND_RECONCILE,
    GAME_REPLY,
    MIND_PATCH_V2,
    POST_REPLY_WORLD_MIND_RECONCILE,
    WORLD_CONTINUITY_REVIEW,
)
from runtime.world_mind.real_stack import build_real_retrieval_stack
from runtime.world_mind.sys12 import Sys12ReleaseConfig, Sys12ReleaseHost

STATIC_ROOT = ROOT / "tools" / "baiweixi_chat_static"
DATA_ROOT = ROOT / "eval" / "world_mind_p0" / "interactive_chat"
DATABASE_PATH = DATA_ROOT / "baiweixi_interactive.sqlite3"
RELEASE_MANIFEST = ROOT / "local_runtime" / "sys12_release_manifest.json"
RETRIEVAL_MANIFEST = (
    ROOT / "local_runtime" / "models" / "retrieval" / "retrieval_assets.json"
)
INITIAL_GAME_TIME = datetime(1, 10, 11, 18, 0, 0)
SAVE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")


class WorldInput(BaseModel):
    location_id: Annotated[str, Field(min_length=1, max_length=64)] = "apartment_table"
    location_label: Annotated[str, Field(min_length=1, max_length=80)] = "出租屋餐桌旁"
    activity: Annotated[str, Field(min_length=1, max_length=100)] = "坐着休息"
    body: Annotated[str, Field(min_length=1, max_length=120)] = "有些疲惫，没有受伤"
    held_item: Annotated[str, Field(max_length=64)] = ""
    scene: Annotated[str, Field(min_length=1, max_length=160)] = "窗外下着雨，屋里亮着暖灯"


class ChatInput(BaseModel):
    save_id: str
    text: Annotated[str, Field(min_length=1, max_length=1000)]
    world: WorldInput


class SaveInput(BaseModel):
    save_id: str
    world: WorldInput | None = None


def _runtime_config() -> WorldMindRuntimeConfig:
    return WorldMindRuntimeConfig(
        expected_world_id="songjiangfu",
        expected_protagonist_id="protagonist",
        world_canon_dir=ROOT / "世界设定" / "松江府",
        protagonist_canon_dir=ROOT / "人物设定" / "主角",
        character_package_dirs={"baiweixi": ROOT / "人物设定" / "白未晞"},
        p0_allowed_character_ids=("baiweixi",),
        foreground_protocol="mind_patch_v2",
    )


def _session(save_id: str) -> RuntimeSessionIdentity:
    if SAVE_ID_PATTERN.fullmatch(save_id) is None:
        raise HTTPException(status_code=400, detail="存档 ID 不合法")
    return RuntimeSessionIdentity(
        save_id=save_id,
        world_id="songjiangfu",
        protagonist_id="protagonist",
        active_character_id="baiweixi",
        conversation_id=f"{save_id}_baiweixi",
    )


def _profiles(config: Sys12ReleaseConfig) -> dict[str, WorldMindModeProfile]:
    values = {
        MIND_PATCH_V2: (180, 0.2, 0.85, 1.05, 1),
        GAME_REPLY: (360, 0.55, 0.9, 1.08, 1),
        WORLD_CONTINUITY_REVIEW: (650, 0.1, 0.8, 1.05, 1),
        POST_REPLY_WORLD_MIND_RECONCILE: (320, 0.2, 0.85, 1.05, 0),
        FIVE_MINUTE_WORLD_MIND_RECONCILE: (320, 0.2, 0.85, 1.05, 0),
    }
    return {
        mode: WorldMindModeProfile(
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            repeat_penalty=repeat_penalty,
            timeout_seconds=config.world_mind_mode_timeouts_seconds[mode],
            retries=retries,
        )
        for mode, (
            max_tokens,
            temperature,
            top_p,
            repeat_penalty,
            retries,
        ) in values.items()
    }


class BaiWeixiChatService:
    def __init__(self) -> None:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.config = _runtime_config()
        self.store = WorldMindStore.open(DATABASE_PATH)
        self.clock = GameClockService(
            self.store,
            initial_game_time=INITIAL_GAME_TIME,
        )
        self.world = PersistentWorldStateProvider(self.store)
        release = Sys12ReleaseConfig.load(RELEASE_MANIFEST)
        self.host = Sys12ReleaseHost(release)
        retrieval_assets = LocalRetrievalAssetBundle.load(RETRIEVAL_MANIFEST)
        self.retrieval = build_real_retrieval_stack(
            store=self.store,
            assets=retrieval_assets,
        )
        identity = WorldMindModelIdentity(
            model_id="llama_cpp/baiweixi-release-interactive",
            revision="sys12-instruct-interactive-v1",
            artifact_sha256=release.model_sha256,
            character_id="baiweixi",
            world_id="songjiangfu",
            protagonist_id="protagonist",
        )
        self.model = LlamaCppWorldMindModel(
            self.host.backend,
            identity=identity,
            mode_profiles=_profiles(release),
        )
        prompt = CharacterPackagePromptComposer(self.config).compose(
            _session("interactive_seed")
        )
        self.memory_proposer = LlamaCppHeroineMemoryProposer(
            self.host.backend,
            identity=identity,
            character_prompt=prompt.system_prompt,
        )
        self.runtime = WorldMindRuntime(
            config=self.config,
            store=self.store,
            world_state_provider=self.world,
            game_clock=self.clock,
            model=self.model,
            memory_repository_factory=self.retrieval.memory_repository_factory,
            memory_proposer_provider=lambda _session: self.memory_proposer,
            periodic_reconcile_seconds=300.0,
            required_reconcile_before_foreground=False,
            coalesce_pending_required_reconcile=True,
            close_store_on_close=True,
        )
        self.send_lock = asyncio.Lock()
        self.started_at = time.time()

    async def start(self) -> None:
        await self.runtime.start()

    async def close(self) -> None:
        await self.runtime.close()
        await self.host.gate.close()

    async def ensure_world(
        self,
        session: RuntimeSessionIdentity,
        world_input: WorldInput | None = None,
    ) -> None:
        if self.store.load_live_world(session) is not None and world_input is None:
            return
        value = world_input or WorldInput()
        # held_item 是面向测试者的自由文本（可能是中文），held_item_ids 要求
        # 小写标识符：文本转 slug，空文本保持空元组。
        held_text = value.held_item.strip()
        held = (
            (re.sub(r"[^a-z0-9_]", "_", held_text.lower()).strip("_") or "item",)
            if held_text
            else ()
        )
        await self.world.update_latest(
            session,
            ProtagonistLiveState(
                protagonist_id="protagonist",
                location_id=value.location_id.strip(),
                location_label=value.location_label.strip(),
                activity=value.activity.strip(),
                body_state={"当前状态": value.body.strip()},
                held_item_ids=held,
            ),
            ActiveSceneState(
                scene_id=value.location_id.strip(),
                location_label=value.location_label.strip(),
                present_character_ids=("protagonist", "baiweixi"),
                item_states={"场景": value.scene.strip()},
            ),
            self.clock.current_time(session),
        )

    async def chat(self, payload: ChatInput) -> dict[str, Any]:
        session = _session(payload.save_id)
        async with self.send_lock:
            await self.ensure_world(session, payload.world)
            request_id = f"interactive_{uuid.uuid4().hex}"
            wall_started = time.perf_counter()
            result = await self.runtime.handle_turn(
                TurnRequest(
                    request_id=request_id,
                    session=session,
                    text=payload.text.strip(),
                )
            )
            visible_ms = (time.perf_counter() - wall_started) * 1000
        if isinstance(result, Failed):
            raise HTTPException(
                status_code=503,
                detail={"code": result.code, "retryable": result.retryable},
            )
        if not isinstance(result, Completed):
            raise HTTPException(status_code=500, detail="未知回合结果")
        return {
            "request_id": request_id,
            "reply": result.text,
            "visible_ms": round(visible_ms, 2),
            "runtime_total_ms": round(result.metrics.total_ms, 2),
            "model_ms": round(result.metrics.model_total_ms, 2),
            "commit_ms": round(result.metrics.ledger_reply_commit_ms, 2),
            "status": self.status(session),
        }

    def status(self, session: RuntimeSessionIdentity) -> dict[str, Any]:
        live = self.store.load_live_world(session)
        runtime = self.store.load_heroine_runtime(session)
        current_time = self.clock.current_time(session)
        result: dict[str, Any] = {
            "save_id": session.save_id,
            "game_time": f"第{current_time.day}日 {current_time:%H:%M:%S}",
            "reconcile_queue": self.store.reconcile_queue_snapshot(session.save_id),
            "memory_queue": self.store.r1_memory_queue_snapshot(session.save_id),
        }
        if live is not None:
            result["world"] = {
                "version": live.version,
                "location_id": live.protagonist.location_id,
                "location_label": live.protagonist.location_label,
                "activity": live.protagonist.activity,
                "body": next(iter(live.protagonist.body_state.values()), ""),
                "held_item": "、".join(live.protagonist.held_item_ids),
                "scene": next(iter(live.scene.item_states.values()), ""),
            }
        if runtime is not None:
            result["heroine"] = {
                "version": runtime.version,
                "living_mind": asdict(runtime.living_mind),
                "relationship": asdict(runtime.relationship),
            }
        return result


service: BaiWeixiChatService | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global service
    service = BaiWeixiChatService()
    await service.start()
    try:
        yield
    finally:
        await service.close()
        service = None


app = FastAPI(title="白未晞 V6 对话测试", lifespan=lifespan)


def _service() -> BaiWeixiChatService:
    if service is None:
        raise HTTPException(status_code=503, detail="模型正在启动")
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
        "model": "白未晞 7B Instruct Q5_K_M",
        "protocol": "M2 mind patch + plain GAME_REPLY",
        "uptime_seconds": round(time.time() - current.started_at, 1),
    }


@app.post("/api/status")
async def status(payload: SaveInput) -> dict[str, Any]:
    current = _service()
    session = _session(payload.save_id)
    await current.ensure_world(session, payload.world)
    return current.status(session)


@app.post("/api/chat")
async def chat(payload: ChatInput) -> dict[str, Any]:
    return await _service().chat(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="白未晞 V6 真实模型对话测试页")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

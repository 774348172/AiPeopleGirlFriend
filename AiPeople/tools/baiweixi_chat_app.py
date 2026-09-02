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
from runtime.gemma_nf4_assets import GEMMA4_ADAPTER_IDS, LocalGemmaNF4Package
from runtime.world_mind import (
    ActiveSceneState,
    GameClockService,
    PersistentWorldStateProvider,
    ProtagonistLiveState,
    RuntimeSessionIdentity,
    TurnRequest,
    WorldMindModeProfile,
    WorldMindRuntime,
    WorldMindRuntimeConfig,
    WorldMindStore,
)
from runtime.world_mind.model_gateway import (
    GAME_REPLY,
    JUDGE_TURN,
)
from runtime.world_mind.gemma_nf4_stack import build_gemma_nf4_world_mind_stack

STATIC_ROOT = ROOT / "tools" / "baiweixi_chat_static"
DATA_ROOT = ROOT / "eval" / "world_mind_p0" / "interactive_chat"
GEMMA_PRIMARY_MANIFEST = ROOT / "local_runtime" / "gemma4_nf4_runtime_manifest.json"
GEMMA_FALLBACK_MANIFEST = ROOT / "local_runtime" / "gemma4_nf4_fallback_manifest.json"
GEMMA_MODEL_MANIFESTS = {
    "primary": GEMMA_PRIMARY_MANIFEST,
    "fallback": GEMMA_FALLBACK_MANIFEST,
}
RETRIEVAL_MANIFEST = (
    ROOT / "local_runtime" / "models" / "retrieval" / "retrieval_assets.json"
)
INITIAL_GAME_TIME = datetime(1, 10, 11, 18, 0, 0)
SAVE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")
APP_MODEL_PROFILE = "primary"
APP_DATABASE_PATH = DATA_ROOT / "baiweixi_gemma4_interactive.sqlite3"
# This process owns the only Gemma instance used for player-visible replies.
# Model-based maintenance is durably queued and drained outside live chat.
INTERACTIVE_BACKGROUND_EXECUTION_ENABLED = False


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
        # Full-mixed LoRA is trained for natural dialogue, not state JSON.
        # World state stays program-owned; the model receives it as evidence.
        foreground_protocol="plain_reply_v1",
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


def _gemma_profiles() -> dict[str, WorldMindModeProfile]:
    # The production 60-turn review used greedy decoding, max_new_tokens=96,
    # and repetition_penalty=1.05. Keep the foreground policy identical.
    return {
        GAME_REPLY: WorldMindModeProfile(
            max_tokens=96,
            temperature=0.0,
            top_p=0.9,
            repeat_penalty=1.05,
            timeout_seconds=90.0,
            retries=0,
        ),
        JUDGE_TURN: WorldMindModeProfile(
            max_tokens=96,
            temperature=0.0,
            top_p=0.9,
            repeat_penalty=1.05,
            timeout_seconds=90.0,
            retries=0,
        )
    }


class BaiWeixiChatService:
    def __init__(
        self,
        *,
        model_profile: str = "primary",
        database_path: Path = APP_DATABASE_PATH,
    ) -> None:
        try:
            model_manifest = GEMMA_MODEL_MANIFESTS[model_profile]
        except KeyError as error:
            raise ValueError(f"unknown model profile: {model_profile}") from error
        database_path = database_path.resolve()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.model_profile = model_profile
        self.model_manifest = model_manifest
        self.config = _runtime_config()
        self.store = WorldMindStore.open(database_path)
        self.clock = GameClockService(
            self.store,
            initial_game_time=INITIAL_GAME_TIME,
        )
        self.world = PersistentWorldStateProvider(self.store)
        retrieval_assets = LocalRetrievalAssetBundle.load(RETRIEVAL_MANIFEST)
        self.reply_asset = LocalGemmaNF4Package.load(model_manifest)
        self.gemma_stack = build_gemma_nf4_world_mind_stack(
            store=self.store,
            retrieval_assets=retrieval_assets,
            reply_asset=self.reply_asset,
            world_mind_config=self.config,
            character_id="baiweixi",
            mode_profiles=_gemma_profiles(),
        )
        self.model = self.gemma_stack.world_mind_model
        self.memory_proposer = self.gemma_stack.memory_proposer
        self.runtime = WorldMindRuntime(
            config=self.config,
            store=self.store,
            world_state_provider=self.world,
            game_clock=self.clock,
            model=self.model,
            memory_repository_factory=self.gemma_stack.memory_repository_factory,
            memory_proposer_provider=lambda _session: self.memory_proposer,
            periodic_reconcile_seconds=300.0,
            required_reconcile_before_foreground=False,
            coalesce_pending_required_reconcile=True,
            background_execution_enabled=INTERACTIVE_BACKGROUND_EXECUTION_ENABLED,
            close_store_on_close=True,
        )
        self.send_lock = asyncio.Lock()
        self.started_at = time.time()

    async def start(self) -> None:
        await self.runtime.start()

    async def close(self) -> None:
        await self.runtime.close()

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
    service = BaiWeixiChatService(
        model_profile=APP_MODEL_PROFILE,
        database_path=APP_DATABASE_PATH,
    )
    await service.start()
    try:
        yield
    finally:
        await service.close()
        service = None


app = FastAPI(title="白未晞正式对话", lifespan=lifespan)


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
        "model": current.reply_asset.identity.model_id,
        "adapter_id": GEMMA4_ADAPTER_IDS[current.reply_asset.identity.model_id],
        "adapter_revision": current.reply_asset.identity.revision,
        "base_model": current.reply_asset.base_model_id,
        "revision": current.reply_asset.identity.revision,
        "model_profile": current.model_profile,
        "protocol": "plain_reply_v1 with program-owned world state",
        "background_execution": (
            "enabled"
            if current.runtime.background_scheduler.execution_enabled
            else "queued_only"
        ),
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
    global APP_MODEL_PROFILE, APP_DATABASE_PATH
    parser = argparse.ArgumentParser(description="白未晞正式对话服务")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument(
        "--model-profile",
        choices=tuple(GEMMA_MODEL_MANIFESTS),
        default="primary",
    )
    parser.add_argument("--database-path", type=Path, default=APP_DATABASE_PATH)
    args = parser.parse_args()
    APP_MODEL_PROFILE = args.model_profile
    APP_DATABASE_PATH = args.database_path.resolve()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

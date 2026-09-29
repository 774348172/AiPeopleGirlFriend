from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
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
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator

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
from runtime.world_mind.gemma_nf4_stack import build_gemma_nf4_world_mind_stack
from runtime.world_mind.model_gateway import (
    GAME_REPLY,
    JUDGE_TURN,
)

from runtime.world_mind.unity_snapshot import UnityGameSnapshot, SubmittedGameWorld
from runtime.world_mind.unity_freshness import UnityFreshness, SnapshotRejected

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
LAUNCHER_SHUTDOWN_TOKEN_ENV = "AIPEOPLE_SHUTDOWN_TOKEN"
LAUNCHER_SHUTDOWN_HEADER = "X-AiPeople-Shutdown-Token"


def _portable_data_root() -> Path:
    configured = os.environ.get("AIPEOPLE_DATA_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "AiPeople" / "data"
    return Path.home() / ".aipeople" / "data"


def _configure_asset_root(asset_root: Path) -> None:
    global GEMMA_PRIMARY_MANIFEST, GEMMA_FALLBACK_MANIFEST
    global GEMMA_MODEL_MANIFESTS, RETRIEVAL_MANIFEST
    root = asset_root.expanduser().resolve()
    GEMMA_PRIMARY_MANIFEST = root / "gemma4_nf4_runtime_manifest.json"
    GEMMA_FALLBACK_MANIFEST = root / "gemma4_nf4_fallback_manifest.json"
    GEMMA_MODEL_MANIFESTS = {
        "primary": GEMMA_PRIMARY_MANIFEST,
        "fallback": GEMMA_FALLBACK_MANIFEST,
    }
    RETRIEVAL_MANIFEST = root / "retrieval" / "retrieval_assets.json"


def _canon_root() -> Path:
    local = ROOT / "人物设定"
    if local.is_dir():
        return local
    repository_root = ROOT.parent / "人物设定"
    if repository_root.is_dir():
        return repository_root
    return local


class SceneFactInput(BaseModel):
    label: Annotated[str, Field(min_length=1, max_length=40)]
    value: Annotated[str, Field(min_length=1, max_length=160)]

    @model_validator(mode="after")
    def validate_text(self):
        self.label = self.label.strip()
        self.value = self.value.strip()
        if (not self.label or not self.value or self.label.startswith(('设备:', '实体:'))
                or self.label in {"场景", "男主手持物", "本轮游戏状态变化", "游戏快照版本",
                                  "游戏状态来源", "游戏时间状态", "当前动作", "最近动作回执",
                                  "男主实际位置", "白未晞实际位置", "男主实际移动"}):
            raise ValueError("scene fact is blank or uses a reserved label")
        return self


class WorldInput(BaseModel):
    location_id: Annotated[str, Field(min_length=1, max_length=64)] = "apartment_table"
    location_label: Annotated[str, Field(min_length=1, max_length=80)] = "出租屋餐桌旁"
    activity: Annotated[str, Field(min_length=1, max_length=100)] = "坐着休息"
    body: Annotated[str, Field(min_length=1, max_length=120)] = "有些疲惫，没有受伤"
    held_item: Annotated[str, Field(max_length=64)] = ""
    scene: Annotated[str, Field(min_length=1, max_length=160)] = "窗外下着雨，屋里亮着暖灯"
    scene_facts: Annotated[list[SceneFactInput], Field(max_length=10)] = Field(default_factory=list)
    game_snapshot: UnityGameSnapshot | None = None

    @model_validator(mode="after")
    def unique_facts(self):
        labels = [fact.label for fact in self.scene_facts]
        if len(labels) != len(set(labels)):
            raise ValueError("scene fact labels must be unique")
        return self

    def item_states(self, previous: dict[str, str] | None = None) -> dict[str, str]:
        # A complete snapshot replaces the previous turn; removed facts must not
        # linger. Keep display names as well as the protagonist's machine IDs.
        current = {"场景": self.scene.strip(),
                   "男主手持物": self.held_item.strip() or "手里没有拿物品",
                   **({fact.label: fact.value for fact in self.scene_facts}
                      if self.game_snapshot is None or self.game_snapshot.schema_version == 4 else {})}
        if self.game_snapshot is not None:
            current.update(self.game_snapshot.facts())
            held = [e.label for e in self.game_snapshot.entities if e.owner_id == "protagonist"]
            current["男主手持物"] = "、".join(held) or "手里没有拿物品"
        changes = []
        for label, old in (previous or {}).items():
            if label in {"本轮游戏状态变化", "游戏快照版本"}:
                continue
            if label not in current:
                changes.append(f"{label}：旧记录已失效，当前快照未提供此状态")
            elif current[label] != old:
                changes.append(f"{label}：旧记录「{old}」已失效，当前「{current[label]}」")
        if changes:
            current["本轮游戏状态变化"] = "；".join(changes)
        return current


class ChatInput(BaseModel):
    save_id: str
    text: Annotated[str, Field(min_length=1, max_length=1000)]
    world: WorldInput


class SaveInput(BaseModel):
    save_id: str
    world: WorldInput | None = None


def _runtime_config() -> WorldMindRuntimeConfig:
    canon_root = _canon_root()
    return WorldMindRuntimeConfig(
        expected_world_id="songjiangfu",
        expected_protagonist_id="protagonist",
        world_canon_dir=ROOT / "世界设定" / "松江府",
        protagonist_canon_dir=canon_root / "主角",
        character_package_dirs={"baiweixi": canon_root / "白未晞"},
        p0_allowed_character_ids=("baiweixi",),
        # The promoted V3 Adapter generates only the reply JSON contract.
        # World state stays program-owned; the runtime unwraps reply for display.
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
        self.game_world = SubmittedGameWorld()
        self.runtime = WorldMindRuntime(
            config=self.config,
            store=self.store,
            world_state_provider=self.world,
            game_clock=self.clock,
            model=self.model,
            game_world=self.game_world,
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
        previous = self.store.load_live_world(session)
        if previous is not None and world_input is None:
            return
        value = world_input or WorldInput()
        snapshot = value.game_snapshot
        prior_observation = self.store.load_unity_observation(session.save_id)
        if (snapshot is not None and prior_observation is not None
                and prior_observation.get('schema_version', 0) > snapshot.schema_version):
            raise HTTPException(status_code=409, detail="游戏快照协议版本倒退，请更新游戏程序")
        if snapshot is None and (prior_observation is not None or
                previous is not None and "游戏快照版本" in previous.scene.item_states):
            raise HTTPException(status_code=409, detail="此存档需要当前游戏快照，请在更新后的游戏中重新发送")
        if snapshot is not None and snapshot.save_id != session.save_id:
            raise HTTPException(status_code=409, detail="游戏快照与当前存档不匹配")
        if snapshot is not None and previous is not None:
            prior = previous.scene.item_states.get("游戏快照版本", "")
            instance, separator, sequence = prior.partition("/")
            if instance == snapshot.instance_id and separator and sequence.isdigit() and snapshot.sequence <= int(sequence):
                raise HTTPException(status_code=409, detail="游戏快照已过期，请重新采集状态")
        observation = None
        if snapshot is not None:
            try:
                observation = self._freshness().accept(snapshot, prior_observation)
                if snapshot.schema_version == 5:
                    observation['facts'] = [fact.model_dump() for fact in snapshot.fact_observations]
            except SnapshotRejected as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
        # held_item 是面向测试者的自由文本（可能是中文），held_item_ids 要求
        # 小写标识符：文本转 slug，空文本保持空元组。
        held_text = value.held_item.strip()
        held = (
            (re.sub(r"[^a-z0-9_]", "_", held_text.lower()).strip("_") or "item",)
            if held_text
            else ()
        )
        actor = snapshot.actor("protagonist") if snapshot else None
        if snapshot:
            held = tuple(e.entity_id for e in snapshot.entities if e.owner_id == "protagonist")
        await self.world.update_latest(
            session,
            ProtagonistLiveState(
                protagonist_id="protagonist",
                location_id=actor.location_id if actor else value.location_id.strip(),
                location_label=actor.location_label if actor else value.location_label.strip(),
                activity="正在行走" if actor and actor.motion == "walking" else value.activity.strip(),
                body_state={"当前状态": value.body.strip()},
                held_item_ids=held,
            ),
            ActiveSceneState(
                scene_id=actor.location_id if actor else value.location_id.strip(),
                location_label=actor.location_label if actor else value.location_label.strip(),
                present_character_ids=("protagonist", "baiweixi"),
                item_states=value.item_states(dict(previous.scene.item_states) if previous else None),
            ),
            snapshot.game_clock.current_time() if snapshot else self.clock.current_time(session),
            unity_observation=observation,
        )

    def _freshness(self) -> UnityFreshness:
        if not hasattr(self, '_unity_freshness'):
            self._unity_freshness = UnityFreshness()
        return self._unity_freshness

    async def chat(
        self,
        payload: ChatInput,
        *,
        on_delta=None,
    ) -> dict[str, Any]:
        session = _session(payload.save_id)
        async with self.send_lock:
            await self.ensure_world(session, payload.world)
            request_id = f"interactive_{uuid.uuid4().hex}"
            wall_started = time.perf_counter()
            with self.game_world.bind(payload.world.game_snapshot):
                result = await self.runtime.handle_turn(
                    TurnRequest(
                        request_id=request_id,
                        session=session,
                        text=payload.text.strip(),
                    ),
                    on_delta=on_delta,
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

    async def chat_stream(self, payload: ChatInput):
        """Yield NDJSON events while preserving the normal commit boundary."""
        queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

        async def emit_delta(text: str) -> None:
            await queue.put(("delta", text))

        task = asyncio.create_task(self.chat(payload, on_delta=emit_delta))
        try:
            while True:
                if task.done() and queue.empty():
                    break
                try:
                    kind, value = await asyncio.wait_for(queue.get(), timeout=0.05)
                except TimeoutError:
                    continue
                yield json.dumps(
                    {"type": kind, "text": str(value)},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ) + "\n"
            try:
                result = await task
            except HTTPException as error:
                detail = error.detail
                yield json.dumps(
                    {"type": "error", "status": error.status_code, "detail": detail},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ) + "\n"
            except Exception as error:
                yield json.dumps(
                    {"type": "error", "status": 500, "detail": str(error)},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ) + "\n"
            else:
                yield json.dumps(
                    {"type": "done", "value": result},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ) + "\n"
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    def status(self, session: RuntimeSessionIdentity) -> dict[str, Any]:
        live = self.store.load_live_world(session)
        runtime = self.store.load_heroine_runtime(session)
        current_time = self.clock.current_time(session)
        result: dict[str, Any] = {
            "save_id": session.save_id,
            "game_time": f"第{current_time.day}日 {current_time:%H:%M:%S}",
            "game_clock": self.clock.snapshot(session),
            "reconcile_queue": self.store.reconcile_queue_snapshot(session.save_id),
            "memory_queue": self.store.r1_memory_queue_snapshot(session.save_id),
        }
        freshness = self._freshness().status(self.store.load_unity_observation(session.save_id))
        result['game_observation'] = freshness
        observation = self.store.load_unity_observation(session.save_id)
        if observation is not None and observation.get('facts'):
            result['fact_observations'] = [
                {**fact, 'state': 'stale', 'value': ''}
                if freshness['status'] != 'current' else dict(fact)
                for fact in observation['facts']
            ]
        if live is not None:
            result["world"] = {
                "version": live.version,
                "location_id": live.protagonist.location_id,
                "location_label": live.protagonist.location_label,
                "activity": live.protagonist.activity,
                "body": next(iter(live.protagonist.body_state.values()), ""),
                "held_item": "、".join(live.protagonist.held_item_ids),
                "scene": live.scene.item_states.get("场景", ""),
                "scene_facts": [
                    {"label": label, "value": value}
                    for label, value in live.scene.item_states.items()
                    if label not in {"场景", "男主手持物", "本轮游戏状态变化"}
                ],
            }
        if runtime is not None:
            result["heroine"] = {
                "version": runtime.version,
                "living_mind": asdict(runtime.living_mind),
                "relationship": asdict(runtime.relationship),
            }
            if live is not None and freshness['status'] == 'current':
                # A status sync may precede the next dialogue/mind commit.
                # Physical activity must come from the new observation then too.
                facts = live.scene.item_states
                result['heroine']['living_mind']['current_activity'] = (
                    f"在{facts.get('白未晞实际位置', '未确认位置')}；"
                    f"{facts.get('白未晞实际移动', '移动状态未确认')}"
                )
        if freshness['status'] != 'current':
            # Read-only redaction: retain the durable watermark/history, but do
            # not expose old positions/possession as a current observation.
            if 'world' in result:
                result['world'].update(location_id='unknown', location_label='当前位置未确认',
                    activity='当前活动未确认', body='当前身体状态未确认', held_item='当前持有物未确认',
                    scene='游戏状态待重新同步', scene_facts=[
                        {'label': '游戏状态', 'value': freshness['reason']}])
            if 'heroine' in result:
                result['heroine']['living_mind']['current_activity'] = '当前活动未确认，等待游戏同步'
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
        "prewarm_ms": getattr(current.model.backend, "prewarm_ms", None),
    }


@app.post("/api/launcher/shutdown", status_code=202, include_in_schema=False)
async def launcher_shutdown(
    request: Request,
    launcher_token: Annotated[
        str | None,
        Header(alias=LAUNCHER_SHUTDOWN_HEADER),
    ] = None,
) -> dict[str, str]:
    client = request.client
    if client is None or client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="forbidden")
    expected = os.environ.get(LAUNCHER_SHUTDOWN_TOKEN_ENV)
    if (
        not expected
        or len(expected) < 32
        or launcher_token is None
        or not secrets.compare_digest(launcher_token, expected)
    ):
        raise HTTPException(status_code=404, detail="not found")
    server = getattr(request.app.state, "uvicorn_server", None)
    if server is None:
        raise HTTPException(status_code=503, detail="server unavailable")
    server.should_exit = True
    return {"status": "stopping"}


@app.post("/api/status")
async def status(payload: SaveInput) -> dict[str, Any]:
    current = _service()
    session = _session(payload.save_id)
    # Read-only polls stay cheap; writes/initialization share the foreground lock
    # so a status request cannot splice a different world into a chat snapshot.
    if payload.world is not None or current.store.load_live_world(session) is None:
        async with current.send_lock:
            await current.ensure_world(session, payload.world)
    return current.status(session)


@app.post("/api/chat")
async def chat(payload: ChatInput) -> dict[str, Any]:
    return await _service().chat(payload)


@app.post("/api/chat/stream")
async def chat_stream(payload: ChatInput) -> StreamingResponse:
    return StreamingResponse(
        _service().chat_stream(payload),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
    parser.add_argument(
        "--asset-root",
        type=Path,
        help="便携发布资产目录；包含 Gemma 与 retrieval manifests",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        help="存档目录；默认使用 LOCALAPPDATA/AiPeople/data",
    )
    args = parser.parse_args()
    if args.asset_root is not None:
        _configure_asset_root(args.asset_root)
    APP_MODEL_PROFILE = args.model_profile
    if args.data_root is not None:
        APP_DATABASE_PATH = (
            args.data_root.expanduser().resolve()
            / "baiweixi_gemma4_interactive.sqlite3"
        )
    elif args.database_path == DATA_ROOT / "baiweixi_gemma4_interactive.sqlite3" and os.environ.get(
        "AIPEOPLE_PORTABLE"
    ) == "1":
        APP_DATABASE_PATH = (
            _portable_data_root() / "baiweixi_gemma4_interactive.sqlite3"
        ).resolve()
    else:
        APP_DATABASE_PATH = args.database_path.resolve()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="info")
    )
    app.state.uvicorn_server = server
    try:
        server.run()
    finally:
        app.state.uvicorn_server = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

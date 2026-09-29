"""游戏-LLM 接口契约的 runtime 侧「远端游戏」适配器。

游戏（Unity 客户端）是世界的唯一事实源与执行者：本适配器把 runtime 的
``project`` / ``execute_action`` / ``submit_player_fact_report`` 调用翻译为对本地
游戏世界服务的 HTTP 请求（Unity 侧端点见
``AiGirlFriendUnity/Assets/AiPeople/World/GameWorldServer.cs``，
默认 ``http://127.0.0.1:8770/game/``）。

用法（在后端进程内显式接入，默认行为不变）::

    from runtime.world_mind.remote_game_world import RemoteGameWorld

    game_world = RemoteGameWorld("http://127.0.0.1:8770")
    runtime = WorldMindRuntime(..., game_world=game_world)

设计依据：《游戏-LLM 接口契约设计_20260827.md》§二/§四/§五——
runtime 只做投影（读）与转发（动作请求），不模拟动作、不写游戏世界。
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .game_interface import ActionOutcome, GameWorldProjection


class RemoteGameWorldError(RuntimeError):
    """游戏世界服务不可用或响应不符合契约。"""


@dataclass(slots=True)
class RemoteGameWorld:
    """连接 Unity 游戏世界服务的适配器（实现 GameWorldInterface 协议）。"""

    base_url: str = "http://127.0.0.1:8770"
    timeout_seconds: float = 5.0
    #: 游戏服务不可达时是否降级为空投影（默认 False：显式失败，避免状态静默漂移）
    fallback_to_empty: bool = False
    _warned_unreachable: bool = field(default=False, init=False, repr=False)

    async def project(self) -> GameWorldProjection:
        try:
            payload = await self._get("/game/project")
        except RemoteGameWorldError:
            if not self.fallback_to_empty:
                raise
            if not self._warned_unreachable:
                self._warned_unreachable = True
                import logging

                logging.getLogger(__name__).warning(
                    "游戏世界服务不可达，已按空投影降级（fallback_to_empty=True）：%s",
                    self.base_url,
                )
            return GameWorldProjection()

        self._warned_unreachable = False
        pending = payload.get("pending_actions") or ()
        item_states = payload.get("item_states")
        return GameWorldProjection(
            pending_actions=tuple(dict(item) for item in pending if isinstance(item, dict)),
            item_states=(
                MappingProxyType({str(key): str(value) for key, value in item_states.items()})
                if isinstance(item_states, dict)
                else None
            ),
            heroine_activity=(str(payload["heroine_activity"]) if payload.get("heroine_activity") else None),
            recent_feedback=tuple(str(item) for item in (payload.get("recent_feedback") or ())),
            player_fact_reports=tuple(
                dict(item) for item in (payload.get("player_fact_reports") or ()) if isinstance(item, dict)
            ),
            unknown_facts=tuple(
                dict(item) for item in (payload.get("unknown_facts") or ()) if isinstance(item, dict)
            ),
        )

    async def execute_action(
        self, action_id: str, params: Mapping[str, object] | None = None
    ) -> ActionOutcome:
        payload = await self._post(
            "/game/execute_action",
            {"action_id": action_id, "params": dict(params or {})},
        )
        return ActionOutcome(
            accepted=bool(payload.get("accepted")),
            reason=str(payload.get("reason", "")),
        )

    async def submit_player_fact_report(self, report: Mapping[str, object]) -> None:
        await self._post("/game/player_fact_report", dict(report))

    async def fetch_action_manifest(self) -> dict[str, dict]:
        """动作清单由游戏导出（接口契约 §四）；返回 action_id → 定义。"""
        payload = await self._get("/game/actions")
        actions = payload.get("actions")
        if not isinstance(actions, list):
            raise RemoteGameWorldError("游戏动作清单响应缺少 actions 列表")
        manifest: dict[str, dict] = {}
        for item in actions:
            if isinstance(item, dict) and item.get("action_id"):
                manifest[str(item["action_id"])] = dict(item)
        return manifest

    # ---------- HTTP ----------

    async def _get(self, path: str) -> dict:
        return await asyncio.to_thread(self._request, "GET", path, None)

    async def _post(self, path: str, body: dict) -> dict:
        return await asyncio.to_thread(self._request, "POST", path, body)

    def _request(self, method: str, path: str, body: dict | None) -> dict:
        url = self.base_url.rstrip("/") + path
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json; charset=utf-8")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                detail = error.read().decode("utf-8", "ignore")
            except Exception:  # noqa: BLE001 - 读取失败不掩盖原始错误
                detail = ""
            raise RemoteGameWorldError(
                f"{method} {path} -> HTTP {error.code}: {detail[:200]}"
            ) from error
        except (urllib.error.URLError, OSError) as error:
            raise RemoteGameWorldError(f"{method} {path} 连接失败：{error}") from error

        try:
            payload = json.loads(raw)
        except ValueError as error:
            raise RemoteGameWorldError(
                f"{method} {path} 响应不是合法 JSON：{raw[:200]}"
            ) from error
        if not isinstance(payload, dict):
            raise RemoteGameWorldError(f"{method} {path} 响应不是 JSON 对象")
        return payload

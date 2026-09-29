"""RemoteGameWorld 适配器测试。

契约样例（ACTIONS_SAMPLE / PROJECT_SAMPLE / PROJECT_DONE_SAMPLE）来自 Unity 实现的实际输出，
由客户端 `AiPeople/导出游戏世界契约样例` 菜单（AiPeopleGameWorldContractProbe）导出，
此处按真实 JSON 固定，避免跨语言契约靠手抄。

对应客户端实现：`AiGirlFriendUnity/Assets/AiPeople/World/{ActionCatalog,GameWorldState,GameWorldServer}.cs`
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import MappingProxyType

import pytest

from runtime.world_mind.game_interface import GameWorldProjection
from runtime.world_mind.remote_game_world import RemoteGameWorld, RemoteGameWorldError

# ---- Unity 实际输出样例（2026-09-11 导出） ----

ACTIONS_SAMPLE = (
    '{"actions":[{"action_id":"cook_meal","description":"走到厨房做饭，双手被占用，大约需要 20 分钟",'
    '"duration_game_seconds":1200,"params":{},"effects":{"item_states":{"桌上":"一碗热汤面"}}},'
    '{"action_id":"move_to","description":"走向某处，路上可以被叫住","duration_game_seconds":60,'
    '"params":{"target_location_id":{"required":true}},"effects":{"item_states":{}}},'
    '{"action_id":"pick_up_item","description":"拿起场景中的某个物品","duration_game_seconds":60,'
    '"params":{"item_id":{"required":true}},"effects":{"item_states":{}}}]}'
)

PROJECT_SAMPLE = (
    '{"pending_actions":[{"action_id":"move_to","description":"走向某处，路上可以被叫住",'
    '"remaining_seconds":30.0,"params":{"target_location_id":"apartment_kitchen"}},'
    '{"action_id":"cook_meal","description":"走到厨房做饭，双手被占用，大约需要 20 分钟",'
    '"remaining_seconds":1200.0,"params":{}}],"item_states":{},'
    '"heroine_activity":"走到厨房做饭，双手被占用，大约需要 20 分钟",'
    '"heroine_location_id":"apartment_kitchen","recent_feedback":[],'
    '"player_fact_reports":[],"unknown_facts":[]}'
)

PROJECT_DONE_SAMPLE = (
    '{"pending_actions":[],"item_states":{"桌上":"一碗热汤面"},"heroine_activity":"在屋里待着",'
    '"heroine_location_id":"apartment_kitchen",'
    '"recent_feedback":["动作被拒绝：动作不在游戏清单内：dance"],'
    '"player_fact_reports":[],"unknown_facts":[]}'
)


class _GameStubHandler(BaseHTTPRequestHandler):
    """按 Unity GameWorldServer 的响应结构提供样例（含真实导出 JSON）。"""

    protocol_version = "HTTP/1.1"
    project_payload: str = PROJECT_SAMPLE

    def log_message(self, *_args) -> None:  # noqa: D102 - 静默
        pass

    def _send(self, status: int, raw: str) -> None:
        body = raw.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/game/project":
            self._send(200, self.project_payload)
            return
        if self.path == "/game/actions":
            self._send(200, ACTIONS_SAMPLE)
            return
        self._send(404, '{"detail":"not found"}')

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

        if self.path == "/game/execute_action":
            params = payload.get("params") or {}
            if payload.get("action_id") == "move_to" and params.get("target_location_id"):
                self._send(200, '{"accepted":true,"reason":""}')
            else:
                self._send(200, '{"accepted":false,"reason":"缺少必要参数：target_location_id"}')
            return

        if self.path == "/game/player_fact_report":
            if all(payload.get(key) for key in ("report_id", "subject", "predicate", "value")):
                self._send(200, '{"status":"pending_confirmation"}')
            else:
                self._send(400, '{"detail":"player fact report 需要 report_id"}')
            return

        self._send(404, '{"detail":"not found"}')


@pytest.fixture()
def make_server():
    servers: list[ThreadingHTTPServer] = []

    def _make(project_payload: str = PROJECT_SAMPLE) -> str:
        handler = type("Handler", (_GameStubHandler,), {"project_payload": project_payload})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield _make
    for server in servers:
        server.shutdown()
        server.server_close()


def test_project_parses_in_progress_sample(make_server) -> None:
    async def scenario() -> None:
        world = RemoteGameWorld(make_server(PROJECT_SAMPLE))
        projection = await world.project()

        assert isinstance(projection, GameWorldProjection)
        assert [item["action_id"] for item in projection.pending_actions] == ["move_to", "cook_meal"]
        assert projection.pending_actions[0]["remaining_seconds"] == 30.0
        assert projection.pending_actions[0]["params"]["target_location_id"] == "apartment_kitchen"
        assert projection.item_states == {}
        assert isinstance(projection.item_states, MappingProxyType)
        assert projection.heroine_activity == "走到厨房做饭，双手被占用，大约需要 20 分钟"
        assert projection.recent_feedback == ()

    asyncio.run(scenario())


def test_project_parses_completed_sample_with_entity_and_feedback(make_server) -> None:
    async def scenario() -> None:
        world = RemoteGameWorld(make_server(PROJECT_DONE_SAMPLE))
        projection = await world.project()

        assert projection.pending_actions == ()
        assert projection.item_states["桌上"] == "一碗热汤面"
        assert projection.heroine_activity == "在屋里待着"
        assert projection.recent_feedback[0].startswith("动作被拒绝")

    asyncio.run(scenario())


def test_execute_action_and_rejection_reason(make_server) -> None:
    async def scenario() -> None:
        world = RemoteGameWorld(make_server())

        accepted = await world.execute_action("move_to", {"target_location_id": "apartment_kitchen"})
        assert accepted.accepted is True
        assert accepted.reason == ""

        rejected = await world.execute_action("move_to", {})
        assert rejected.accepted is False
        assert "缺少必要参数" in rejected.reason

    asyncio.run(scenario())


def test_player_fact_report(make_server) -> None:
    async def scenario() -> None:
        world = RemoteGameWorld(make_server())

        await world.submit_player_fact_report(
            {"report_id": "r1", "subject": "protagonist", "predicate": "ate", "value": "面"}
        )

        with pytest.raises(RemoteGameWorldError):
            await world.submit_player_fact_report({"report_id": "r2"})

    asyncio.run(scenario())


def test_action_manifest_exported_by_game(make_server) -> None:
    async def scenario() -> None:
        world = RemoteGameWorld(make_server())
        manifest = await world.fetch_action_manifest()

        assert set(manifest) == {"cook_meal", "move_to", "pick_up_item"}
        assert manifest["cook_meal"]["duration_game_seconds"] == 1200
        assert manifest["cook_meal"]["effects"]["item_states"]["桌上"] == "一碗热汤面"
        assert manifest["move_to"]["params"]["target_location_id"]["required"] is True

    asyncio.run(scenario())


def test_unreachable_game_raises_or_falls_back() -> None:
    async def scenario() -> None:
        strict = RemoteGameWorld("http://127.0.0.1:1", timeout_seconds=0.5)
        with pytest.raises(RemoteGameWorldError):
            await strict.project()

        lenient = RemoteGameWorld("http://127.0.0.1:1", timeout_seconds=0.5, fallback_to_empty=True)
        projection = await lenient.project()
        assert projection.pending_actions == ()
        assert projection.item_states is None

    asyncio.run(scenario())

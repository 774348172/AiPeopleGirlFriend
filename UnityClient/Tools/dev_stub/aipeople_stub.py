"""开发用契约桩（不含任何模型）。

复刻后端 tools/baiweixi_chat_app.py 的三个端点行为，供 Unity 客户端开发与自动化联调：

    GET  /api/health
    POST /api/status
    POST /api/chat/stream    (application/x-ndjson: delta* -> done | error)

用法：
    python Tools/dev_stub/aipeople_stub.py --port 8767

注意：本桩不产生真实模型回复，**不得用于验收结论**；正式验收必须使用
AiPeople 后端的 baiweixi_chat_app.py。
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SAVE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")
INITIAL_GAME_TIME = datetime(1, 10, 11, 18, 0, 0)   # 正典锚点：第一年秋季第十一日 18:00
STARTED = time.monotonic()

REPLIES = (
    "……嗯，你回来了。",
    "我？……没在等你。只是刚好坐在这里。",
    "锅里的面还剩一点。你要是饿了，自己去盛。",
    "……你今天的话比平时多。",
    "窗外的雨停了。空气还是潮的。",
)

_state_lock = threading.Lock()
_saves: dict[str, dict] = {}


def _game_time() -> str:
    now = INITIAL_GAME_TIME + timedelta(seconds=time.monotonic() - STARTED)
    return f"第{now.day}日 {now:%H:%M:%S}"


def _save_state(save_id: str) -> dict:
    with _state_lock:
        state = _saves.get(save_id)
        if state is None:
            state = {
                "turns": 0,
                "world": {
                    "location_id": "apartment_table",
                    "location_label": "出租屋餐桌旁",
                    "activity": "坐着休息",
                    "body": "有些疲惫，没有受伤",
                    "held_item": "",
                    "scene": "窗外下着雨，屋里亮着暖灯",
                },
            }
            _saves[save_id] = state
        return state


class StubServer(ThreadingHTTPServer):
    chunk_delay = 0.06


class StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        pass

    # ---------- helpers ----------

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_payload(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def _reject_save_id(self, payload: dict) -> bool:
        save_id = str(payload.get("save_id", ""))
        if SAVE_ID_PATTERN.fullmatch(save_id) is None:
            self._send_json(400, {"detail": "存档 ID 不合法"})
            return True
        return False

    # ---------- endpoints ----------

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/health":
            self._send_json(200, {
                "status": "ready",
                "model": "dev-stub",
                "adapter_id": "none",
                "protocol": "plain_reply_v1 (dev stub, 无模型)",
                "background_execution": "queued_only",
                "uptime_seconds": round(time.monotonic() - STARTED, 1),
            })
            return
        self._send_json(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        payload = self._read_payload()
        if payload is None:
            self._send_json(400, {"detail": "请求体不是合法 JSON"})
            return
        if self._reject_save_id(payload):
            return

        if self.path == "/api/status":
            self._status(payload)
            return
        if self.path == "/api/chat/stream":
            self._chat_stream(payload)
            return
        self._send_json(404, {"detail": "not found"})

    def _status(self, payload: dict) -> None:
        state = _save_state(str(payload["save_id"]))
        world = payload.get("world")
        if isinstance(world, dict):
            state["world"] = world
        self._send_json(200, {
            "save_id": payload["save_id"],
            "game_time": _game_time(),
            "world": dict(state["world"], version=state["turns"] + 1),
            "heroine": {
                "version": state["turns"] + 1,
                "living_mind": {
                    "emotion": "平静里藏着一点在意",
                    "attention": "你刚进门的方向",
                    "current_activity": "在纸箱边坐着",
                    "immediate_intent": "等你先开口",
                },
                "relationship": {
                    "stage": "暂时共同生活（早期好感）",
                    "trust": "戒备中带依赖",
                },
            },
            "reconcile_queue": {"pending": 0, "failed": 0},
            "memory_queue": {"pending": 0, "failed": 0},
        })

    def _chat_stream(self, payload: dict) -> None:
        state = _save_state(str(payload["save_id"]))
        world = payload.get("world")
        if isinstance(world, dict):
            state["world"] = world
        turn = state["turns"]
        state["turns"] = turn + 1
        reply = REPLIES[turn % len(REPLIES)]

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        # 分片流式输出，模拟真实服务的 delta 序列
        step = max(1, len(reply) // 3)
        for index in range(0, len(reply), step):
            line = json.dumps({"type": "delta", "text": reply[index:index + step]}, ensure_ascii=False)
            self._write_chunk(line + "\n")
            time.sleep(self.server.chunk_delay)

        done = json.dumps({
            "type": "done",
            "value": {"reply": reply, "save_id": payload["save_id"], "turn": turn + 1},
        }, ensure_ascii=False)
        self._write_chunk(done + "\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _write_chunk(self, text: str) -> None:
        data = text.encode("utf-8")
        self.wfile.write(b"%X\r\n" % len(data) + data + b"\r\n")
        self.wfile.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="AiPeople 开发用契约桩（无模型）")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--chunk-delay", type=float, default=0.06, help="delta 分片间隔秒数")
    args = parser.parse_args()

    server = StubServer((args.host, args.port), StubHandler)
    server.chunk_delay = args.chunk_delay
    print(f"[dev-stub] listening on http://{args.host}:{args.port} (无模型，仅供开发联调)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

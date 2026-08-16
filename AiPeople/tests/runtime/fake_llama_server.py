from __future__ import annotations

import argparse
import json
import os
import select
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--fake-scenario", default="normal")
    parser.add_argument("--fake-state-file")
    parser.add_argument("--fake-observation-file")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--alias", default="qinweixi")
    parser.add_argument("--api-key", required=True)
    arguments, _unknown = parser.parse_known_args()
    return arguments


ARGS = _arguments()
HEALTH_CALLS = 0
BUSINESS_CALLS = 0


def _increment_launch_count() -> int:
    if not ARGS.fake_state_file:
        return 1
    path = Path(ARGS.fake_state_file)
    try:
        count = int(path.read_text(encoding="ascii")) + 1
    except (FileNotFoundError, ValueError):
        count = 1
    path.write_text(str(count), encoding="ascii")
    return count


LAUNCH_COUNT = _increment_launch_count()

if ARGS.fake_scenario == "startup_crash" and LAUNCH_COUNT == 1:
    sys.exit(26)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {ARGS.api_key}"

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        global HEALTH_CALLS
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        if self.path == "/health":
            HEALTH_CALLS += 1
            if ARGS.fake_scenario == "loading" and HEALTH_CALLS == 1:
                self._json(503, {"status": "loading model"})
            else:
                self._json(200, {"status": "ok"})
            return
        if self.path == "/v1/models":
            alias = "wrong-model" if ARGS.fake_scenario == "wrong_alias" else ARGS.alias
            self._json(200, {"object": "list", "data": [{"id": alias}]})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        global BUSINESS_CALLS
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self._json(400, {"error": "bad json"})
            return
        is_warmup = body.get("max_tokens") == 4
        if ARGS.fake_observation_file:
            observation = {
                "authorization": self.headers.get("Authorization"),
                "accept": self.headers.get("Accept"),
                "body": body,
                "warmup": is_warmup,
                "launch_count": LAUNCH_COUNT,
            }
            with Path(ARGS.fake_observation_file).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(observation, ensure_ascii=False) + "\n")
        if self.path == "/apply-template":
            if ARGS.fake_scenario == "measurement_unsupported":
                self._json(404, {"error": "not found"})
                return
            if ARGS.fake_scenario == "measurement_timeout":
                time.sleep(1)
            if ARGS.fake_scenario == "invalid_template":
                self._json(200, {"prompt": None})
                return
            messages = body.get("messages")
            if not isinstance(messages, list):
                self._json(400, {"error": "messages required"})
                return
            prompt = "".join(
                f"<|{item.get('role')}|>{item.get('content')}"
                for item in messages
                if isinstance(item, dict)
            ) + "<|assistant|>"
            self._json(200, {"prompt": prompt})
            return
        if self.path == "/tokenize":
            if ARGS.fake_scenario == "invalid_tokens":
                self._json(200, {"tokens": "invalid"})
                return
            content = body.get("content")
            if not isinstance(content, str):
                self._json(400, {"error": "content required"})
                return
            self._json(200, {"tokens": list(range(len(content)))})
            return
        if self.path != "/v1/chat/completions":
            self._json(404, {"error": "not found"})
            return
        if is_warmup:
            self._sse([_event("好"), b"data: [DONE]\n\n"])
            return
        BUSINESS_CALLS += 1
        self._scenario()

    def _scenario(self) -> None:
        scenario = ARGS.fake_scenario
        if scenario == "http_400":
            self._json(400, {"error": "private-response-secret"})
            return
        if scenario == "http_500":
            self._json(500, {"error": "temporary"})
            return
        if scenario == "malformed":
            self._sse([b"data: {not-json}\n\n"])
            return
        if scenario == "invalid_schema":
            self._sse([b"data: {\"choices\":{}}\n\n", b"data: [DONE]\n\n"])
            return
        if scenario == "invalid_event":
            self._sse([b"data: []\n\n", b"data: [DONE]\n\n"])
            return
        if scenario == "disconnect_before":
            self._disconnect()
            return
        if scenario == "disconnect_after":
            self._sse([_event("visible")], close_after=True)
            return
        if scenario == "read_timeout":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            time.sleep(2)
            return
        if scenario == "total_timeout":
            pieces = [_event("x") for _ in range(20)]
            self._sse(pieces, delay=0.08)
            return
        if scenario == "thinking":
            chunks = ["<th", "ink>secret", " thoughts</th", "ink>", "正文"]
            self._sse([*map(_event, chunks), b"data: [DONE]\n\n"])
            return
        if scenario == "thinking_only":
            self._sse([_event("<think>hidden</think>"), b"data: [DONE]\n\n"])
            return
        if scenario == "sse_edges":
            payload = json.dumps(
                {"choices": [{"delta": {"content": "边缘"}}]},
                ensure_ascii=False,
            )
            midpoint = payload.index("\"delta\"")
            event = (
                b": keepalive\r\n"
                + f"data: {payload[:midpoint]}\r\n".encode("utf-8")
                + f"data: {payload[midpoint:]}\r\n\r\n".encode("utf-8")
            )
            self._sse([event, _event(None), _event(None, reasoning="hidden"), b"data: [DONE]\r\n\r\n"], fragment=True)
            return
        if scenario == "length_finish":
            self._sse(
                [
                    _event("被截断"),
                    _event(None, finish_reason="length"),
                    b"data: [DONE]\n\n",
                ]
            )
            return
        if scenario == "crash_before" and LAUNCH_COUNT == 1:
            os._exit(23)
        if scenario == "crash_after" and LAUNCH_COUNT == 1:
            self._sse([_event("first")], close_after=True)
            os._exit(24)
        if scenario == "always_crash":
            os._exit(25)
        if scenario == "serialized":
            self._observe({"phase": "start"})
            time.sleep(0.1)
            self._observe({"phase": "end"})
            self._sse([_event("顺序"), b"data: [DONE]\n\n"])
            return
        if scenario == "cancel" or (
            scenario == "cancel_once" and BUSINESS_CALLS == 1
        ):
            self._cancel_stream()
            return
        self._sse([_event("你"), _event("好"), b"data: [DONE]\n\n"])

    def _disconnect(self) -> None:
        self.close_connection = True
        try:
            self.connection.shutdown(2)
        except OSError:
            pass
        self.connection.close()

    def _observe(self, payload: object) -> None:
        if not ARGS.fake_observation_file:
            return
        with Path(ARGS.fake_observation_file).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _cancel_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        first = _event("start")
        self.wfile.write(f"{len(first):X}\r\n".encode("ascii"))
        self.wfile.write(first + b"\r\n")
        self.wfile.flush()
        readable, _writable, _errors = select.select([self.connection], [], [], 1.0)
        disconnected = False
        if readable:
            try:
                disconnected = self.connection.recv(1, socket.MSG_PEEK) == b""
            except (ConnectionResetError, OSError):
                disconnected = True
        if disconnected and ARGS.fake_observation_file:
            with Path(ARGS.fake_observation_file).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"client_disconnected": True}) + "\n")
            return
        self._sse([_event("late"), b"data: [DONE]\n\n"], mark_disconnect=True)

    def _sse(
        self,
        pieces: list[bytes],
        *,
        fragment: bool = False,
        close_after: bool = False,
        delay: float = 0.0,
        mark_disconnect: bool = False,
    ) -> None:
        if mark_disconnect:
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for piece in pieces:
                network_chunks = [piece[index : index + 3] for index in range(0, len(piece), 3)] if fragment else [piece]
                for chunk in network_chunks:
                    self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                    self.wfile.write(chunk + b"\r\n")
                    self.wfile.flush()
                if delay:
                    time.sleep(delay)
            if close_after:
                self._disconnect()
                return
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            if mark_disconnect and ARGS.fake_observation_file:
                with Path(ARGS.fake_observation_file).open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"client_disconnected": True}) + "\n")


def _event(
    content: str | None,
    *,
    reasoning: str | None = None,
    finish_reason: str | None = None,
) -> bytes:
    delta: dict[str, object] = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    choice: dict[str, object] = {"delta": delta}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    payload = json.dumps({"choices": [choice]}, ensure_ascii=False)
    return f"data: {payload}\n\n".encode("utf-8")


if __name__ == "__main__":
    server = ThreadingHTTPServer((ARGS.host, ARGS.port), Handler)
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()

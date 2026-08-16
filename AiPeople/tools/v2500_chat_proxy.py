from __future__ import annotations

import argparse
import http.client
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime._prompt import QIN_WEIXI_REPLY_SYSTEM


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def inject_runtime_contract(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply the Qin Weixi reply contract without mutating the caller's payload."""
    updated = dict(payload)
    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        raise ValueError("messages must be a list")

    non_system_messages = [
        dict(message)
        for message in messages
        if isinstance(message, dict) and message.get("role") != "system"
    ]
    updated["messages"] = [
        {"role": "system", "content": QIN_WEIXI_REPLY_SYSTEM},
        *non_system_messages,
    ]

    template_kwargs = payload.get("chat_template_kwargs", {})
    if not isinstance(template_kwargs, dict):
        template_kwargs = {}
    updated["chat_template_kwargs"] = {
        **template_kwargs,
        "enable_thinking": False,
    }
    return updated


class QinWeixiProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    backend_host = "127.0.0.1"
    backend_port = 18081

    def do_GET(self) -> None:
        self._proxy()

    def do_HEAD(self) -> None:
        self._proxy()

    def do_POST(self) -> None:
        self._proxy()

    def do_PUT(self) -> None:
        self._proxy()

    def do_DELETE(self) -> None:
        self._proxy()

    def do_OPTIONS(self) -> None:
        self._proxy()

    def log_message(self, format: str, *args: object) -> None:
        sys.stdout.write("[v2500-chat] " + (format % args) + "\n")
        sys.stdout.flush()

    def _proxy(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        request_body = self.rfile.read(content_length) if content_length else None

        if self.path.split("?", 1)[0] in {
            "/v1/chat/completions",
            "/chat/completions",
        }:
            try:
                payload = json.loads((request_body or b"{}").decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("request body must be a JSON object")
                request_body = json.dumps(
                    inject_runtime_contract(payload),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                self._send_json_error(400, str(error))
                return

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
            and key.lower() not in {"host", "content-length"}
        }
        headers["Host"] = f"{self.backend_host}:{self.backend_port}"
        if request_body is not None:
            headers["Content-Length"] = str(len(request_body))

        connection = http.client.HTTPConnection(
            self.backend_host,
            self.backend_port,
            timeout=600,
        )
        try:
            connection.request(self.command, self.path, body=request_body, headers=headers)
            response = connection.getresponse()
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() in HOP_BY_HOP_HEADERS:
                    continue
                self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()

            if self.command != "HEAD":
                while chunk := response.read(64 * 1024):
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (ConnectionError, OSError, http.client.HTTPException) as error:
            if not self.wfile.closed:
                self._send_json_error(502, f"model backend unavailable: {error}")
        finally:
            self.close_connection = True
            connection.close()

    def _send_json_error(self, status: int, message: str) -> None:
        body = json.dumps({"error": {"message": message}}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve llama.cpp WebUI with the Qin Weixi runtime contract enforced."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18082)
    parser.add_argument("--backend-host", default="127.0.0.1")
    parser.add_argument("--backend-port", type=int, default=18081)
    args = parser.parse_args()

    QinWeixiProxyHandler.backend_host = args.backend_host
    QinWeixiProxyHandler.backend_port = args.backend_port
    server = ThreadingHTTPServer((args.host, args.port), QinWeixiProxyHandler)
    print(
        f"Qin Weixi chat: http://{args.host}:{args.port}/ "
        f"-> http://{args.backend_host}:{args.backend_port}/",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

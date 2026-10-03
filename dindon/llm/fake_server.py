"""Deterministic OpenAI-compatible endpoint for local development."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from typing import Any

_MODEL_ID = "dindon-fake"


class FakeLLMHandler(BaseHTTPRequestHandler):
    server_version = "DindonFakeLLM/0.1"

    def do_GET(self) -> None:
        if self.path != "/v1/models":
            self._send_json(404, {"error": {"message": "not found"}})
            return
        self._send_json(200, {"object": "list", "data": [{"id": _MODEL_ID, "object": "model"}]})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": "not found"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": {"message": "invalid JSON request"}})
            return
        if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
            self._send_json(400, {"error": {"message": "messages must be a list"}})
            return
        text = self._last_user_message(body["messages"])
        answer = f"fake: {text}"
        model = body.get("model", _MODEL_ID)
        if body.get("stream") is True:
            self._send_stream(model, answer)
        else:
            self._send_json(
                200,
                {
                    "id": "chatcmpl-dindon-fake",
                    "object": "chat.completion",
                    "created": 0,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": answer},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                },
            )

    @staticmethod
    def _last_user_message(messages: list[Any]) -> str:
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                content = message.get("content", "")
                return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        return ""

    def _send_stream(self, model: Any, answer: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        midpoint = len(answer) // 2
        chunks = [
            {"id": "chatcmpl-dindon-fake", "object": "chat.completion.chunk", "model": model,
             "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
            {"id": "chatcmpl-dindon-fake", "object": "chat.completion.chunk", "model": model,
             "choices": [{"index": 0, "delta": {"content": answer[:midpoint]}, "finish_reason": None}]},
            {"id": "chatcmpl-dindon-fake", "object": "chat.completion.chunk", "model": model,
             "choices": [{"index": 0, "delta": {"content": answer[midpoint:]}, "finish_reason": None}]},
            {"id": "chatcmpl-dindon-fake", "object": "chat.completion.chunk", "model": model,
             "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        for chunk in chunks:
            self.wfile.write(b"data: " + json.dumps(chunk).encode("utf-8") + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")

    def _send_json(self, status: int, document: dict[str, Any]) -> None:
        payload = json.dumps(document, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        # Keep deterministic development output free of timestamps and client addresses.
        print(fmt % args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a deterministic fake OpenAI-compatible LLM")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), FakeLLMHandler)
    print(f"Fake LLM listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

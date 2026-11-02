#!/usr/bin/env python3
"""OpenAI-compatible mock model so the whole flow runs without any model key.

The contest evaluator may not have a model API key. This server implements the
three endpoints the Dify OpenAI-API-compatible plugin needs for a chat model:

    GET  /v1/models              -> lists local-qwen2.5-0.5b
    POST /v1/chat/completions    -> deterministic in-character reply (stream or blocking)
    POST /v1/embeddings          -> deterministic 384-dim vector

The reply is derived from the CASEBOOK block in the user prompt, so a recalled
PowerContext memory visibly changes the answer.

Usage:
    python3 dify/scripts/mock_llm.py              # listens on 0.0.0.0:8099
    MOCK_LLM_PORT=9000 python3 dify/scripts/mock_llm.py
"""

import base64
import hashlib
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = os.getenv("MOCK_LLM_MODEL", "local-qwen2.5-0.5b")
NO_MEMORY_REPLY = (
    "The casebook is empty, so this is your first testimony. "
    "I have written it down, suspect - and I will hold you to every word. "
    "Tell me, who else saw you that night?"
)


def _quoted_memory(line: str) -> str:
    text = re.sub(r"^\[[^\]]*\]\s*", "", line)
    text = re.sub(r"^Player testimony:\s*", "", text)
    return text.strip().splitlines()[0].strip()[:240] if text.strip() else ""


def _casebook_entries(user_text: str) -> list[str]:
    """Return remembered texts from either recall format the bridge can emit."""
    if "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1" in user_text:
        start = user_text.index("BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1") + len(
            "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
        )
        end = user_text.find("END_POWERCONTEXT_PREPARED_CONTEXT_V1", start)
        raw = user_text[start:end].strip() if end > start else ""
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        entries = [
            str(item.get("content", "")).strip()
            for item in (payload.get("items") or [])
            if str(item.get("content", "")).strip()
        ]
        if entries:
            return entries
    marker = "CASEBOOK:"
    if marker not in user_text:
        return []
    block = user_text.split(marker, 1)[1]
    block = block.split("CURRENT TESTIMONY:", 1)[0]
    entries = []
    for raw in block.splitlines():
        line = raw.strip()
        if line.startswith("- ") and "No prior casebook" not in line:
            entries.append(line[2:].strip())
    return entries


def build_reply(user_text: str) -> str:
    entries = _casebook_entries(user_text)
    if not entries:
        return NO_MEMORY_REPLY
    entry = next((item for item in entries if "promise" in item.lower()), entries[0])
    quote = _quoted_memory(entry)
    return (
        "The casebook remembers you, suspect. You swore: "
        f"\u201c{quote}\u201d \u2014 and tonight your story changed. "
        "I have the record in front of me, so do not bother denying it. "
        "Why should I believe this new version?"
    )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in ("/v1/models", "/models"):
            self._json(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": MODEL,
                            "object": "model",
                            "created": int(time.time()),
                            "owned_by": "local-mock",
                        }
                    ],
                }
            )
            return
        if path in ("/health", "/healthz"):
            self._json({"status": "ok", "model": MODEL})
            return
        self._json({"error": {"message": "not found", "type": "invalid_request_error"}}, 404)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}

    def _stream(self, reply: str, model: str, prompt_text: str = ""):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        # HTTP/1.1 keep-alive would make the client wait for more data forever;
        # the stream is delimited by closing the connection after [DONE].
        self.send_header("Connection", "close")
        self.close_connection = True
        self._cors()
        self.end_headers()
        created = int(time.time())
        pieces = [reply[i : i + 24] for i in range(0, len(reply), 24)] or [""]

        def emit(delta, finish=None, usage=None):
            chunk = {
                "id": "chatcmpl-mock",
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            if usage is not None:
                chunk["usage"] = usage
            self.wfile.write(
                ("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode("utf-8")
            )

        emit({"role": "assistant", "content": ""})
        for piece in pieces:
            emit({"content": piece})
        emit({}, "stop")
        usage = {
            "prompt_tokens": max(1, len(prompt_text) // 4),
            "completion_tokens": max(1, len(reply) // 4),
            "total_tokens": max(2, (len(prompt_text) + len(reply)) // 4),
        }
        self.wfile.write(
            (
                "data: "
                + json.dumps(
                    {
                        "id": "chatcmpl-mock",
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [],
                        "usage": usage,
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            ).encode("utf-8")
        )
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        payload = self._read_json()
        if path.endswith("/chat/completions"):
            messages = payload.get("messages") or []
            user_text = ""
            for message in reversed(messages):
                if message.get("role") in ("user", "system"):
                    content = message.get("content")
                    if isinstance(content, list):
                        content = " ".join(
                            str(part.get("text", ""))
                            for part in content
                            if isinstance(part, dict)
                        )
                    if message.get("role") == "user":
                        user_text = str(content or "")
                        break
            reply = build_reply(user_text)
            model = payload.get("model") or MODEL
            if payload.get("stream"):
                self._stream(reply, model, user_text)
                return
            self._json(
                {
                    "id": "chatcmpl-mock",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": reply},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": max(1, len(user_text) // 4),
                        "completion_tokens": max(1, len(reply) // 4),
                        "total_tokens": max(2, (len(user_text) + len(reply)) // 4),
                    },
                }
            )
            return
        if path.endswith("/embeddings"):
            text = ""
            raw_input = payload.get("input")
            if isinstance(raw_input, list):
                text = " ".join(str(item) for item in raw_input)
            else:
                text = str(raw_input or "")
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vector = [
                (digest[i % len(digest)] / 255.0) - 0.5 for i in range(384)
            ]
            norm = sum(value * value for value in vector) ** 0.5 or 1.0
            self._json(
                {
                    "object": "list",
                    "model": "mock-embedding",
                    "data": [
                        {
                            "object": "embedding",
                            "index": 0,
                            "embedding": [round(value / norm, 6) for value in vector],
                        }
                    ],
                    "usage": {"prompt_tokens": 8, "total_tokens": 8},
                }
            )
            return
        self._json({"error": {"message": "not found", "type": "invalid_request_error"}}, 404)

    def log_message(self, fmt, *args):
        print("mock-llm: " + (fmt % args), flush=True)


if __name__ == "__main__":
    port = int(os.getenv("MOCK_LLM_PORT", "8099"))
    print(f"mock LLM listening on 0.0.0.0:{port} (model={MODEL})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()

#!/usr/bin/env python3
"""OpenAI-compatible mock model for offline Inspector Recall self-checks.

The contest evaluator may not have a model API key. This server implements
POST /v1/chat/completions for the model name used by the submitted DSL
(`local-qwen2.5-0.5b`) and returns a deterministic in-character reply, so the
whole Dify -> bridge -> PowerContext chain can be exercised without keys.

Usage:
    python3 dify/scripts/mock_llm.py          # listens on 0.0.0.0:8099

Then in Dify: Settings -> Model Provider -> OpenAI-API-compatible
    Base URL: http://host.docker.internal:8099/v1
    API Key:  dummy
    Model:    local-qwen2.5-0.5b
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


MODEL = os.getenv("MOCK_LLM_MODEL", "local-qwen2.5-0.5b")
REPLY = (
    "[offline self-check] I remember your promise. The casebook says you swore "
    "to return the ledger by dawn, and tonight you told me a different story. "
    "Explain yourself, suspect."
)


class Handler(BaseHTTPRequestHandler):
    def _send(self, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        try:
            self.rfile.read(length)
        except Exception:
            pass
        if self.path.endswith("/chat/completions"):
            self._send(
                {
                    "id": "chatcmpl-mock",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": MODEL,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": REPLY},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 16, "completion_tokens": 24, "total_tokens": 40},
                }
            )
        elif self.path.endswith("/embeddings"):
            self._send(
                {
                    "object": "list",
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.0] * 384}],
                    "model": "mock-embedding",
                }
            )
        else:
            self._send({"error": {"message": "not found", "type": "invalid_request_error"}})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(os.getenv("MOCK_LLM_PORT", "8099"))
    print(f"mock LLM listening on 0.0.0.0:{port} (model={MODEL})")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()

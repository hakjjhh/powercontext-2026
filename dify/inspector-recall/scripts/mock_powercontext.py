from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


PORT = int(os.environ.get("MOCK_POWERCONTEXT_PORT", "8000"))
MEMORIES: dict[str, list[str]] = {}
SCOPES: dict[str, str] = {}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"mock-powercontext: {fmt % args}", flush=True)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self) -> None:
        if self.path == "/health/ready":
            self._json(200, {"status": "ready"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        body = self._body()
        if self.path == "/v1/scopes":
            key = body.get("idempotency_key", "demo")
            scope_id = SCOPES.setdefault(key, f"scp_{abs(hash(key)):x}")
            self._json(201, {"scope_id": scope_id})
            return
        if self.path == "/v1/context/prepare":
            scope_id = body["scope_id"]
            content = "\n".join(MEMORIES.get(scope_id, []))
            self._json(200, {"status": "ready" if content else "empty", "content": content or None})
            return
        if self.path == "/v1/memory/remember":
            scope_id = body["scope_id"]
            MEMORIES.setdefault(scope_id, []).append(body["text"])
            self._json(200, {"citation": f"mock-citation-{len(MEMORIES[scope_id])}"})
            return
        self._json(404, {"error": "not found"})


def main() -> None:
    print(f"Mock PowerContext listening on http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
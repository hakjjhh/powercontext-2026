from __future__ import annotations

import json
import re
import os
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


POWERCONTEXT_URL = os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:8000").rstrip("/")
POWERCONTEXT_TOKEN = os.environ.get("POWERCONTEXT_TOKEN", "")
BRIDGE_TOKEN = os.environ.get("BRIDGE_TOKEN", "")
DB_PATH = Path(os.environ.get("BRIDGE_DB_PATH", "./state/scopes.sqlite3"))
PORT = int(os.environ.get("BRIDGE_PORT", "8787"))
MAX_BODY_BYTES = 1_000_000
DB_LOCK = threading.Lock()


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _error(handler: BaseHTTPRequestHandler, status: int, code: str, message: str) -> None:
    _json_response(handler, status, {"ok": False, "error": {"code": code, "message": message}})


def _pc_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if POWERCONTEXT_TOKEN:
        headers["Authorization"] = f"Bearer {POWERCONTEXT_TOKEN}"
    request = urllib.request.Request(
        f"{POWERCONTEXT_URL}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"PowerContext {path} failed with HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"PowerContext {path} is unavailable: {error.reason}") from error


def _brief_from_content(content: str) -> str:
    """Extract a short, human-readable casebook excerpt from PowerContext output."""
    marker = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
    if marker in content:
        start = content.find(marker) + len(marker)
        end = content.find("END_POWERCONTEXT_PREPARED_CONTEXT_V1")
        raw = content[start:end].strip() if end > start else ""
        if raw:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = None
            items = (payload or {}).get("items") or []
            lines = []
            for item in items[:4]:
                text = str(item.get("content", "")).strip().splitlines()
                if text:
                    lines.append("- " + text[0][:300])
            if not lines:
                for match in re.findall(r'"content":"((?:[^"\\]|\\.)*)"', raw)[:4]:
                    try:
                        text = json.loads('"' + match + '"')
                    except json.JSONDecodeError:
                        continue
                    first = text.strip().splitlines()[0] if text.strip() else ""
                    if first:
                        lines.append("- " + first[:300])
            if not lines:
                idx = raw.rfind('"content":"')
                if idx >= 0:
                    tail = raw[idx + len('"content":"'):]
                    tail = re.split(r'","|"}', tail)[0]
                    tail = tail.replace("\\n", " ").replace('\\"', '"').strip()
                    if tail:
                        lines.append("- " + tail[:300])
            if lines:
                return "\n".join(lines)
    return "\n".join(
        line.strip() for line in content.splitlines() if line.strip().startswith("- ")
    )[:800]

def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scopes (
                session_key TEXT PRIMARY KEY,
                scope_id TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.commit()


def _scope_for(session_id: str) -> str:
    if not session_id or len(session_id) > 160:
        raise ValueError("session_id must contain 1-160 characters")
    with DB_LOCK:
        with sqlite3.connect(DB_PATH) as connection:
            row = connection.execute(
                "SELECT scope_id FROM scopes WHERE session_key = ?", (session_id,)
            ).fetchone()
            if row:
                return str(row[0])
            created = _pc_post(
                "/v1/scopes",
                {
                    "title": f"Inspector Recall - {session_id}",
                    "summary": "Persistent casebook for the Inspector Recall Dify game",
                    "idempotency_key": f"inspector-recall:{session_id}",
                },
            )
            scope_id = created.get("scope_id")
            if not scope_id:
                raise RuntimeError("PowerContext did not return scope_id")
            connection.execute(
                "INSERT OR IGNORE INTO scopes(session_key, scope_id) VALUES (?, ?)",
                (session_id, scope_id),
            )
            connection.commit()
            return str(scope_id)


def _authorized(handler: BaseHTTPRequestHandler) -> bool:
    if not BRIDGE_TOKEN:
        return True
    return handler.headers.get("Authorization", "") == f"Bearer {BRIDGE_TOKEN}"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"bridge: {fmt % args}", flush=True)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("request body is too large")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def do_GET(self) -> None:
        if self.path == "/health":
            try:
                request = urllib.request.Request(f"{POWERCONTEXT_URL}/health/ready", method="GET")
                with urllib.request.urlopen(request, timeout=5) as response:
                    pc_status = response.status
            except Exception as error:  # noqa: BLE001
                pc_status = f"unavailable: {error}"
            _json_response(self, 200, {"ok": True, "powercontext_status": pc_status})
            return
        _error(self, 404, "not_found", "unknown endpoint")

    def do_POST(self) -> None:
        if not _authorized(self):
            _error(self, 401, "unauthorized", "missing or invalid bridge token")
            return
        try:
            body = self._body()
            session_id = str(body.get("session_id", "")).strip()
            scope_id = _scope_for(session_id)
            if self.path == "/api/recall":
                query = str(body.get("query", "")).strip()
                max_bytes = int(body.get("max_bytes", 2400))
                if not query:
                    raise ValueError("query is required")
                prepared = _pc_post(
                    "/v1/context/prepare",
                    {"scope_id": scope_id, "query": query, "max_bytes": max(max_bytes, 8000)},
                )
                content = (prepared.get("content") or "").strip()
                status = prepared.get("status", "empty")
                retrieval = "prepare"
                hit_count = 0
                if not content:
                    search = _pc_post(
                        "/v1/memory/search",
                        {"scope_id": scope_id, "query": query, "limit": 6},
                    )
                    hits = search.get("hits") or []
                    hit_count = len(hits)
                    if hits:
                        lines = ["PowerContext memory search matches:"]
                        for hit in hits:
                            text = str(hit.get("text", "")).strip()
                            if text:
                                lines.append(f"- {text}")
                        content = "\n".join(lines)
                        status = "fts"
                        retrieval = "memory.search"
                    else:
                        listed = _pc_post(
                            "/v1/memory/entries/list",
                            {"scope_id": scope_id},
                        )
                        entries = listed.get("entries") or []
                        hit_count = len(entries)
                        if entries:
                            lines = ["PowerContext casebook entries (latest revision):"]
                            for entry in entries:
                                text = str(entry.get("text", "")).strip()
                                if text:
                                    lines.append(f"- [{entry.get('kind', 'memory')}] {text}")
                            content = "\n".join(lines)
                            status = "entries"
                            retrieval = "memory.entries.list"
                brief = _brief_from_content(content)
                _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "scope_id": scope_id,
                        "status": status,
                        "content": content,
                        "brief": brief,
                        "query": query,
                        "retrieval": retrieval,
                        "hit_count": hit_count,
                    },
                )
                return
            if self.path == "/api/remember":
                text = str(body.get("text", "")).strip()
                kind = str(body.get("kind", "observation")).strip() or "observation"
                reason = str(body.get("reason", "Dify Inspector Recall")).strip()
                if not text:
                    raise ValueError("text is required")
                result = _pc_post(
                    "/v1/memory/remember",
                    {"scope_id": scope_id, "kind": kind, "text": text, "reason": reason},
                )
                _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "scope_id": scope_id,
                        "citation": result.get("citation"),
                        "memory_ref": result.get("memory_ref"),
                    },
                )
                return
            if self.path == "/api/reset":
                with DB_LOCK:
                    with sqlite3.connect(DB_PATH) as connection:
                        connection.execute("DELETE FROM scopes WHERE session_key = ?", (session_id,))
                        connection.commit()
                _json_response(self, 200, {"ok": True, "reset": session_id})
                return
            _error(self, 404, "not_found", "unknown endpoint")
        except (ValueError, json.JSONDecodeError) as error:
            _error(self, 400, "invalid_request", str(error))
        except Exception as error:  # noqa: BLE001
            _error(self, 502, "powercontext_error", str(error))


def main() -> None:
    _init_db()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Inspector Recall bridge listening on http://0.0.0.0:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
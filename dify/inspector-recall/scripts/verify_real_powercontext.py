from __future__ import annotations

import json
import os
import urllib.request


BASE = os.environ.get("BRIDGE_URL", "http://127.0.0.1:8787").rstrip("/")
TOKEN = os.environ.get("BRIDGE_TOKEN", "")
SESSION = os.environ.get("SESSION_ID", "real-player")


def post(path: str, payload: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def main() -> None:
    first = post("/api/recall", {"session_id": SESSION, "query": "What did I promise?", "max_bytes": 2400})
    saved = post(
        "/api/remember",
        {
            "session_id": SESSION,
            "kind": "observation",
            "text": "The player promised to return the golden compass before midnight.",
            "reason": "verification seed",
        },
    )
    second = post("/api/recall", {"session_id": SESSION, "query": "What did I promise?", "max_bytes": 2400})
    result = {
        "first_recall": first,
        "remember": saved,
        "second_recall": second,
        "scope_stable": first["scope_id"] == second["scope_id"],
        "memory_recalled": "golden compass" in second.get("content", ""),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["scope_stable"] or not result["memory_recalled"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PORT_BRIDGE = int(os.environ.get("BRIDGE_PORT", "18787"))
PORT_PC = int(os.environ.get("POWERCONTEXT_PORT", "18000"))
ENV = os.environ.copy()
ENV.update(
    {
        "POWERCONTEXT_URL": f"http://127.0.0.1:{PORT_PC}",
        "POWERCONTEXT_TOKEN": "",
        "BRIDGE_TOKEN": "local-demo-token",
        "BRIDGE_DB_PATH": str(ROOT / "state" / "verification.sqlite3"),
        "BRIDGE_PORT": str(PORT_BRIDGE),
        "MOCK_POWERCONTEXT_PORT": str(PORT_PC),
    }
)


def post(url: str, payload: dict, token: str = "") -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def wait(url: str) -> None:
    for _ in range(50):
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"service did not start: {url}")


def main() -> None:
    (ROOT / "state").mkdir(exist_ok=True)
    pc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "mock_powercontext.py")], env=ENV)
    bridge = subprocess.Popen([sys.executable, str(ROOT / "bridge" / "server.py")], env=ENV)
    try:
        wait(f"http://127.0.0.1:{PORT_PC}/health/ready")
        wait(f"http://127.0.0.1:{PORT_BRIDGE}/health")
        token = "local-demo-token"
        first = post(
            f"http://127.0.0.1:{PORT_BRIDGE}/api/recall",
            {"session_id": "demo-player", "query": "What did I promise?", "max_bytes": 1200},
            token,
        )
        post(
            f"http://127.0.0.1:{PORT_BRIDGE}/api/remember",
            {
                "session_id": "demo-player",
                "kind": "observation",
                "text": "The player promised to return the golden compass before midnight.",
                "reason": "verification seed",
            },
            token,
        )
        second = post(
            f"http://127.0.0.1:{PORT_BRIDGE}/api/recall",
            {"session_id": "demo-player", "query": "What did I promise?", "max_bytes": 1200},
            token,
        )
        result = {
            "first_recall": first,
            "second_recall": second,
            "assertions": {
                "scope_is_stable": first["scope_id"] == second["scope_id"],
                "memory_is_recalled": "golden compass" in second.get("content", ""),
                "status_is_ready": second.get("status") == "ready",
            },
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not all(result["assertions"].values()):
            raise SystemExit(1)
    finally:
        bridge.terminate()
        pc.terminate()
        bridge.wait(timeout=5)
        pc.wait(timeout=5)


if __name__ == "__main__":
    main()
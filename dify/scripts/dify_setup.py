#!/usr/bin/env python3
"""Register the offline model provider, import the DSL and publish the app.

This removes the two manual steps that made earlier submissions fail in the
evaluation environment:

  1. the OpenAI-API-compatible provider / ``local-qwen2.5-0.5b`` was missing;
  2. the app was imported but never published, so ``/v1/chat-messages`` failed.

Environment (all have safe local defaults except the admin login):

    DIFY_BASE_URL        http://127.0.0.1:8080
    DIFY_EMAIL           Dify admin email
    DIFY_PASSWORD        Dify admin password (plain text, base64-encoded here)
    MODEL_BASE_URL       http://mock-llm:8099/v1
    BRIDGE_URL           http://bridge:8787
    DSL_PATH             <repo>/dify/dsl/inspector-recall.yml
    PLUGIN_ID            langgenius/openai_api_compatible:0.0.73

Output: ``dify/.runtime/setup.json`` with app_id, API key and base URL, which
``scripts/demo.py`` consumes.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from difylib import DifyClient, DifyError  # noqa: E402

DIFY_DIR = Path(__file__).resolve().parent.parent
RUNTIME = DIFY_DIR / ".runtime"
PROVIDER = "langgenius/openai_api_compatible/openai_api_compatible"
PLUGIN_ID = os.getenv("PLUGIN_ID", "langgenius/openai_api_compatible:0.0.73")
MODEL = os.getenv("DIFY_MODEL", "local-qwen2.5-0.5b")


def log(message: str) -> None:
    print(f"[dify-setup] {message}", flush=True)


def patch_bridge_url(yaml_text: str, bridge_url: str) -> str:
    lines = yaml_text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.strip() == "name: POWERCONTEXT_BRIDGE_URL":
            for offset in range(index + 1, min(index + 8, len(lines))):
                if lines[offset].lstrip().startswith("value:"):
                    indent = lines[offset][: len(lines[offset]) - len(lines[offset].lstrip())]
                    lines[offset] = f"{indent}value: {bridge_url}\n"
                    return "".join(lines)
    raise SystemExit("could not find POWERCONTEXT_BRIDGE_URL.value in the DSL")


def ensure_provider(client: DifyClient) -> None:
    if client.provider_installed(PROVIDER):
        log(f"provider already installed: {PROVIDER}")
        return
    log(f"provider missing, installing marketplace plugin {PLUGIN_ID}")
    try:
        client.post(
            "/console/api/workspaces/current/plugin/install/marketplace",
            {"plugin_unique_identifiers": [PLUGIN_ID]},
        )
    except DifyError as error:
        raise SystemExit(
            "could not install the OpenAI-API-compatible plugin automatically "
            f"({error}). Install it once in Dify -> Plugins -> Marketplace, then rerun."
        ) from error
    for _ in range(60):
        time.sleep(2)
        if client.provider_installed(PROVIDER):
            log("provider installed")
            return
    raise SystemExit("plugin install did not finish within 120s; check Dify -> Plugins")


def register_model(client: DifyClient, model_base_url: str) -> None:
    credentials = {
        "api_key": os.getenv("MODEL_API_KEY", "dummy"),
        "endpoint_url": model_base_url,
        "base_url": model_base_url,
        "mode": "chat",
        "context_size": "8192",
        "max_tokens_to_sample": "2048",
        "function_calling_type": "no_call",
        "stream_mode": "streaming",
    }
    credentials_path = (
        f"/console/api/workspaces/current/model-providers/{PROVIDER}/models/credentials"
    )
    query = f"{credentials_path}?model={MODEL}&model_type=llm&config_from=custom-model"
    try:
        current = client.get(query)
    except DifyError:
        current = {}
    credential_id = (current or {}).get("current_credential_id")
    if credential_id:
        # Dify keeps using the credential that was active first, even after new
        # ones are created. Update it in place so a stale endpoint cannot win.
        client.request(
            "PUT",
            credentials_path,
            {
                "model": MODEL,
                "model_type": "llm",
                "credential_id": credential_id,
                "name": current.get("current_credential_name"),
                "credentials": credentials,
            },
        )
        log(f"updated active model credential {MODEL} -> {model_base_url}")
        return
    client.post(
        credentials_path,
        {"model": MODEL, "model_type": "llm", "credentials": credentials},
    )
    current = client.get(query)
    credential_id = (current or {}).get("current_credential_id")
    if credential_id:
        client.post(
            f"{credentials_path}/switch",
            {"model": MODEL, "model_type": "llm", "credential_id": credential_id},
        )
    log(f"registered model {MODEL} -> {model_base_url}")


def import_app(client: DifyClient, yaml_text: str, bridge_url: str) -> str:
    payload = {
        "mode": "yaml-content",
        "yaml_content": patch_bridge_url(yaml_text, bridge_url),
        "name": f"Inspector Recall (e2e {uuid.uuid4().hex[:6]})",
    }
    result = client.post("/console/api/apps/imports", payload)
    status = str(result.get("status", ""))
    app_id = result.get("app_id")
    if status not in ("completed", "completed-with-warnings"):
        raise SystemExit(f"DSL import failed: status={status} error={result.get('error')}")
    if not app_id:
        raise SystemExit(f"DSL import returned no app_id: {result}")
    log(f"DSL imported: app_id={app_id} status={status} warnings={len(result.get('warnings') or [])}")
    return str(app_id)


def publish(client: DifyClient, app_id: str) -> None:
    client.post(
        f"/console/api/apps/{app_id}/workflows/publish",
        {
            "marked_name": "offline e2e",
            "marked_comment": "imported and published by scripts/dify_setup.py",
        },
    )
    log("workflow published")


def ensure_api_key(client: DifyClient, app_id: str) -> str:
    keys = client.get(f"/console/api/apps/{app_id}/api-keys")
    data = keys.get("data") if isinstance(keys, dict) else keys
    if data:
        log("reusing existing app API key")
        return str(data[0]["token"])
    created = client.post(f"/console/api/apps/{app_id}/api-keys")
    token = created.get("token") if isinstance(created, dict) else None
    if not token:
        raise SystemExit(f"could not create app API key: {created}")
    log("created app API key")
    return str(token)


def main() -> int:
    base = os.getenv("DIFY_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
    email = os.getenv("DIFY_EMAIL", "")
    password = os.getenv("DIFY_PASSWORD", "")
    model_base = os.getenv("MODEL_BASE_URL", "http://mock-llm:8099/v1")
    bridge_url = os.getenv("BRIDGE_URL", "http://bridge:8787").rstrip("/")
    dsl_path = Path(os.getenv("DSL_PATH", str(DIFY_DIR / "dsl" / "inspector-recall.yml")))

    if not email or not password:
        raise SystemExit("set DIFY_EMAIL and DIFY_PASSWORD (Dify admin account)")

    client = DifyClient(base)
    client.login(email, password)
    log(f"logged in to {base}")
    ensure_provider(client)
    register_model(client, model_base)
    app_id = import_app(client, dsl_path.read_text(encoding="utf-8"), bridge_url)
    publish(client, app_id)
    token = ensure_api_key(client, app_id)

    RUNTIME.mkdir(exist_ok=True)
    state = {
        "base_url": base,
        "app_id": app_id,
        "api_key": token,
        "model": MODEL,
        "model_base_url": model_base,
        "bridge_url": bridge_url,
    }
    (RUNTIME / "setup.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    log(f"wrote {RUNTIME / 'setup.json'}")
    print(json.dumps({k: state[k] for k in ("app_id", "model", "bridge_url")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

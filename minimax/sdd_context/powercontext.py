"""Thin PowerContext HTTP client used to persist and reuse task state."""

import os
import pathlib

import requests


BASE_URL = os.getenv("POWERCONTEXT_URL", "http://127.0.0.1:8000").rstrip("/")
SCOPE_ID = os.getenv("POWERCONTEXT_SCOPE_ID", "").strip()
SCOPE_FILE = pathlib.Path(os.getenv("POWERCONTEXT_SCOPE_FILE", ".powercontext_scope"))


def _headers():
    token = os.getenv("POWERCONTEXT_TOKEN", "").strip()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def ensure_scope(name: str = "minimax-sdd-demo") -> str:
    global SCOPE_ID
    if SCOPE_ID:
        return SCOPE_ID
    if SCOPE_FILE.exists():
        SCOPE_ID = SCOPE_FILE.read_text(encoding="utf-8").strip()
        if SCOPE_ID:
            os.environ["POWERCONTEXT_SCOPE_ID"] = SCOPE_ID
            return SCOPE_ID
    response = requests.post(
        f"{BASE_URL}/v1/scopes",
        json={"name": name},
        headers=_headers(),
        timeout=15,
    )
    if response.status_code >= 400:
        response = requests.get(f"{BASE_URL}/v1/scopes/default", headers=_headers(), timeout=15)
        response.raise_for_status()
        payload = response.json()
        SCOPE_ID = payload.get("id") or payload.get("scope_id") or payload.get("scope", {}).get("id")
    else:
        payload = response.json()
        SCOPE_ID = payload.get("id") or payload.get("scope_id") or payload.get("scope", {}).get("id")
    if not SCOPE_ID:
        raise RuntimeError(f"cannot resolve scope id: {payload}")
    SCOPE_FILE.write_text(SCOPE_ID + "\n", encoding="utf-8")
    os.environ["POWERCONTEXT_SCOPE_ID"] = SCOPE_ID
    return SCOPE_ID


def remember(kind: str, text: str) -> dict:
    scope = ensure_scope()
    response = requests.post(
        f"{BASE_URL}/v1/memory/remember",
        json={"scope_id": scope, "kind": kind, "text": text},
        headers=_headers(),
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def search(query: str, limit: int = 5) -> list:
    scope = ensure_scope()
    response = requests.post(
        f"{BASE_URL}/v1/memory/search",
        json={"scope_id": scope, "query": query, "limit": limit},
        headers=_headers(),
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, list):
        return payload
    return (
        payload.get("hits")
        or payload.get("entries")
        or payload.get("results")
        or payload.get("items")
        or []
    )

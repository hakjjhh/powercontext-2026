#!/usr/bin/env python3
"""Small stdlib-only Dify console/API client used by the setup and demo scripts.

No third-party dependency, so the evaluator only needs Python 3.9+.
"""

from __future__ import annotations

import base64
import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = os.getenv("DIFY_BASE_URL", "http://127.0.0.1:8080")


class DifyError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, body: str):
        super().__init__(f"{method} {path} -> HTTP {status}: {body[:600]}")
        self.status = status
        self.body = body


class DifyClient:
    def __init__(self, base_url: str = DEFAULT_BASE, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self.bearer: str | None = None

    # -- low level ---------------------------------------------------------
    def cookie(self, name: str) -> str | None:
        for cookie in self.jar:
            if cookie.name == name:
                return cookie.value
        return None

    def request(self, method: str, path: str, payload=None, headers=None, raw=False):
        url = self.base_url + path
        data = None
        merged = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            merged["Content-Type"] = "application/json"
        if headers:
            merged.update(headers)
        csrf = self.cookie("csrf_token")
        if csrf:
            merged.setdefault("X-CSRF-Token", urllib.parse.unquote(csrf))
        if self.bearer:
            merged.setdefault("Authorization", "Bearer " + self.bearer)
        request = urllib.request.Request(url, data=data, headers=merged, method=method.upper())
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                if raw:
                    return body
                return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise DifyError(method, path, error.code, body) from error

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, payload=None, **kwargs):
        return self.request("POST", path, payload if payload is not None else {}, **kwargs)

    # -- auth --------------------------------------------------------------
    def login(self, email: str, password: str) -> None:
        encoded = base64.b64encode(password.encode("utf-8")).decode("ascii")
        result = self.post(
            "/console/api/login",
            {
                "email": email,
                "password": encoded,
                "language": "en-US",
                "remember_me": True,
            },
        )
        token = (((result or {}).get("data") or {}).get("access_token")) if isinstance(result, dict) else None
        if token:
            self.bearer = token

    # -- convenience -------------------------------------------------------
    def list_model_providers(self) -> list[dict]:
        data = self.get("/console/api/workspaces/current/model-providers")
        if isinstance(data, dict):
            data = data.get("data") or data.get("providers") or []
        return [item for item in data if isinstance(item, dict)]

    def provider_installed(self, provider: str) -> bool:
        return any(str(item.get("provider", "")) == provider for item in self.list_model_providers())


def wait_http(url: str, timeout: float = 120.0, expect=(200,)) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status in expect:
                    return True
        except Exception:  # noqa: BLE001 - probing
            pass
        time.sleep(2)
    return False

"""MiniMax chat-completion client (OpenAI-compatible endpoint by default)."""

import os

import requests


BASE_URL = os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/")
MODEL = os.getenv("MINIMAX_MODEL", "MiniMax-M2")


def available() -> bool:
    return bool(os.getenv("MINIMAX_API_KEY", "").strip())


def chat(messages, temperature: float = 0.2, max_tokens: int = 12000) -> str:
    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY is not set")
    response = requests.post(
        f"{BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=180,
    )
    response.raise_for_status()
    payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError(f"unexpected MiniMax response: {str(payload)[:500]}")
    return choices[0]["message"]["content"]

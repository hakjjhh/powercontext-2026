#!/usr/bin/env python3
"""Static self-check of the submission materials.

Runs without Dify/docker and fails loudly when a required piece is missing:

  * the single DSL exists and targets the offline model provider;
  * the bridge URL comes from an app environment variable;
  * one-click scripts / compose exist and use service names;
  * no real credentials are committed.

Usage: python3 dify/scripts/verify_materials.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DIFY = Path(__file__).resolve().parent.parent
FAILS: list[str] = []


def check(condition: bool, message: str) -> None:
    if condition:
        print(f"  ok   {message}")
    else:
        print(f"  FAIL {message}")
        FAILS.append(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def main() -> int:
    print("layout")
    required = [
        "README.md",
        "dsl/inspector-recall.yml",
        "backend/bridge/server.py",
        "deploy/compose.yaml",
        "scripts/mock_llm.py",
        "scripts/start_all.sh",
        "scripts/dify_setup.py",
        "scripts/demo.py",
        "docs/design.md",
        "docs/run.md",
        "docs/verification.md",
        "docs/evidence.md",
        "verification/real-dify/real-run.md",
    ]
    for relative in required:
        check((DIFY / relative).exists(), relative)

    print("dsl")
    dsl = read(DIFY / "dsl/inspector-recall.yml")
    for needle, label in [
        ("local-qwen2.5-0.5b", "model local-qwen2.5-0.5b"),
        ("langgenius/openai_api_compatible/openai_api_compatible", "OpenAI-compatible provider"),
        ("POWERCONTEXT_BRIDGE_URL", "bridge URL env var"),
        ("/api/recall", "recall HTTP node"),
        ("/api/remember", "remember HTTP node"),
        ("type: http-request", "http-request nodes"),
        ("type: llm", "llm node"),
        ("type: answer", "answer node"),
    ]:
        check(needle in dsl, label)
    check("localhost" not in dsl and "127.0.0.1" not in dsl, "DSL has no localhost/127.0.0.1 default")

    print("service names")
    compose = read(DIFY / "deploy/compose.yaml")
    for needle, label in [
        ("powercontext:8000", "bridge -> powercontext service name"),
        ("DIFY_NETWORK", "shared network with Dify"),
        ("seekdb", "embedded seekdb storage"),
        ("mock-llm", "mock LLM service"),
    ]:
        check(needle in compose, label)
    setup = read(DIFY / "scripts/dify_setup.py")
    check("models/credentials" in setup, "auto model registration")
    check("/apps/imports" in setup, "auto DSL import")
    check("/workflows/publish" in setup, "auto publish")
    check("api-keys" in setup, "auto app API key")

    print("secrets")
    offenders = []
    pattern = re.compile(r"sk-[A-Za-z0-9_\-]{20,}")
    for path in DIFY.rglob("*"):
        if not path.is_file() or path.suffix in {".png", ".pyc", ".db", ".sqlite3"}:
            continue
        if ".runtime" in path.parts or "state" in path.parts:
            continue
        if pattern.search(read(path)):
            offenders.append(str(path.relative_to(DIFY)))
    check(not offenders, "no API keys committed" + (f" ({offenders})" if offenders else ""))

    state = DIFY / "verification/real-dify/chat-transcript.json"
    if state.exists():
        transcript = json.loads(state.read_text(encoding="utf-8"))
        check(all(turn.get("status") == 200 for turn in transcript.get("turns", [])),
              "real Dify transcript turns are HTTP 200")

    print("primary evidence (verification/e2e-seekdb)")
    evidence = DIFY / "verification/e2e-seekdb"
    for name in (
        "README.md",
        "run.md",
        "chat-transcript.json",
        "node-trace.json",
        "bridge-log.txt",
        "mock-llm.log",
        "powercontext-backend.txt",
        "seekdb-storage.txt",
        "seekdb-recall.json",
        "pip-check.txt",
        "setup-evidence.json",
    ):
        check((evidence / name).exists(), f"e2e-seekdb/{name}")
    backend = read(evidence / "powercontext-backend.txt")
    check("POWERCONTEXT_SERVER_DATABASE_KIND=seekdb" in backend, "backend proof says seekdb")
    pip_check = read(evidence / "pip-check.txt")
    check("No broken requirements found" in pip_check, "pip check clean in the image")
    recall = read(evidence / "seekdb-recall.json")
    if recall:
        check(recall.count("Player testimony") >= 3, "casebook rows readable after the run")
    trace_path = evidence / "node-trace.json"
    transcript_path = evidence / "chat-transcript.json"
    if trace_path.exists() and transcript_path.exists():
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        turns = json.loads(transcript_path.read_text(encoding="utf-8")).get("turns", [])
        check(len(trace) == 3, "3 workflow runs captured")
        check(all(run.get("status") == "succeeded" for run in trace), "every run succeeded")
        check(
            all(node["status"] == "succeeded" for run in trace for node in run.get("nodes", [])),
            "every node succeeded",
        )
        check(
            {run.get("workflow_run_id") for run in trace}
            == {turn.get("workflow_run_id") for turn in turns},
            "run ids match between transcript and trace",
        )
        scope = (json.loads(transcript_path.read_text(encoding="utf-8")).get("bridge_reset") or {}).get(
            "scope_id"
        )
        check(bool(scope), "transcript records the reset scope id")
        if scope:
            check(scope in recall, "recall proof uses the same scope id")

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        return 1
    print("all material checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

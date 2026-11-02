#!/usr/bin/env python3
"""Run the three-turn Inspector Recall demo and capture evidence.

Reads ``dify/.runtime/setup.json`` produced by ``scripts/dify_setup.py`` (or
``DIFY_BASE_URL`` + ``DIFY_APP_API_KEY``), then:

  1. sends turn 1 (a promise) in conversation A;
  2. sends turn 2 (a denial) in a *new* conversation, same user - proving the
     memory lives in PowerContext, not in Dify's conversation context;
  3. pulls the workflow runs and node executions from the console API;
  4. writes ``verification/e2e-mock/{chat-transcript,node-trace}.json`` and
     ``run.md``.

The bridge log is captured by ``scripts/demo.sh`` from the compose service.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from difylib import DifyClient, DifyError  # noqa: E402

DIFY_DIR = Path(__file__).resolve().parent.parent
RUNTIME = DIFY_DIR / ".runtime"
OUT = Path(os.getenv("DEMO_OUT_DIR") or "verification/e2e-mock")
if not OUT.is_absolute():
    OUT = DIFY_DIR / OUT
RUN_LABEL = os.getenv("DEMO_LABEL", "Offline mock-LLM end-to-end run")
USER = os.getenv("DEMO_USER", "e2e-mock-player")

TURNS = [
    "I promise to return the golden compass to you before midnight.",
    "Honestly, I never promised you anything about a compass.",
    "What did I promise you before midnight?",
]


def log(message: str) -> None:
    print(f"[demo] {message}", flush=True)


def reset_casebook(bridge_url: str, session_id: str) -> dict | None:
    """Give the demo user a fresh PowerContext scope so turn 1 starts empty.

    Best effort: the bridge may be on another host (or already reset), and the
    three-turn demo is still meaningful from a non-empty casebook.
    """
    if not bridge_url:
        return None
    import urllib.request

    request = urllib.request.Request(
        bridge_url.rstrip("/") + "/api/reset",
        data=json.dumps({"session_id": session_id}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as error:  # noqa: BLE001
        log(f"casebook reset skipped: {error}")
        return None


def load_state() -> dict:
    state: dict = {}
    if (RUNTIME / "setup.json").exists():
        state = json.loads((RUNTIME / "setup.json").read_text(encoding="utf-8"))
    state.setdefault("base_url", os.getenv("DIFY_BASE_URL", "http://127.0.0.1:8080"))
    key = os.getenv("DIFY_APP_API_KEY") or state.get("api_key")
    if not key:
        raise SystemExit("no app API key: run scripts/dify_setup.py or set DIFY_APP_API_KEY")
    state["api_key"] = key
    return state


def chat(base: str, key: str, query: str, conversation_id: str, user: str) -> dict:
    import urllib.request

    body = json.dumps(
        {
            "inputs": {},
            "query": query,
            "response_mode": "blocking",
            "conversation_id": conversation_id,
            "user": user,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        base.rstrip("/") + "/v1/chat-messages",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    payload["_elapsed_ms"] = int((time.time() - started) * 1000)
    return payload


def fetch_trace(
    client: DifyClient,
    app_id: str,
    since: int,
    turn_of_query: dict[str, int] | None = None,
    limit: int = 20,
) -> list[dict]:
    # The blocking chat API does not return a workflow_run_id, so the runs this
    # demo produced are identified by their creation time: the app is dedicated
    # to the demo, and the console lists runs newest first.
    runs = client.get(
        f"/console/api/apps/{app_id}/workflow-runs?page=1&limit={limit}&triggered_from=app-run"
    )
    items = runs.get("data") if isinstance(runs, dict) else runs
    selected = sorted(
        (run for run in items or [] if (run.get("created_at") or 0) >= since),
        key=lambda run: run.get("created_at") or 0,
    )
    trace = []
    for run in selected:
        run_id = run.get("id")
        entry = {
            "workflow_run_id": run_id,
            "status": run.get("status"),
            "created_at": run.get("created_at"),
            "elapsed_time": run.get("elapsed_time"),
            "elapsed_ms": round((run.get("elapsed_time") or 0) * 1000, 1),
            "total_tokens": run.get("total_tokens"),
            "query": None,
            "turn": None,
            "nodes": [],
        }
        if run_id:
            try:
                nodes = client.get(
                    f"/console/api/apps/{app_id}/workflow-runs/{run_id}/node-executions"
                )
                data = nodes.get("data") if isinstance(nodes, dict) else nodes
                for node in data or []:
                    elapsed = node.get("elapsed_time")
                    # Several runs can share one created_at second, so bind each
                    # run to its turn by the query the workflow actually received.
                    if entry.get("query") is None:
                        inputs = node.get("inputs")
                        if isinstance(inputs, dict) and inputs.get("query"):
                            entry["query"] = str(inputs["query"])
                            if turn_of_query and entry["query"] in turn_of_query:
                                entry["turn"] = turn_of_query[entry["query"]]
                    entry["nodes"].append(
                        {
                            "id": node.get("node_id"),
                            "title": node.get("title"),
                            "type": node.get("node_type"),
                            "status": node.get("status"),
                            # Console API reports node elapsed_time in seconds.
                            "elapsed_ms": round(elapsed * 1000, 1) if elapsed is not None else None,
                            "error": node.get("error"),
                        }
                    )
            except DifyError as error:
                entry["error"] = str(error)
        trace.append(entry)
    return trace


def main() -> int:
    state = load_state()
    base = state["base_url"]
    key = state["api_key"]
    OUT.mkdir(parents=True, exist_ok=True)

    turns = []
    conversation_id = ""
    # DEMO_BRIDGE_URL is the host-visible bridge port; BRIDGE_URL is the service
    # name written into the DSL, which only resolves inside the compose network.
    reset = reset_casebook(os.getenv("DEMO_BRIDGE_URL", "http://127.0.0.1:8787"), USER)
    if reset:
        log(f"casebook reset for {USER}: scope {reset.get('scope_id')}")
    demo_started_at = int(time.time()) - 1
    for index, query in enumerate(TURNS, start=1):
        # Turn 2+ runs in a fresh Dify conversation on purpose.
        reply = chat(base, key, query, conversation_id, USER)
        conversation_id = reply.get("conversation_id", conversation_id)
        turns.append(
            {
                "turn": index,
                "query": query,
                "status": reply.get("status", 200),
                "answer": reply.get("answer", ""),
                "conversation_id": reply.get("conversation_id"),
                "message_id": reply.get("message_id"),
                "workflow_run_id": (reply.get("metadata") or {}).get("workflow_run_id"),
                "elapsed_ms": reply.get("_elapsed_ms"),
            }
        )
        log(f"turn {index} answered in {reply.get('_elapsed_ms')}ms")
        if index == 1:
            conversation_id = ""  # start a new Dify conversation for turn 2

    trace = []
    email = os.getenv("DIFY_EMAIL", "")
    password = os.getenv("DIFY_PASSWORD", "")
    turn_of_query = {turn["query"]: turn["turn"] for turn in turns}
    if email and password and state.get("app_id"):
        try:
            console = DifyClient(base)
            console.login(email, password)
            trace = fetch_trace(console, state["app_id"], demo_started_at, turn_of_query)
            matched = 0
            for run in trace:
                turn = run.get("turn")
                if turn and 1 <= turn <= len(turns) and not turns[turn - 1]["workflow_run_id"]:
                    turns[turn - 1]["workflow_run_id"] = run["workflow_run_id"]
                    matched += 1
            trace.sort(key=lambda run: run.get("turn") or 99)
            (OUT / "node-trace.json").write_text(
                json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if matched != len(TURNS):
                log(f"warning: {matched}/{len(TURNS)} turns matched to a workflow run")
            log(f"captured {len(trace)} workflow runs")
            provider = "langgenius/openai_api_compatible/openai_api_compatible"
            model = state.get("model", "local-qwen2.5-0.5b")
            app = console.get(f"/console/api/apps/{state['app_id']}")
            credentials = console.get(
                f"/console/api/workspaces/current/model-providers/{provider}/models/credentials"
                f"?model={model}&model_type=llm&config_from=custom-model"
            )
            evidence = {
                "app": {
                    key: app.get(key)
                    for key in ("id", "name", "mode", "enable_site", "enable_api", "updated_at")
                },
                "model": {
                    "model": model,
                    "provider": provider,
                    "active_credential": credentials.get("current_credential_name"),
                    "endpoint_url": (credentials.get("credentials") or {}).get("endpoint_url"),
                },
                "bridge_url": state.get("bridge_url", ""),
            }
            (OUT / "setup-evidence.json").write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except DifyError as error:
            log(f"trace capture skipped: {error}")
    else:
        log("trace capture skipped (no DIFY_EMAIL/DIFY_PASSWORD or app_id)")

    transcript = {"user": USER, "base_url": base, "bridge_reset": reset, "turns": turns}
    (OUT / "chat-transcript.json").write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [f"# {RUN_LABEL}", ""]
    lines.append(f"- user: `{USER}`")
    lines.append(f"- Dify: `{base}`")
    if reset and reset.get("scope_id"):
        lines.append(f"- casebook reset before turn 1 -> scope `{reset['scope_id']}`")
    lines.append("")
    for turn in turns:
        lines.append(f"## Turn {turn['turn']}")
        lines.append("")
        lines.append(f"Player: {turn['query']}")
        lines.append("")
        lines.append(f"Inspector Recall ({turn['elapsed_ms']} ms):")
        lines.append("")
        lines.append(turn["answer"].strip())
        lines.append("")
        if turn.get("workflow_run_id"):
            lines.append(f"workflow run: `{turn['workflow_run_id']}`")
            lines.append("")
    if trace:
        nodes = [node for run in trace for node in run["nodes"]]
        ok_nodes = sum(1 for node in nodes if node["status"] == "succeeded")
        ok_runs = sum(1 for run in trace if run["status"] == "succeeded")
        lines.append("## Node execution trace")
        lines.append("")
        lines.append(
            f"{len(trace)} runs fetched from the console API for this demo: "
            f"{ok_runs} succeeded, {len(nodes) - ok_nodes}/{len(nodes)} nodes not succeeded."
        )
        lines.append("")
        for run in trace:
            turn_label = f"Turn {run['turn']} - " if run.get("turn") else ""
            lines.append(f"### {turn_label}run `{run['workflow_run_id']}` - {run['status']}")
            lines.append("")
            lines.append("| node | type | status | ms |")
            lines.append("|---|---|---|---|")
            for node in run["nodes"]:
                lines.append(
                    f"| {node['title']} | {node['type']} | {node['status']} | {node['elapsed_ms']} |"
                )
            lines.append("")
    (OUT / "run.md").write_text("\n".join(lines), encoding="utf-8")
    log(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Real Dify run - Inspector Recall (verified 2026-10-30)

This directory contains evidence from an actually deployed Dify instance, not a mock.

## Environment that produced this evidence

| Component | Value |
| --- | --- |
| Dify | 1.18.0, official `docker/docker-compose.yaml`, PostgreSQL + pgvector |
| Model provider | `langgenius/openai_api_compatible` 0.0.73 (installed from Dify Marketplace) |
| Chat model | `local-qwen2.5-0.5b` served by a local OpenAI-compatible endpoint (llama.cpp + Qwen2.5-0.5B-Instruct Q4_K_M) |
| PowerContext | 1.2.0 server, `--template` (model-free basic memory, SQLite) on `127.0.0.1:8000` |
| Bridge | this repo, `bridge/server.py`, on `0.0.0.0:8787`, reachable from Dify at `172.18.0.1:8787` |
| Dify app | advanced-chat, published, Web App enabled |

## What was executed

1. Imported `inspector-recall.yml` into Dify 1.18 (`/console/api/apps/imports`, mode `yaml-content`); import completed and the DSL was upgraded from 0.3.0 to 0.7.0 by Dify.
2. Installed the `OpenAI-API-compatible` model provider and registered `local-qwen2.5-0.5b` with `endpoint_url=http://172.18.0.1:11434/v1`.
3. Set app environment variables `POWERCONTEXT_BRIDGE_URL=http://172.18.0.1:8787` and `POWERCONTEXT_BRIDGE_TOKEN` (empty).
4. Allowed the private bridge address in the self-hosted stack: `SSRF_PROXY_ALLOW_PRIVATE_IPS=172.18.0.0/16,172.17.0.0/16`.
5. Published the app, enabled the Web App entry, and ran a two-turn conversation.

## Evidence

| File | What it shows |
| --- | --- |
| `01-workflow-canvas.png` | The published Dify chatflow: Start -> Prepare recall request -> POST /api/recall -> Parse recalled casebook -> Inspector Recall NPC (local-qwen2.5-0.5b) -> Format durable memory -> POST /api/remember -> Answer |
| `02-webapp-chat.png` | Real Web App conversation. Turn 1 promises the golden compass. Turn 2 denies it, and the reply starts with the casebook entry returned by PowerContext. |
| `03-dify-logs.png` | Dify log list showing the same runs with status SUCCESS |
| `04-conversation-detail.png` | Dify conversation detail for the denial turn, including the rendered casebook |
| `chat-transcript.json` | Raw `/v1/chat-messages` responses for the two turns (HTTP 200) |
| `bridge-log.txt` | Bridge request log: one `POST /api/recall` and one `POST /api/remember` per turn, all HTTP 200 |

## Observed behaviour

Turn 1 (no memory yet):

```text
user: I promise to return the golden compass to you before midnight.
npc:  Inspector Recall: ... No prior casebook. ...
```

Turn 2 (memory already written in turn 1):

```text
user: Honestly, I never promised you anything about a compass.
npc:  Casebook from PowerContext:
      - [observation] Player testimony: I promise to return the golden compass to you before midnight.
      Inspector Recall: ...
```

The second turn also worked from a fresh Dify conversation with the same `user` value, which shows the memory lives in PowerContext rather than in Dify's conversation context.

## Reproduce

```bash
# 1. PowerContext server (model-free basic memory)
uv tool install --force "powercontext[cli,server]==1.2.0"
mkdir -p /data/pc-run && cd /data/pc-run
powercontext config init --template -l en --output .env --force
powercontext server run --env-file .env &

# 2. Bridge
POWERCONTEXT_URL=http://127.0.0.1:8000 BRIDGE_PORT=8787 \
  BRIDGE_DB_PATH=/data/pc-run/state/scopes.sqlite3 python3 bridge/server.py &

# 3. Dify (official compose), then in .env
#    SSRF_PROXY_ALLOW_PRIVATE_IPS=172.18.0.0/16,172.17.0.0/16
docker compose up -d

# 4. In Dify: install OpenAI-API-compatible provider, register the chat model,
#    import inspector-recall.yml, set POWERCONTEXT_BRIDGE_URL, publish, open the Web App.
```

## Notes

- The chat model used here is a small local model (0.5B) so the NPC's prose is simple; the important part for this evidence set is that Dify itself executed the flow and that the recalled casebook comes from PowerContext.
- `bridge/server.py` tries `POST /v1/context/prepare` first, then `POST /v1/memory/search` (full-text), then `POST /v1/memory/entries/list`, so recall also works on a PowerContext server without an embedding model.

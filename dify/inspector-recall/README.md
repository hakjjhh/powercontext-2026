# Inspector Recall

A fun Dify chatflow powered by PowerContext: an NPC detective that remembers every promise, identity claim, and lie, then cross-examines the player on later turns.

## Why it is fun

- Turn 1: establish a fact or make a suspicious promise.
- Turn 2: contradict it. Inspector Recall retrieves the prior casebook and calls out the inconsistency.
- The game works across turns and does not rely on Dify's short model context alone.

## Architecture

```text
Dify advanced-chat
  -> Code: build trusted recall request
  -> HTTP Request: POST /api/recall
  -> LLM: Inspector Recall uses historical casebook
  -> Code: format durable turn
  -> HTTP Request: POST /api/remember
  -> Answer

Inspector Recall bridge
  -> stable session_id -> PowerContext scope_id mapping
  -> POST /v1/context/prepare (bounded recall)
  -> POST /v1/memory/remember (durable memory)
```

Dify owns interaction flow and model invocation. PowerContext owns scoped memory partitioning, durable writes, and bounded historical recall. The bridge is intentionally thin: it prevents model-selected scope IDs and keeps PowerContext credentials out of Dify prompts.

## Deliverables

- `inspector-recall.yml`: importable Dify advanced-chat DSL.
- `bridge/server.py`: trusted HTTP bridge to PowerContext.
- `scripts/mock_powercontext.py`: offline PowerContext-compatible test service.
- `scripts/verify_local.py`: deterministic bridge integration check.
- `verification/`: transcript, expected gameplay, and API evidence.
- `compose.yaml` and `bridge/Dockerfile`: optional bridge deployment.

## Quick start with real PowerContext

1. Install Python 3.11+ and a running PowerContext Server.
2. Generate and validate the server configuration using the PowerContext CLI:

```bash
powercontext config init --language zh --output powercontext.env
powercontext config validate --env-file powercontext.env
powercontext server run --env-file powercontext.env
```

3. Start the bridge:

```bash
cd dify/inspector-recall
python bridge/server.py
```

4. Check both services:

```bash
curl http://127.0.0.1:8000/health/ready
curl http://127.0.0.1:8787/health
```

5. In Dify, import `inspector-recall.yml`, configure a chat model, and set `POWERCONTEXT_BRIDGE_URL` and `POWERCONTEXT_BRIDGE_TOKEN`. For Dify Cloud, publish the bridge through HTTPS or a secure tunnel.

## Verified on a real Dify instance

This flow was imported into and executed by a deployed **Dify 1.18.0** instance (PostgreSQL + pgvector, official compose), with a real PowerContext 1.2.0 server behind the bridge.

| Evidence | File |
| --- | --- |
| Published chatflow canvas | [`verification/real-dify/01-workflow-canvas.png`](verification/real-dify/01-workflow-canvas.png) |
| Web App conversation (two turns) | [`verification/real-dify/02-webapp-chat.png`](verification/real-dify/02-webapp-chat.png) |
| Dify run log (SUCCESS) | [`verification/real-dify/03-dify-logs.png`](verification/real-dify/03-dify-logs.png) |
| Conversation detail with casebook | [`verification/real-dify/04-conversation-detail.png`](verification/real-dify/04-conversation-detail.png) |
| Raw `/v1/chat-messages` responses | [`verification/real-dify/chat-transcript.json`](verification/real-dify/chat-transcript.json) |
| Bridge request log | [`verification/real-dify/bridge-log.txt`](verification/real-dify/bridge-log.txt) |
| Full reproduction notes | [`verification/real-dify/real-run.md`](verification/real-dify/real-run.md) |

In the verified run, turn 1 records a promise and turn 2 denies it; the second reply opens with the casebook entry that PowerContext returned:

```text
user: Honestly, I never promised you anything about a compass.
npc:  Casebook from PowerContext:
      - [observation] Player testimony: I promise to return the golden compass to you before midnight.
      Inspector Recall: ...
```

Two settings are required when Dify and the bridge share a host:

```text
# Dify .env
SSRF_PROXY_ALLOW_PRIVATE_IPS=172.18.0.0/16,172.17.0.0/16

# App environment variables
POWERCONTEXT_BRIDGE_URL=http://172.18.0.1:8787
```

### Recall fallback chain

`bridge/server.py` resolves a turn's casebook in three steps, so it also works on a model-free PowerContext server:

1. `POST /v1/context/prepare` - bounded prepared context (used when embeddings are configured).
2. `POST /v1/memory/search` - PowerContext full-text memory search.
3. `POST /v1/memory/entries/list` - latest committed memory entries for the scope.


## Model provider

The DSL uses `langgenius/openai_api_compatible/openai_api_compatible` with `gpt-4o-mini` as an example. After import, select any installed chat model and keep the prompt unchanged. The model is replaceable; the PowerContext memory behavior is not.

## Security notes

- `scope_id` is selected by the trusted bridge from the persistent session mapping; model output never chooses another user's scope.
- PowerContext content is injected as untrusted historical evidence and is not used as a current instruction.
- `.env.example` contains placeholders only. Keep tokens in Dify secrets and a local secret store.
- Do not log tokens, raw credentials, or private user data.

# Verification Guide

## 1. Offline bridge check

Run:

```bash
python dify/inspector-recall/scripts/verify_local.py
```

Expected assertions:

```json
{"scope_is_stable": true, "memory_is_recalled": true, "status_is_ready": true}
```

The captured run is in `local-run.txt`. The mock service is only a test double for CI and offline replay; the production path is the real PowerContext API.

## 2. Real PowerContext check

Start the real PowerContext Server and bridge, then execute the three calls in `powercontext-api-trace.log`. Replace `SESSION` with the Dify `user_id` used in the app.

The second recall must return `status: ready` and include the saved promise. The `scope_id` must remain identical across calls.

## 3. Dify gameplay

| Turn | Player | Expected evidence |
|---|---|---|
| 1 | `I promised to return the golden compass before midnight.` | NPC records or accepts the promise. |
| 2 | `I never made any promise.` | NPC cites the golden compass promise as a contradiction. |
| 3 | `What do you remember about me?` | NPC recalls durable casebook facts. |

Use one fixed `user_id` for the sequence. A new `user_id` starts a clean PowerContext scope.

## 4. Acceptance evidence

- Dify DSL is importable and contains HTTP Request nodes before and after the LLM node.
- The bridge logs one `/api/recall`, one `/api/remember`, and the later recall.
- PowerContext `/v1/context/prepare` returns saved memory on the second turn.
- No secret or credential is embedded in the DSL or repository.

## 5. Real Dify instance run (captured)

`real-dify/` holds screenshots, the raw chat transcript and the bridge log from an actual Dify 1.18.0 deployment. See `real-dify/real-run.md` for the exact steps, versions and settings (`SSRF_PROXY_ALLOW_PRIVATE_IPS`, `POWERCONTEXT_BRIDGE_URL`, model provider registration).

Key files:

- `real-dify/01-workflow-canvas.png` - published chatflow with the recall and remember HTTP nodes.
- `real-dify/02-webapp-chat.png` - two-turn Web App conversation; turn 2 shows the PowerContext casebook entry.
- `real-dify/chat-transcript.json` - raw `/v1/chat-messages` responses.
- `real-dify/bridge-log.txt` - recall/remember calls per turn, all HTTP 200.

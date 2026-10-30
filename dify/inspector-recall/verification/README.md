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
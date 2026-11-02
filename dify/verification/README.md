# Verification Guide

## 1. Offline bridge check

Run:

```bash
python dify/scripts/verify_local.py
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

## 5. End-to-end runs (captured)

| 目录 | 地位 | 后端 |
|---|---|---|
| `e2e-seekdb/` | **主交付证据** | 交付 compose 里的 PowerContext 容器，内嵌 seekdb（`DATABASE_KIND=seekdb`） |
| `e2e-mock/` | 辅助自检，不作交付证据 | 无 Docker 时降级为 PowerContext 内置 SQLite，只证明 Dify 调用链 |
| `real-dify/` | 另一套真实 Dify 1.18 环境的人工截图证据 | 见 `real-dify/real-run.md` |

`e2e-seekdb/README.md` lists every artifact and how to reproduce the run
(`dify/scripts/start_all.sh` + `dify/scripts/demo.sh` with `DEMO_OUT_DIR=verification/e2e-seekdb`).
`real-dify/` holds screenshots, the raw chat transcript and the bridge log from an actual Dify
1.18.0 deployment, including the exact settings (`SSRF_PROXY_ALLOW_PRIVATE_IPS`,
`POWERCONTEXT_BRIDGE_URL`, model provider registration).

Key files in `real-dify/`:

- `01-workflow-canvas.png` - published chatflow with the recall and remember HTTP nodes.
- `02-webapp-chat.png` - two-turn Web App conversation; turn 2 shows the PowerContext casebook entry.
- `chat-transcript.json` - raw `/v1/chat-messages` responses.
- `bridge-log.txt` - recall/remember calls per turn, all HTTP 200.

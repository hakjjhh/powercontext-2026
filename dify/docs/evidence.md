# 证据索引

本文件把「DSL 能导入 / 应用能发布 / 每个节点成功 / 记忆真的跨会话复用 / bridge 真被调用 /
后端真的是 seekdb」逐条对应到仓库内的可核查文件。所有证据都可在**无模型 Key**的环境下复跑。

## 1. 主证据：`verification/e2e-seekdb/`（真嵌入式 seekdb）

跑在交付的 `deploy/compose.yaml` 上：PowerContext 容器内嵌 seekdb 引擎
（`POWERCONTEXT_SERVER_DATABASE_KIND=seekdb`，数据目录 `/data/seekdb`），镜像由
`deploy/powercontext-offline.Dockerfile` 从 `deploy/wheelhouse/` 离线构建。

| 组件 | 实际值 |
|---|---|
| Dify | 1.18.0 官方 compose（PostgreSQL + pgvector + ssrf_proxy），`http://127.0.0.1:8080` |
| 模型 | `local-qwen2.5-0.5b`，provider `langgenius/openai_api_compatible` 0.0.73，`endpoint_url=http://mock-llm:8099/v1`，Key=`dummy` |
| PowerContext | 1.2.0 server，容器 `inspector-recall-powercontext` |
| 存储引擎 | 嵌入式 seekdb；内核日志首行 `observer version: OceanBase 1.4.0.0` |
| Bridge | 容器别名 `bridge:8787`，与 Dify 同一 compose 网络 |
| 演示用户 | `e2e-seekdb-player`，跑前 reset 到 scope `scp_1spgb534qh73kyc4tjqgxn0rh5` |

证明点（详见 `verification/e2e-seekdb/README.md`）：

| 文件 | 证明什么 |
|---|---|
| `setup-evidence.json` | DSL 导入并发布成功（`advanced-chat`、站点/API 已启用）、模型生效凭据的 `endpoint_url` |
| `node-trace.json` | **本次 3 个 run**（按节点收到的 query 绑定轮次）3 × 8 节点全部 `succeeded` |
| `chat-transcript.json` | 3 轮均 HTTP 200，含 `bridge_reset.scope_id`、每轮 `answer`/`conversation_id`/`workflow_run_id`/耗时 |
| `run.md` | 上述内容的可读版；开头写明 reset 后的 scope |
| `powercontext-backend.txt` | 正在服务的容器内的 `POWERCONTEXT_SERVER_DATABASE_*` + `PowerContext Server is ready` |
| `seekdb-storage.txt` | `/data/seekdb/{etc,log,run,store}` 目录、`du -sh` 占用、seekdb 引擎日志首行 |
| `seekdb-recall.json` | 跑完后直接从 bridge 召回的 3 条案卷，`scope_id` 与 reset 后一致 |
| `bridge-log.txt` | 本次时间窗 `reset`×1、`recall`×3、`remember`×3 全 200（已剔除健康探测） |
| `pip-check.txt` | 镜像内 `pip check` → `No broken requirements found`（`PyMySQL==1.1.2` + `aiomysql==0.3.2` + `pylibseekdb==1.4.0.post2`） |
| `mock-llm.log` | 3 轮各 1 次 `/v1/chat/completions`，均 200 |

三份 ID 互相印证：`chat-transcript.json` 的每轮 `workflow_run_id` == `node-trace.json` 的
`workflow_run_id`（含 `turn`/`query`）== `run.md` 每轮末尾标注的 run。

## 2. 辅助自检：`verification/e2e-mock/`（SQLite，不作交付证据）

没有 Docker、或 seekdb wheel 拉不下来时的**快速链路自检**：同一套 DSL 与 bridge，后端退化为
PowerContext 默认的 SQLite 文件库。它只能证明「Dify → bridge → PowerContext」这条调用链通，
**不能**证明存储引擎是 seekdb，因此评审应以第 1 节为准。

目录内容与第 1 节同构（`run.md`、`chat-transcript.json`、`node-trace.json`、`bridge-log.txt`、
`mock-llm.log`、`setup-evidence.json`），采集于 2026-11-02，演示用户 `e2e-mock-player`。

## 3. 另一套真实环境：`verification/real-dify/`（2026-10-30）

| 文件 | 证明什么 |
|---|---|
| `01-workflow-canvas.png` | 已发布的 chatflow 画布：Start → recall → parse → LLM → format → remember → Answer |
| `02-webapp-chat.png` | Web App 实际对话：第二轮在新会话中引用第一轮承诺 |
| `03-dify-logs.png` | Dify 运行日志 SUCCESS |
| `04-conversation-detail.png` | 会话详情中的案卷内容 |
| `chat-transcript.json` / `bridge-log.txt` | 原始 HTTP 响应与 bridge 调用记录 |
| `real-run.md` | 复现步骤与环境说明（`SSRF_PROXY_ALLOW_PRIVATE_IPS`、模型供应商注册等） |

## 4. 对话结果（主证据 `e2e-seekdb/run.md`）

| 轮次 | 玩家输入 | 观察到的结果 |
|---|---|---|
| 1 | `I promise to return the golden compass to you before midnight.` | 案卷为空，NPC 记录证词并追问 |
| 2（**新的 Dify 会话**） | `Honestly, I never promised you anything about a compass.` | 回答以 PowerContext 案卷开头，并引用第 1 轮承诺对质 |
| 3 | `What did I promise you before midnight?` | 案卷同时包含承诺与否认两条记录，NPC 再次引用承诺 |

第 2 轮回答（摘自 `chat-transcript.json`）：

```text
Casebook from PowerContext:
- [observation] Player testimony: I promise to return the golden compass to you before midnight.

Inspector Recall: The casebook remembers you, suspect. You swore: “I promise to return
the golden compass to you before midnight.” — and tonight your story changed. ...
```

第 1 轮的 `conversation_id` 与第 2、3 轮不同，Dify 侧没有第 1 轮上下文，说明记忆来自
PowerContext（seekdb）而不是 Dify 的对话窗口。

## 5. 逐节点执行

`node-trace.json` 中 3 次运行的节点顺序与结果完全一致：

| 节点 | 类型 | 状态 |
|---|---|---|
| Start | start | succeeded |
| Prepare recall request | code | succeeded |
| Recall PowerContext casebook | http-request | succeeded |
| Parse recalled casebook | code | succeeded |
| Inspector Recall NPC | llm | succeeded |
| Format durable memory | code | succeeded |
| Save Inspector case note | http-request | succeeded |
| Inspector reply | answer | succeeded |

端到端耗时（控制台 API 的 `elapsed_time`）分别为 435.5 ms、428.8 ms、293.8 ms。

## 6. 两种 seekdb 形态为什么只保留一种

| 形态 | 配置 | 结论 |
|---|---|---|
| 嵌入式 seekdb（交付路径） | `DATABASE_KIND=seekdb` + `DATABASE_PATH=/data/seekdb`，`mysql+aseekdb` + `pylibseekdb`，引擎跑在 PowerContext 进程内 | 已按 compose 端到端跑通，即第 1 节证据 |
| 远程 seekdb / OceanBase（server 模式） | `DATABASE_KIND=oceanbase` + `mysql+aoceanbase://user@host:10000/db?charset=utf8mb4` | 在同一 VM 的 seekdb 1.4.0.0 实例上被 PowerContext 拒绝：`UnsupportedOceanBaseTenantError: OceanBase profile requires a MySQL-compatible tenant; found missing ob_compatibility_mode`；`SHOW VARIABLES LIKE 'ob_compatibility_mode'` 返回空，而 `@@version = 5.7.25-OceanBase seekdb-v1.4.0.0` |

也就是说：远程模式要求租户暴露 `ob_compatibility_mode`，seekdb 单机版不提供该变量，因此
**嵌入式 seekdb 是当前 seekdb 版本下唯一可行路径**；且两种形态共用同一套
`pylibseekdb`/`aiomysql` 写入栈，第 1 节暴露的 `pymysql` 兼容问题对两者同样成立。
另外远程模式还需要为 PowerContext 单独建库建用户，交付材料只用嵌入式，评审无需外部数据库。

## 7. 复现命令

```bash
# 0) 可选：一次下载 wheelhouse，供原生安装与镜像构建共用（无网环境自备 deploy/wheelhouse/）
bash dify/scripts/fetch_wheelhouse.sh

# 1) powercontext(内嵌 seekdb) + bridge + mock-llm
bash dify/scripts/start_all.sh

# 2) 注册模型供应商 -> 导入 DSL -> 发布 -> 建 API Key
export DIFY_BASE_URL=http://127.0.0.1:8080
export DIFY_EMAIL=you@example.com DIFY_PASSWORD=******
python3 dify/scripts/dify_setup.py

# 3) 三轮演示并收集证据（脚本会先 reset 案卷）
export DEMO_OUT_DIR=verification/e2e-seekdb
export DEMO_LABEL="Embedded seekdb end-to-end run (compose + offline wheelhouse image)"
export DEMO_USER=e2e-seekdb-player
bash dify/scripts/demo.sh
```

`dify_setup.py` 每次都会重新导入并发布一个新应用，避免沿用上一次的旧凭据或未发布状态；
`demo.sh` 的日志类证据按 `--since` 只取本次时间窗，不会混入上一次尝试。

## 8. 已知边界

- `mock_llm.py` 是确定性的验证替身，只用于证明链路；换成任意 OpenAI 兼容模型即可。
- 主证据依赖 Docker 与 `deploy/wheelhouse/`（或联网构建镜像）；两者缺失时按第 2 节的
  SQLite 自检降级，但那不能证明 seekdb。
- `pymysql` 必须 `<1.2`：`aiomysql 0.3.2` 依赖 `pymysql.converters.escape_bytes_prefixed`，
  1.2 起该符号被移除，seekdb 写入会全部失败。两个 Dockerfile 与 `start_all.sh` 的 uv 安装
  路径都已钉版，钉版后的一致性由 `pip-check.txt` 佐证。
- 未修改任何评测输入、题库或平台表单；仓库内不含真实 Key（模型凭据是占位 `dummy`）。

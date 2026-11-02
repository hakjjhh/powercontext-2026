# 设计说明 · Inspector Recall

## 1. 目标

Dify 赛道要求同时证明四件事：

1. **真实使用 Dify**：作品是一个可导入、可发布、可对话的 Dify 应用；
2. **真实使用记忆组件**：跨会话记住玩家说过的话，并在后续主动复用；
3. **不只是提示词**：记忆由外部组件（PowerContext + seekdb）持久化，而不是塞在 Dify 会话历史里；
4. **可复现**：评测环境没有模型 Key 时也能跑通全链路。

Inspector Recall 用一个侦探 NPC 把「记忆复用」变成玩法：玩家撒谎/立誓，下一轮被案卷对质。

## 2. 组件

| 组件 | 职责 | 不负责 |
|---|---|---|
| Dify 1.18 advanced-chat | 节点编排、模型调用、对用户输出 | 不存长期记忆、不持有 PowerContext 凭据 |
| PowerContext 1.2.0 | scope 隔离、记忆写入、受限召回 | 不做对话编排 |
| seekdb | PowerContext 的存储与检索后端（`POWERCONTEXT_SERVER_DATABASE_KIND=seekdb`） | 不直接暴露给 Dify |
| bridge | 把 Dify 的 `user_id` 映射为稳定的 `scope_id`，代理 PowerContext HTTP API | 不生成回答、不存业务数据 |
| mock LLM | 无 Key 时的 OpenAI 兼容模型 `local-qwen2.5-0.5b` | 不追求对话质量，只保证链路可执行 |

## 3. 数据流

```text
Dify advanced-chat
  Start
    -> Code "Prepare recall request"      {session_id: sys.user_id, query, max_bytes}
    -> HTTP "Recall PowerContext casebook" POST {POWERCONTEXT_BRIDGE_URL}/api/recall
    -> Code "Parse recalled casebook"     content/brief/prefix/scope_id/status
    -> LLM "Inspector Recall NPC"         CASEBOOK + CURRENT TESTIMONY
    -> Code "Format durable memory"       Player testimony + NPC reply
    -> HTTP "Remember this turn"          POST {POWERCONTEXT_BRIDGE_URL}/api/remember
    -> Answer
```

bridge 内部：

```text
POST /api/recall
  session_id -> scope_id（sqlite 持久映射，首次调用创建 scope）
  -> POST /v1/context/prepare   （有 embedding 时的受限上下文）
  -> POST /v1/memory/search     （全文检索回退）
  -> POST /v1/memory/entries/list（最新条目回退）
  -> {content, brief, status, retrieval, hit_count, scope_id}

POST /api/remember
  -> POST /v1/memory/remember
  -> {citation, memory_ref, scope_id}
```

三级召回回退保证：即使 PowerContext 没有配置 embedding 模型，也能用全文检索/条目列表召回，
所以「无模型 Key」不仅指 LLM，也指记忆组件侧。

## 4. 为什么需要 bridge

- **凭据隔离**：PowerContext token 只在 bridge 的进程环境里，Dify 提示词/DSL 中不出现。
- **scope 可信**：`scope_id` 由服务端根据 `session_id` 生成并持久化，模型无法指定别人的 scope。
- **协议收窄**：Dify 只需要两个稳定的 JSON 接口，不必理解 PowerContext 的检索细节。
- **可测试**：`scripts/mock_powercontext.py` 可以在没有 PowerContext 的情况下验证 bridge 行为。

## 5. seekdb 的位置

PowerContext 的存储后端由 `POWERCONTEXT_SERVER_DATABASE_KIND` 选择（`sqlite` / `seekdb` / `oceanbase`）。
本作品使用 **seekdb**：

```yaml
POWERCONTEXT_SERVER_DATABASE_KIND: seekdb
POWERCONTEXT_SERVER_DATABASE_PATH: /data/seekdb
```

`deploy/compose.yaml` 中 `powercontext` 服务挂载 `powercontext-data:/data`，
seekdb 数据落在 `/data/seekdb`，由容器内 `powercontext server run` 打开。
这也是 `start_all.sh` 输出里 “seekdb embedded” 的含义：评测只需启动一个 powercontext 服务，
seekdb 引擎随进程启动，无需单独的数据库实例与连接串。

两个工程约束：

- `pymysql` 必须钉在 `>=1.1,<1.2`。`aiomysql 0.3.2` 从 `pymysql.converters` 导入
  `escape_bytes_prefixed`，pymysql 1.2 起该符号被替换成字符串占位，内嵌 seekdb 的每次写入都会以
  `TypeError: 'str' object is not callable` 失败；两个 Dockerfile 与 `start_all.sh` 的 uv 安装
  路径都已钉版，镜像内 `pip check` 的输出见 `verification/e2e-seekdb/pip-check.txt`。
- 远程模式（`DATABASE_KIND=oceanbase` + `mysql+aoceanbase://…?charset=utf8mb4`）在 seekdb 1.4.0.0
  单机实例上会被 PowerContext 拒绝（`UnsupportedOceanBaseTenantError`，因为该实例不暴露
  `ob_compatibility_mode`），所以交付只用内嵌 seekdb；`prepare_seekdb_db.sh` 只在真正的
  OceanBase MySQL 租户上才需要。

若环境无法下载 seekdb wheel，可把 `POWERCONTEXT_SERVER_DATABASE_KIND` 改为 `sqlite`
使用 PowerContext 内置 SQLite 后端；`verification/e2e-mock/` 就是该模式下的快速自检，
只证明 Dify 链路，主证据是 `verification/e2e-seekdb/`（compose 默认为 seekdb）。

## 6. 无 Key 设计

| 依赖 | 评测环境 | 方案 |
|---|---|---|
| LLM | 可能没有 Key | `scripts/mock_llm.py` 实现 `/v1/models`、`/v1/chat/completions`（含 SSE）、`/v1/embeddings` |
| 模型供应商 | 可能没装 | `scripts/dify_setup.py` 检测并按需安装 `langgenius/openai_api_compatible:0.0.73` |
| 模型注册 | 之前失败的主要原因 | `dify_setup.py` 自动写 `models/credentials`，模型名固定 `local-qwen2.5-0.5b` |
| DSL 导入 | 手工步骤易漏 | `dify_setup.py` 调 `/console/api/apps/imports`，并检查 `status` |
| 应用发布 | 导入后未发布会导致调用失败 | `dify_setup.py` 调 `/workflows/publish` 并创建 API Key |

mock 模型的回复由 prompt 中的 `CASEBOOK:` 块决定：有案卷时优先引用含承诺的记忆并质问，
没有案卷时记录证词。这样「记忆是否被复用」可以从回答文本直接判断，而不依赖模型随机性。

## 7. 服务发现

| 调用方 | 被调方 | 地址 |
|---|---|---|
| Dify 容器 | bridge | `http://bridge:8787` |
| Dify 容器 | mock LLM | `http://mock-llm:8099/v1` |
| bridge 容器 | PowerContext | `http://powercontext:8000` |

`deploy/compose.yaml` 默认加入外部网络 `${DIFY_NETWORK}`；`start_all.sh` 会从运行中的
`langgenius/dify-api` 容器自动探测网络名，保证 Dify 与三个服务在同一网络内，
因此全部使用服务名而不是 `localhost`。

## 8. 安全与边界

- DSL / 仓库中不含真实 Key，`.env.example` 全部为占位值。
- 召回内容在系统提示中被明确标注为「不可信历史证据」，不作为指令执行。
- bridge 的 `session_id` 长度受限（1–160），body 大小受限（1 MB）。
- `BRIDGE_TOKEN` 可选；配置后 Dify 通过 app 环境变量 `POWERCONTEXT_BRIDGE_TOKEN` 传入。
- 评测输入（题库、平台表单）未修改，改动仅限 `dify/` 材料与脚本。

# 运行手册

## 0. 前置

| 依赖 | 版本 | 说明 |
|---|---|---|
| Dify | 1.18.0 | 官方 docker compose 自部署；需管理员账号 |
| Python | 3.9+ | 脚本仅用标准库 |
| Docker | 可选 | 有 docker 时 `start_all.sh` 用 compose；没有则 native |
| PowerContext | 1.2.0（含 seekdb extra） | native 模式需要 `powercontext` CLI |

## 1. 启动记忆后端

```bash
bash dify/scripts/start_all.sh
```

脚本行为：

1. 若检测到运行中的 Dify 容器，取其 docker 网络名，否则使用并创建 `inspector-recall-net`；
2. `docker compose up -d --build` 启动 `powercontext`（内嵌 seekdb）、`bridge`、`mock-llm`；
3. 轮询 `http://127.0.0.1:8787/health`、`http://127.0.0.1:8099/v1/models` 和 powercontext 健康状态；
4. 打印服务地址与 Dify 需要的配置项。

没有 docker 时强制 native：

```bash
bash dify/scripts/start_all.sh native
```

native 模式要求：

```bash
uv tool install "powercontext[cli,server,seekdb]==1.2.0" --with "pymysql>=1.1,<1.2"
# seekdb wheel 不可用时改用内置 SQLite：
POWERCONTEXT_SERVER_DATABASE_KIND=sqlite \
  uv tool install "powercontext[cli,server]==1.2.0"
```

`--with "pymysql>=1.1,<1.2"` 不能省：`aiomysql 0.3.2` 依赖 `pymysql.converters.escape_bytes_prefixed`，
pymysql 1.2 起该符号被移除，内嵌 seekdb 的所有写入都会 502。

native 模式产物在 `dify/.runtime/`：`powercontext.env`、`powercontext.log`、`bridge.log`、`mock-llm.log`、`*.pid`。

停止：

```bash
bash dify/scripts/stop_all.sh
```

## 2. Dify 侧配置

### 2.1 自动（推荐）

```bash
export DIFY_BASE_URL=http://127.0.0.1:8080
export DIFY_EMAIL=you@example.com
export DIFY_PASSWORD='your-password'
python3 dify/scripts/dify_setup.py
```

脚本依次执行：

| 步骤 | 接口 |
|---|---|
| 登录 | `POST /console/api/login`（密码 base64） |
| 检查/安装供应商 | `GET /console/api/workspaces/current/model-providers`、`POST /plugin/install/marketplace` |
| 注册模型 | `POST /workspaces/current/model-providers/<provider>/models/credentials` |
| 导入 DSL | `POST /console/api/apps/imports`（`mode=yaml-content`） |
| 发布 | `POST /console/api/apps/<app_id>/workflows/publish` |
| 建 API Key | `POST /console/api/apps/<app_id>/api-keys` |

运行状态写入 `dify/.runtime/setup.json`（已 gitignore）。可选环境变量：

| 变量 | 默认 | 用途 |
|---|---|---|
| `MODEL_BASE_URL` | `http://mock-llm:8099/v1` | 模型地址（容器内用服务名） |
| `BRIDGE_URL` | `http://bridge:8787` | 写入 DSL 的 `POWERCONTEXT_BRIDGE_URL` |
| `DIFY_MODEL` | `local-qwen2.5-0.5b` | 模型名 |
| `PLUGIN_ID` | `langgenius/openai_api_compatible:0.0.73` | 供应商插件 |

### 2.2 若 Dify 与 bridge 不在同一 docker 网络

把 Dify 的 compose 网络加入 `DIFY_NETWORK`：

```bash
DIFY_NETWORK=docker_default bash dify/scripts/start_all.sh
```

或在 Dify 的 `.env` 中放行私网地址（自建 Dify 默认会拦截内部 IP）：

```text
SSRF_PROXY_ALLOW_PRIVATE_IPS=172.16.0.0/12,10.0.0.0/8
```

然后重新 `docker compose up -d` 使 `ssrf_proxy` 生效。

### 2.3 手动（与自动步骤等价）

1. Plugins → Marketplace → 安装 **OpenAI-API-compatible**；
2. Settings → Model Provider → 添加模型：Base URL `http://mock-llm:8099/v1`，Key `dummy`，模型 `local-qwen2.5-0.5b`；
3. Studio → Import DSL → `dify/dsl/inspector-recall.yml`；
4. 检查环境变量 `POWERCONTEXT_BRIDGE_URL=http://bridge:8787`；
5. 发布 → 创建 API Key。

## 3. 运行演示

```bash
bash dify/scripts/demo.sh
```

`demo.sh` 会先调 `POST /api/reset`（`DEMO_BRIDGE_URL`，默认 `http://127.0.0.1:8787`）把演示用户
的案卷清空，因此第一轮必定从空案卷开始；随后收集**本次时间窗**内的 bridge/mock/引擎证据。

默认产物写入 `dify/verification/e2e-mock/`，主证据请显式指定目录：

```bash
export DEMO_OUT_DIR=verification/e2e-seekdb
export DEMO_LABEL="Embedded seekdb end-to-end run (compose + offline wheelhouse image)"
export DEMO_USER=e2e-seekdb-player
bash dify/scripts/demo.sh
```

| 文件 | 内容 |
|---|---|
| `chat-transcript.json` | 三轮 `/v1/chat-messages` 响应 + reset 后的 `scope_id` + 每轮 `workflow_run_id` |
| `node-trace.json` | 本次每个 workflow run 的逐节点状态/耗时（按节点里的 query 绑定轮次） |
| `bridge-log.txt` | `/api/reset`、`/api/recall`、`/api/remember` 调用日志（仅本次时间窗） |
| `mock-llm.log` | mock 模型请求日志 |
| `powercontext-backend.txt`、`seekdb-storage.txt`、`seekdb-recall.json`、`pip-check.txt` | 由 `demo.sh` 在服务中的容器上采集的后端证据（`DATABASE_KIND`、`/data/seekdb`、跑完后的召回、`pip check`） |
| `run.md` | 人读版总结（含节点 trace 表格） |
| `setup-evidence.json` | 应用/模型/生效凭据的导入后状态 |

## 4. 评测环境建议

1. 先跑 `python3 dify/scripts/verify_materials.py`，确认材料完整且无泄漏；
2. 启动后端后 `curl http://<bridge>:8787/health`，应返回 `{"ok": true, "powercontext_status": 200}`；
3. 执行 `dify_setup.py` 后，`setup.json` 中应包含 `app_id` 与 `api_key`；
4. 最后跑 `demo.py`，第二轮回答必须以 `Casebook from PowerContext:` 开头。

## 5. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| DSL 导入成功但运行报 “model not found” | 供应商未注册/名称不一致 | 重跑 `dify_setup.py`；确认模型名 `local-qwen2.5-0.5b` |
| LLM 节点报 401/连接失败 | Base URL 不可达 | 容器内用 `http://mock-llm:8099/v1`；宿主调试用 `http://127.0.0.1:8099/v1` |
| HTTP 节点报 SSRF blocked | Dify 默认拦截私网 | 配置 `SSRF_PROXY_ALLOW_PRIVATE_IPS` 并重启 ssrf_proxy |
| HTTP 节点 502 / bridge 连不上 PowerContext | PowerContext 未就绪 | `start_all.sh` 会等健康检查；查看 `.runtime/powercontext.log` |
| 第二轮没有案卷 | 第一轮 remember 失败，或换了 `user` | 用同一 `user`；检查 `bridge-log.txt` 是否有 200 |
| `resources.enter_async_context` / `SeekDBUnavailableError` | 缺 seekdb extra | `uv tool install "powercontext[cli,server,seekdb]==1.2.0" --with "pymysql>=1.1,<1.2"` |
| `/api/remember` 返回 502，PowerContext 日志 `TypeError: 'str' object is not callable` | pymysql 被解析到 >=1.2，`aiomysql 0.3.2` 取不到 `escape_bytes_prefixed` | 装成 `pymysql>=1.1,<1.2`（两个 Dockerfile 已钉版），`python -m pip check` 应无冲突 |
| 端口 8787/8099 被占用 | 本机已有服务 | `BRIDGE_PORT=... MOCK_LLM_PORT=...` 或先 `stop_all.sh` |

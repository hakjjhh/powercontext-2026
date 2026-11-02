# e2e-seekdb —— 主交付证据（真 seekdb 后端）

本目录是本作品的**主证据**：三轮演示跑在交付的 `deploy/compose.yaml` 上，
PowerContext 容器使用**嵌入式 seekdb**（`POWERCONTEXT_SERVER_DATABASE_KIND=seekdb`，
引擎与 PowerContext 同进程，数据目录 `/data/seekdb`）。

`verification/e2e-mock/` **不是**交付证据：那是在没有 Docker/没有 seekdb wheel 的环境里
用 SQLite 后端做的快速链路自检，只证明 Dify 侧调用链，不能证明 seekdb。

## 运行环境（2026-11-02，采集自运行中的容器）

| 组件 | 实际值 |
|---|---|
| Dify | 1.18.0 官方 compose（PostgreSQL + pgvector + ssrf_proxy），`http://127.0.0.1:8080` |
| 模型 | `local-qwen2.5-0.5b`，provider `langgenius/openai_api_compatible` 0.0.73，`endpoint_url=http://mock-llm:8099/v1` |
| PowerContext | 1.2.0 server，镜像 `inspector-recall-powercontext`（由 `deploy/powercontext-offline.Dockerfile` 从本地 wheelhouse 构建） |
| 存储引擎 | 嵌入式 seekdb，`POWERCONTEXT_SERVER_DATABASE_KIND=seekdb`、`POWERCONTEXT_SERVER_DATABASE_PATH=/data/seekdb` |
| seekdb 内核 | `OceanBase 1.4.0.0`（`revision 1-bf450b4c…`，见 `seekdb-storage.txt` 引擎日志首行） |
| Bridge | `backend/bridge/server.py`，容器别名 `bridge:8787`，与 Dify 同一网络 |
| 演示用户 | `e2e-seekdb-player`，跑前 reset 到 scope `scp_1spgb534qh73kyc4tjqgxn0rh5` |

## 文件清单

| 文件 | 证明什么 |
|---|---|
| `run.md` | 可读版：3 轮对话 + 每轮的 `workflow_run_id` + 节点表；开头记录 reset 后的 scope |
| `chat-transcript.json` | 原始 `/v1/chat-messages` 响应：`bridge_reset.scope_id`、每轮 `answer`、`conversation_id`、`workflow_run_id`、耗时 |
| `node-trace.json` | 控制台 API 取回的**本次 3 个 run**（按节点里实际收到的 query 与轮次绑定），3 × 8 节点全部 `succeeded` |
| `powercontext-backend.txt` | **正在服务的那个容器**的 `POWERCONTEXT_SERVER_DATABASE_*` 与启动日志（`PowerContext Server is ready`） |
| `seekdb-storage.txt` | 容器内 `/data/seekdb` 目录结构（`etc/log/run/store`）、`du -sh` 占用、seekdb 引擎日志首行 |
| `seekdb-recall.json` | 跑完后直接从 bridge `/api/recall` 读到的 3 条案卷（`scope_id` 与 reset 后一致，含 `entry_id`/`revision`） |
| `bridge-log.txt` | 本次时间窗内的 bridge 调用：`/api/reset` ×1、`/api/recall` ×3、`/api/remember` ×3，均 200（已剔除健康探测） |
| `mock-llm.log` | 3 轮各触发 1 次 `/v1/chat/completions`，均 200 |
| `pip-check.txt` | 镜像内 `python -m pip check` → `No broken requirements found`（`PyMySQL==1.1.2`、`aiomysql==0.3.2`、`pylibseekdb==1.4.0.post2`、`SQLAlchemy==2.1.6`、`pyobvector==0.2.29`） |
| `setup-evidence.json` | DSL 导入并发布成功后的应用信息（`advanced-chat`、站点/API 已启用）与模型凭据 `endpoint_url` |

三份 ID 互相印证：`chat-transcript.json` 每轮的 `workflow_run_id` == `node-trace.json` 的
`workflow_run_id`（并带 `turn`/`query` 字段）== `run.md` 各轮末尾标注的 run。

## 关键结论

1. **记忆在 PowerContext，不在 Dify**：第 1 轮用 `conversation_id` A，第 2、3 轮走全新的
   会话 B（`chat-transcript.json` 可见），第 2 轮回答仍引用第 1 轮的承诺。
2. **引擎确实是 seekdb**：`powercontext-backend.txt` 与 `seekdb-storage.txt` 都取自运行中的
   容器；`/data/seekdb/store` 约 257 MB、`/data/seekdb/log` 约 385 MB 是 OB 内核自己的
   sstable/redo/日志布局，SQLite 后端不会产生这种目录。
3. **写入真的落到案卷**：`seekdb-recall.json` 的 3 条 `Player testimony` 与三轮输入逐条对应。
4. **依赖钉版无冲突**：`pymysql` 钉在 `>=1.1,<1.2`（`aiomysql 0.3.2` 需要
   `pymysql.converters.escape_bytes_prefixed`，1.2 起该符号被移除，所有 seekdb 写入会以
   `TypeError: 'str' object is not callable` 失败），`pip-check.txt` 是钉版后的一致性输出。

## 复现

```bash
# 一次下载 wheelhouse，供 VM 原生安装与镜像构建共用
bash dify/scripts/fetch_wheelhouse.sh          # 需要联网；离线则自备 deploy/wheelhouse/
bash dify/scripts/start_all.sh                 # compose：powercontext(内嵌 seekdb)+bridge+mock-llm

export DIFY_BASE_URL=http://127.0.0.1:8080
export DIFY_EMAIL=... DIFY_PASSWORD=...        # 仅本地 Dify 管理员账号
python3 dify/scripts/dify_setup.py             # 注册模型 -> 导入 DSL -> 发布 -> 建 API Key

export DEMO_OUT_DIR=verification/e2e-seekdb
export DEMO_LABEL="Embedded seekdb end-to-end run (compose + offline wheelhouse image)"
export DEMO_USER=e2e-seekdb-player
bash dify/scripts/demo.sh                      # 自动 reset 案卷、跑 3 轮、抓全部证据
```

`demo.sh` 会先调 `/api/reset` 再跑第 1 轮，因此第 1 轮的案卷必然为空；日志类证据按
`--since` 只取本次时间窗，不会混入上一次尝试。

## 关于「为什么不用远程 seekdb/OceanBase」

交付路径用的是嵌入式 seekdb；远程（server 模式）走 `POWERCONTEXT_SERVER_DATABASE_KIND=oceanbase`
+ `mysql+aoceanbase://…?charset=utf8mb4`。在同一台 VM 的 seekdb 1.4.0.0 实例上实测：

```text
powercontext.builtin.persistence.oceanbase.profile.UnsupportedOceanBaseTenantError:
OceanBase profile requires a MySQL-compatible tenant; found missing ob_compatibility_mode
```

`SHOW VARIABLES LIKE 'ob_compatibility_mode'` 在该实例上返回空，PowerContext 因此拒绝连接；
而 `@@version` 为 `5.7.25-OceanBase seekdb-v1.4.0.0`，说明引擎本身正常。也就是说：**嵌入式
seekdb 是当前 seekdb 版本下唯一可跑的 seekdb 路径，而它正好等于交付 compose 的路径**，
两种形态用的是同一个 `pylibseekdb`/`aiomysql` 写入栈。

## 隐私

目录内不含任何 Key、口令或 DSN 密码：模型凭据是占位 `dummy`，PowerContext 未启用鉴权
（`POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true`），env 抓取只过滤
`POWERCONTEXT_SERVER_DATABASE_*`，且脚本对 `user:password@host` 形式统一打码。

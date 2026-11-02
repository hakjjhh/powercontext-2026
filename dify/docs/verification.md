# 验证说明

本作品按「材料自检 → 离线 bridge → 真实 PowerContext → 真实 Dify 端到端」四层验证，
任何一层都可以独立复跑。

## 1. 材料自检（无需任何服务）

```bash
python3 dify/scripts/verify_materials.py
```

覆盖：

- DSL 存在且包含 `recall`/`remember` 两个 HTTP 节点、LLM 节点、Answer 节点；
- DSL 引用 `local-qwen2.5-0.5b` 与 `langgenius/openai_api_compatible`；
- DSL 默认地址不含 `localhost`/`127.0.0.1`；
- compose 使用服务名 `powercontext:8000` 与 `seekdb`；
- `dify_setup.py` 覆盖注册、导入、发布、API Key 四步；
- 仓库中没有 `sk-` 形式的真实 Key；
- 真实 Dify transcript 全部为 HTTP 200。

预期最后一行：`all material checks passed`。

## 2. 离线 bridge 集成测试（不需要 PowerContext）

```bash
python3 dify/scripts/verify_local.py
```

启动 `mock_powercontext.py` 作为 PowerContext 测试替身，断言：

```json
{"scope_is_stable": true, "memory_is_recalled": true, "status_is_ready": true}
```

结果留档于 `verification/local-run.txt`。

## 3. 真实 PowerContext

启动 `powercontext`（compose 默认 `DATABASE_KIND=seekdb`；无网络时可用内置 SQLite）与 bridge 后：

```bash
curl -s http://127.0.0.1:8000/health/ready
curl -s http://127.0.0.1:8787/health
```

`powercontext-api-trace.log` 记录了三次调用的真实响应：

1. `POST /v1/scopes` 创建 scope；
2. `POST /v1/memory/remember` 写入玩家承诺；
3. `POST /v1/context/prepare` 在召回时返回同一 scope 下的记忆。

验收点：`scope_id` 三次一致；第二次召回 `status=ready` 且包含承诺原文。

## 4. 真实 Dify 端到端

### 4.1 自动脚本

```bash
bash dify/scripts/start_all.sh
python3 dify/scripts/dify_setup.py      # 需 DIFY_EMAIL/DIFY_PASSWORD
bash dify/scripts/demo.sh
```

### 4.2 验收清单

| 检查项 | 通过标准 | 证据（`verification/e2e-seekdb/`） |
|---|---|---|
| DSL 可导入 | `/apps/imports` 返回 `completed`（允许 warnings） | `dify_setup.py` 日志、`setup-evidence.json` |
| 应用已发布 | `/workflows/publish` 返回 success | `setup-evidence.json`（`mode=advanced-chat`、`enable_api=true`） |
| 全节点成功 | 每个 workflow run 的节点 `status=succeeded` | `node-trace.json`（3 run × 8 节点） |
| 记忆实际复用 | 第二轮（新会话）回答包含第一轮承诺 | `chat-transcript.json`、`run.md` |
| 真实组件接入 | bridge 日志有 `/api/recall`、`/api/remember` 各 200 | `bridge-log.txt`（reset×1 + recall×3 + remember×3） |
| 后端确为 seekdb | 服务中的容器 `DATABASE_KIND=seekdb`，`/data/seekdb` 有 OB 内核目录与日志 | `powercontext-backend.txt`、`seekdb-storage.txt` |
| 案卷真的落盘 | 跑完后从 bridge 直接召回到 3 条证词，scope 与 reset 后一致 | `seekdb-recall.json` |
| 依赖钉版一致 | 镜像内 `pip check` 无冲突 | `pip-check.txt` |
| 无模型 Key | 模型为本地 mock，Key 为 `dummy` | `scripts/mock_llm.py`、`deploy/compose.yaml`、`mock-llm.log` |

`verification/e2e-mock/` 是同一份清单在无 Docker（SQLite 后端）下的快速自检结果，
不能证明存储引擎，只作辅助；`verification/real-dify/` 是另一套真实 Dify 环境的人工截图证据。

### 4.3 人工判定样例

第一轮（案卷为空）：

```text
user: I promise to return the golden compass to you before midnight.
npc:  Inspector Recall: The casebook is empty, so this is your first testimony...
```

第二轮（**新** Dify 会话，同一 user）：

```text
user: Honestly, I never promised you anything about a compass.
npc:  Casebook from PowerContext:
      - [observation] Player testimony: I promise to return the golden compass to you before midnight.

      Inspector Recall: The casebook remembers you, suspect...
```

第二轮回答开头的案卷来自 PowerContext；Dify 会话里没有第一轮内容，证明记忆在外部组件中。

## 5. 官方关注点的对应关系

| 官方常见失败原因 | 本仓库的应对 |
|---|---|
| 交付材料不完整 | 顶层 README + dsl/ + backend/ + deploy/ + docs/ + scripts/ + verification/ |
| DSL 无法正常执行 | 单一 DSL，节点参数完整；真实 Dify 1.18 导入执行通过 |
| 项目无法启动 | `start_all.sh` 一键启动，native/docker 双模式，健康检查阻塞等待 |
| 服务无法连通 | 容器内统一服务名；compose 显式依赖与 healthcheck |
| 记忆没有实际复用 | 第二轮换新会话仍能召回；`node-trace.json` + transcript 双重证据 |
| 没有真实组件接入 | 真实 PowerContext 1.2.0 + 真实 Dify 1.18，非纯提示词（seekdb 由 compose 固定） |
| 验证范围不足 | 材料/离线/真实 PC/真实 Dify 四层验证 |

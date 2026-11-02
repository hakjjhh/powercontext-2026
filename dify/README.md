# Dify 赛道 · Inspector Recall

> Dify × OceanBase 大赛作品
> **Dify** 负责对话编排 · **PowerContext（seekdb 存储）** 负责跨会话记忆 · **无需模型 Key** 即可完整复现。

Inspector Recall 是一个「记得你说过的每一句谎话」的侦探 NPC。第一轮玩家立下承诺，
下一轮（**新会话**）推翻它，NPC 会从 PowerContext 取回上一轮案卷并当场对质。
记忆不存在 Dify 的会话上下文里，而是在 PowerContext + seekdb 中，所以换一个 Dify 会话仍然能召回。

## 目录

```
dify/
├── README.md                      本文件（提交入口）
├── dsl/
│   └── inspector-recall.yml       Dify advanced-chat DSL（唯一一份，导入即用）
├── backend/bridge/
│   └── server.py                  Dify -> PowerContext 的可信桥（标准库，无三方依赖）
├── deploy/
│   ├── compose.yaml               powercontext(内嵌 seekdb) + bridge + mock-llm
│   ├── powercontext.Dockerfile    PowerContext 1.2.0 + 内嵌 seekdb（联网构建）
│   └── powercontext-offline.Dockerfile  同上，但从 deploy/wheelhouse/ 离线构建
├── scripts/
│   ├── mock_llm.py                OpenAI 兼容 mock 模型（local-qwen2.5-0.5b，支持 SSE）
│   ├── fetch_wheelhouse.sh        一次下载全部 wheel，原生安装与镜像构建共用
│   ├── start_all.sh               一键启动 seekdb/PowerContext + bridge + mock
│   ├── stop_all.sh                停止上述服务
│   ├── dify_setup.py              自动注册模型供应商 -> 导入 DSL -> 发布 -> 建 API Key
│   ├── demo.py                    三轮对话 + 拉取逐节点执行 trace
│   ├── demo.sh                    demo + 收集 bridge/mock/引擎证据
│   ├── prepare_seekdb_db.sh       可选：为远程 seekdb/OceanBase 模式建库建用户并留证
│   ├── verify_materials.py        材料静态自检（无 Key、无 localhost、无泄漏）
│   ├── verify_local.py            离线 bridge 集成测试
│   └── mock_powercontext.py       离线 PowerContext 测试替身
├── docs/
│   ├── design.md                  架构与设计取舍
│   ├── run.md                     运行手册（docker / native / 评测环境）
│   ├── verification.md            验证步骤与预期结果
│   └── evidence.md                证据索引（截图 / trace / 日志对应关系）
└── verification/
    ├── e2e-seekdb/                主交付证据：compose + 内嵌 seekdb 的端到端运行
    ├── e2e-mock/                  无 Docker 时的 SQLite 快速自检（非交付证据）
    ├── real-dify/                 真实 Dify 1.18 运行证据（截图 + transcript + bridge log）
    ├── local-run.txt              离线 bridge 断言结果
    └── powercontext-*.            真实 PowerContext API trace
```

## 一键启动（无需模型 Key）

```bash
# 1) 启动 seekdb(内嵌于 PowerContext) + bridge + mock LLM
bash dify/scripts/start_all.sh            # 有 docker 用 compose，没有则 native

# 2) 在 Dify 1.18 中自动完成：装插件 -> 注册模型 -> 导入 DSL -> 发布 -> 建 API Key
export DIFY_BASE_URL=http://127.0.0.1:8080
export DIFY_EMAIL=you@example.com DIFY_PASSWORD=******
python3 dify/scripts/dify_setup.py

# 3) 跑三轮演示并收集证据
bash dify/scripts/demo.sh
```

Dify 侧只需要两处设置（脚本会尽量自动完成）：

| 项 | 值 | 说明 |
|---|---|---|
| 模型供应商 | `langgenius/openai_api_compatible` | `dify_setup.py` 缺失时自动安装 |
| 模型 / Base URL | `local-qwen2.5-0.5b` / `http://mock-llm:8099/v1` | mock 模型，Key 填 `dummy` |
| 应用环境变量 | `POWERCONTEXT_BRIDGE_URL=http://bridge:8787` | DSL 默认值，导入时按 `BRIDGE_URL` 覆盖 |
| SSRF 白名单 | `SSRF_PROXY_ALLOW_PRIVATE_IPS=<共享网段>` | 仅自建 Dify 需要 |

> 容器之间一律用**服务名**互相访问（`powercontext:8000`、`bridge:8787`、`mock-llm:8099`），
> 不使用 `localhost`/`127.0.0.1`。`start_all.sh` 会自动找到正在运行的 Dify 容器所在 docker 网络并加入。

## 验证样例

| 轮次 | 玩家 | 预期 |
|---|---|---|
| 1 | `I promise to return the golden compass to you before midnight.` | NPC 记下承诺（案卷为空） |
| 2（新会话） | `Honestly, I never promised you anything about a compass.` | 回复以 PowerContext 案卷开头，并引用第 1 轮的承诺 |
| 3 | `What did I promise you before midnight?` | 案卷同时含承诺与否认，NPC 再次引用承诺 |

主证据见 [`verification/e2e-seekdb/`](verification/e2e-seekdb/run.md)（交付 compose + 内嵌 seekdb）；
另一套真实 Dify 环境见 [`verification/real-dify/`](verification/real-dify/real-run.md)；
[`verification/e2e-mock/`](verification/e2e-mock/run.md) 只是无 Docker 时的 SQLite 快速自检。

## 技术结合

| 技术 | 作用 | 证据 |
|---|---|---|
| Dify 1.18 | 对话流编排：Code → HTTP(recall) → LLM → Code → HTTP(remember) → Answer | `dsl/inspector-recall.yml`、`verification/e2e-seekdb/node-trace.json`、`verification/real-dify/01-workflow-canvas.png` |
| PowerContext 1.2.0 | scope 隔离、持久记忆、受限召回 | `verification/powercontext-api-trace.log`、`verification/e2e-seekdb/seekdb-recall.json` |
| seekdb | PowerContext 的存储/检索后端（`DATABASE_KIND=seekdb`，内嵌引擎） | `verification/e2e-seekdb/powercontext-backend.txt`、`verification/e2e-seekdb/seekdb-storage.txt`、`deploy/compose.yaml` |
| mock LLM | 无 Key 时的 OpenAI 兼容模型，保证评测可复现 | `scripts/mock_llm.py`、`verification/e2e-seekdb/mock-llm.log` |

## 安全说明

- 仓库内所有配置均为占位值，不包含真实 API Key。
- `scope_id` 由可信 bridge 根据 `session_id` 生成，模型无法选择别人的 scope。
- PowerContext 召回内容作为「不可信历史证据」注入，不作为当前指令。
- 评测输入（题库、平台表单）未做任何修改。

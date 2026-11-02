# Dify 赛道 · Inspector Recall

> Dify × OceanBase 大赛作品（Just for Fun）
> **Dify** 编排对话流 · **PowerContext** 提供跨会话记忆 · 评审可按本说明完整复现。

## 作品

Inspector Recall 是一个「记得你说过的每一句谎话」的侦探 NPC：第一轮玩家立下承诺或编造身份，
下一轮推翻时，NPC 会从 PowerContext 取回上一轮的案卷，当场对质。

## 目录

```
dify/
  README.md                 本文件（提交入口）
  inspector-recall/
    inspector-recall.yml    Dify advanced-chat DSL（导入即用）
    bridge/server.py        Dify → PowerContext 的可信桥
    scripts/                mock PowerContext、离线验证脚本
    verification/           真实 Dify 运行截图、API trace、预期对话
    compose.yaml            可选：bridge 容器部署
  scripts/
    mock_llm.py             OpenAI 兼容 mock 模型（无 Key 也能跑通 DSL）
```

## 运行环境

| 组件 | 版本 |
|---|---|
| Dify | 1.18.0（docker compose 自部署） |
| PowerContext | 仓库 `2026-competition-ecosystem` 分支源码，HTTP API |
| Python | 3.11+ |
| 模型 | 任意 OpenAI 兼容模型；无 Key 时用 `scripts/mock_llm.py` |

## 快速开始（无模型 Key 也能验证）

```bash
# 1. 启动 mock 模型（OpenAI 兼容，模型名 local-qwen2.5-0.5b）
python3 dify/scripts/mock_llm.py          # http://0.0.0.0:8099/v1

# 2. 在 Dify 控制台添加模型供应商
#    Settings -> Model Provider -> OpenAI-API-compatible
#    Base URL: http://host.docker.internal:8099/v1
#    API Key:  dummy
#    Model:    local-qwen2.5-0.5b

# 3. 启动 PowerContext 与 bridge（见 inspector-recall/README.md）
# 4. 导入 DSL 并运行
#    inspector-recall/inspector-recall.yml -> Import DSL -> 发布 -> 对话
```

## 验证样例

1. 第一轮：告诉 NPC「我保证明天把账本还给你」。
2. 第二轮（新会话或继续对话）：说「我从来没借过账本」。
3. 预期：NPC 引用上一轮承诺并对质（截图见 `inspector-recall/verification/real-dify/`）。

## 技术结合

| 技术 | 作用 | 证据 |
|---|---|---|
| Dify | 对话流编排（Code → HTTP → LLM → Code → HTTP → Answer） | `inspector-recall.yml`、`verification/real-dify/` |
| PowerContext | 跨会话记忆写入与召回 | `verification/powercontext-api-trace.log`、`powercontext-runtime-run.json` |
| mock LLM | 无 Key 时的离线自检模型 | `scripts/mock_llm.py` |

## 说明

- 所有配置示例使用占位值，不包含真实 API Key。
- `bridge/` 隔离 PowerContext 凭据，Dify 提示词里不出现 scope_id 或 token。

# StepFun · 端侧迷你知识库

浏览器本地完成 embedding，seekdb 完成全文 + 向量混合检索的迷你知识库。

## 环境

| 组件 | 版本 |
|---|---|
| Node.js | 22.20.0 |
| seekdb | 服务端模式，端口 10000（本项目在 `/data/obtest/seekdb1` 上验证） |
| seekdb-js (`seekdb`) | 1.3.0（安装需 `--legacy-peer-deps`，见下） |
| Transformers.js | `@huggingface/transformers@3.8.1`（CDN 加载） |
| 模型 | `Xenova/all-MiniLM-L6-v2`（384 维，FP32 / INT8） |

## 启动

```bash
# 1. 数据库（已有 seekdb 部署时跳过）
source /data/tools/env.sh
cd "$DEPLOY_HOME" && export _OBD_HOME=$PWD
./obd.sh start -n single

# 2. 建库
mysql -h127.0.0.1 -P10000 -uroot -e "CREATE DATABASE IF NOT EXISTS stepfun"

# 3. 后端
cd stepfun/server
npm install --legacy-peer-deps --no-audit --no-fund
npm start            # http://127.0.0.1:8787
```

> `--legacy-peer-deps` 是必须的：`seekdb@1.3.0` 的 peer 依赖
> `@seekdb/default-embed` 在 npm 包里残留 `workspace:*`，npm 10 会直接报
> `EUNSUPPORTEDPROTOCOL`。本项目不用它的默认 embedding（向量全部在浏览器算），
> 跳过 peer 不影响功能。

浏览器打开 `http://<host>:8787`，按页面三步操作：加载模型 → 导入语料 → 搜索。

## 目录

```
stepfun/
  server/       Express + seekdb-js（建集合、写入、混合检索 SQL）
  web/          Transformers.js 端侧向量化 + UI
  README.md
  TECH_NOTE.md  一页技术说明（模型/量化/后端/seekdb/性能）
```

## 设计要点

- **向量只在浏览器产生**：`web/app.js` 用 Transformers.js 的
  `feature-extraction` pipeline 计算 embedding，服务端只接收向量。
- **混合检索在 seekdb 内完成**：`server/server.mjs` 的 `/api/search` 只发一条 SQL，
  同时使用 `COSINE_DISTANCE(embedding, ...)` 与 `MATCH(document) AGAINST (...)`，
  `ORDER BY 0.7*dist + 0.3*(1-ft)` 由数据库排序，客户端不做两路合并。
- **索引**：集合建表时声明 `FULLTEXT KEY ... WITH PARSER ik` 与
  `VECTOR KEY ... (DISTANCE=COSINE, TYPE=HNSW, LIB=VSAG)`。
- **WebGPU 优先、WASM 自动降级**：`buildPipeline()` 先尝试 `device: 'webgpu'`，
  失败则回退 `wasm`，页面顶部 badge 与运行指标里都会标明当前后端。

## 已知限制

- `seekdb-js` 的 `collection.hybridSearch()` 在当前版本（1.3.0）返回空数组，
  因此服务端用同一个 SDK 的 `client.execute()` 直接下发混合检索 SQL；
  集合创建与写入仍走 SDK 的 `createCollection` / `add`。
- 演示语料只有几十条，HNSW 与全文索引的收益需要更大数据量才能体现。
- 模型首次加载需要访问 Hugging Face（或配置镜像）；后续命中浏览器缓存。

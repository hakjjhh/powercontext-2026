# 技术说明（一页）

## 1. 模型与量化

| 项 | FP32 基线 | INT8 量化 |
|---|---|---|
| 模型 | `Xenova/all-MiniLM-L6-v2` | 同左 |
| ONNX 文件 | `onnx/model.onnx` | `onnx/model_quantized.onnx` |
| 实测文件大小 | 90,387,606 B ≈ 86.2 MiB | 22,972,370 B ≈ 21.9 MiB |
| 压缩比 | — | **3.93×** |
| 向量维度 | 384 | 384 |
| 来源 | Hugging Face `Xenova/all-MiniLM-L6-v2`（Apache-2.0） | 同左 |

量化方式为 ONNX Runtime 的 INT8 动态量化权重（`model_quantized.onnx`），
由 Transformers.js 按 `dtype: "q8"` 选择；FP32 与 INT8 共用同一 tokenizer。

检索质量对比（同一语料、同一查询集，Recall@5，以 FP32 全量精确最近邻为基准）：

> 待补：本次会话受电量限制未跑完浏览器端对比；页面与测量步骤已就绪，见第 5 节。

## 2. 端侧运行与后端选择

- 运行时：Transformers.js 3.8.1（ONNX Runtime Web），全部推理在浏览器内，
  不调用任何云端推理 API；服务端只接收 `float32[384]` 向量。
- 后端选择：`buildPipeline()` 优先 `device: "webgpu"`；
  `navigator.gpu` 不存在或初始化失败时回退 `device: "wasm"`。
  页面顶部 badge 实时显示 `WebGPU · fp32/q8` 或 `WASM · fp32/q8`。
- 批处理：导入时按 16 条一批向量化，降低单次推理内存峰值。

## 3. seekdb 使用

- 接入：`seekdb-js`（npm `seekdb@1.3.0`）server 模式，连接参数可配。
- 表结构（由 collection schema 生成，实测）：

```sql
CREATE TABLE c$v2$... (
  _id varbinary(512) NOT NULL,
  document string,
  embedding VECTOR(384),
  metadata json,
  PRIMARY KEY (_id),
  FULLTEXT KEY idx_fts (document) WITH PARSER ik PARSER_PROPERTIES=(ik_mode="smart"),
  VECTOR KEY idx_vec (embedding) WITH (DISTANCE=COSINE, TYPE=HNSW, LIB=VSAG)
);
```

- 混合检索：一条 SQL 内同时计算向量距离与全文得分，由 seekdb 排序：

```sql
SELECT _id, document, metadata,
       COSINE_DISTANCE(embedding, :vec) AS dist,
       MATCH(document) AGAINST (:query) AS ft
FROM `c$v2$...`
ORDER BY 0.7 * COSINE_DISTANCE(embedding, :vec)
       + 0.3 * (1 - LEAST(MATCH(document) AGAINST (:query), 1)) ASC
LIMIT :k;
```

- 增量写入：`collection.add()` 逐批追加；实测写入 2 条后同一进程内按
  `COSINE_DISTANCE` 排序即返回 `dist=0` / `dist=2`，无需额外刷新。
  （注：当前 seekdb-js 1.3.0 的 `add()` 会强制解析未安装的 default-embed，
  因此实现里改用同一 SDK 的 `client.execute()` 执行 INSERT；写入同样即时可检索。）

**实测（VM，3 条文档）**：`POST /api/docs` 写入 1 条成功；紧接着
`POST /api/search` 返回该文档，`distance=0`、`fulltextScore=0.544554`，
单条混合 SQL 耗时 **2.46 ms**。

## 4. 状态与优化

- 集合数据由 seekdb 持久化（`sdk_collections` + `c$v2$...`），重启后仍可检索；
  `POST /api/reset` 清空数据。
- 已实现优化：批量向量化（16/批）、向量在客户端计算（服务端零模型成本）、
  单条 SQL 完成混合排序（避免两路召回后在应用层合并）。
- 待实测：模型加载时间、向量化吞吐、seekdb 检索延迟、端到端延迟、
  WebGPU vs WASM、FP32 vs INT8 检索质量。

## 5. 复现测量步骤

1. 启动后端与页面，分别选择 FP32 / INT8 加载模型，记录顶部 badge 与运行指标。
2. 导入 200 条以上语料，记录 `embedMs`、`embedMsPerText`。
3. 用固定查询集各查一次，记录 `seekdbSearchMs` 与端到端延迟。
4. 关闭 WebGPU（Edge 加 `--disable-gpu`）重复步骤 1–3，验证 WASM 回退。
5. 以 FP32 的 top-5 为基准，统计 INT8 的 Recall@5。

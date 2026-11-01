# PaperLens 论文速查

基于 **seekdb**（OceanBase 向量数据库）的论文全文+向量混合检索系统。一条 SQL 同时使用 `MATCH ... AGAINST` 全文检索与 `COSINE_DISTANCE` 向量相似度，返回加权 relevance 分数。

## 前置条件

| 依赖 | 版本 | 说明 |
|------|------|------|
| Node.js | >= 20 | 运行时 |
| seekdb Server | 最新 | 监听 `127.0.0.1:10000`，root 无密码 |

启动 seekdb server（Docker）:

```bash
docker run -d --name seekdb -p 10000:10000 \
  oceanbase/seekdb:latest \
  -p 10000
```

或从官方二进制启动：参见 [seekdb 部署文档](https://www.oceanbase.ai/docs/deploy-overview/)。

## 快速开始

```bash
# 1. 安装依赖
npm install

# 2. 初始化数据库 schema（建库 + 建表 + 索引）
npm run init-db

# 3. 导入 sample 数据
npm run seed

# 4. 启动服务
npm start
```

打开浏览器访问 **http://localhost:3789** 即可使用。

## 项目结构

```
paperlens/
├── server.js                 # Express 主服务
├── config.js                 # 连接 & 权重配置
├── lib/
│   ├── db.js                 # seekdb 连接池、schema 初始化
│   ├── embedding.js          # 本地确定性向量生成（384维）
│   └── hybrid-search.js      # 混合检索核心 SQL
├── routes/
│   ├── documents.js          # /api/documents CRUD
│   └── search.js             # /api/search 搜索
├── public/
│   └── index.html            # 前端单页应用
├── data/
│   └── sample-papers.json    # 10 篇示例论文
├── scripts/
│   ├── init.js               # 初始化 schema
│   ├── seed.js               # 导入 sample 数据
│   └── smoke-test.js         # 端到端冒烟测试
└── package.json
```

## API 说明

### 搜索

```
GET /api/search?q=transformer attention&topK=10
```

响应:

```json
{
  "query": "transformer attention",
  "count": 3,
  "results": [
    {
      "id": "...",
      "title": "Attention Is All You Need",
      "content": "...",
      "authors": "Vaswani et al.",
      "year": 2017,
      "text_score": 2.45,
      "vec_distance": 0.12,
      "relevance": 1.526
    }
  ]
}
```

### 导入单篇

```
POST /api/documents
Content-Type: application/json

{
  "title": "Paper Title",
  "content": "Full text or abstract...",
  "authors": "Author Name",
  "year": 2024
}
```

### 批量导入

```
POST /api/documents/batch
{
  "documents": [ { "title": "...", "content": "..." }, ... ]
}
```

### 文档列表

```
GET /api/documents?page=1&size=20
```

## 混合检索原理

核心 SQL（见 `lib/hybrid-search.js`）：

```sql
SELECT
  id, title, content,
  MATCH(title, content) AGAINST(? IN NATURAL LANGUAGE MODE) AS text_score,
  COSINE_DISTANCE(embedding, ?) AS vec_distance,
  (MATCH(title, content) AGAINST(?) * 0.4
    + (1 - COSINE_DISTANCE(embedding, ?)) * 0.6
  ) AS relevance
FROM documents
WHERE
  MATCH(title, content) AGAINST(? IN NATURAL LANGUAGE MODE) > 0
  OR COSINE_DISTANCE(embedding, ?) < 0.95
ORDER BY relevance DESC
LIMIT 10
```

- **text_score**: MySQL/seekdb 全文索引自然语言模式得分，越大越相关
- **vec_distance**: 余弦距离 [0, 2]，越小越相似
- **relevance**: 加权综合得分 = 0.4×text + 0.6×(1-vec_distance)

## 向量生成

为保持项目自包含（无需外部 AI 模型 API），使用本地 hash-based 确定性向量生成器（`lib/embedding.js`，384 维单位向量）。

生产环境替换方案：

```bash
npm install @seekdb/default-embed
```

在 `lib/embedding.js` 中改为调用 `all-MiniLM-L6-v2` 模型，或使用 `@seekdb/qwen` 等云端 Embedding。

## 冒烟测试

```bash
npm run smoke-test
```

自动启动服务 → 导入测试文档 → 执行搜索 → 验证返回结果 → 退出。

## 配置

编辑 `config.js` 或设置环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PORT` | 3789 | Express 监听端口 |
| `SEEKDB_HOST` | 127.0.0.1 | seekdb 地址 |
| `SEEKDB_PORT` | 10000 | seekdb 端口 |
| `SEEKDB_USER` | root | 用户名 |
| `SEEKDB_PASSWORD` | (空) | 密码 |
| `SEEKDB_DATABASE` | paperlens | 数据库名 |

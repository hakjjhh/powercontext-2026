# Qoder 赛道 · PaperLens 论文速查

## 项目网站（Qoder Sites）

**https://paperlens-9olpfbewqyh.qoder.website**

- 可见范围：公开（未登录可访问，实测 HTTP 200）
- 站点内容：项目简介、解决的问题、核心功能、真实运行截图、混合检索 SQL、运行步骤、代码仓库链接

## 代码仓库

https://github.com/hakjjhh/powercontext-2026/tree/2026-competition-ecosystem/qoder/paperlens

## Qoder 账号

| 项 | 值 |
|---|---|
| 用户名 | hakjjhh |
| Qoder UID | `01a19512-30ee-78e8-8dc4-19d5a2456156` |
| 产品 | QoderWork（桌面端）+ Qoder CLI 1.1.81（Qoder Sites） |
| 模型 | Qwen3.8-Flash（免费模型） |

## 作品简介

PaperLens 把论文和笔记导入 seekdb，用**一条 SQL** 同时完成全文检索（`MATCH ... AGAINST`）
与向量检索（`COSINE_DISTANCE`），按加权 relevance 排序，返回结果同时给出三个可解释分数。

解决的问题：关键词检索漏掉语义相近的表述，语义检索又会漏掉精确术语；两路召回在应用层合并既不透明也不高效。

## Qoder 开发记录

- 开发方式：在 QoderWork 中把工作目录设为 `qoder/`，用自然语言描述需求，由 Qoder（Qwen3.8-Flash）完成
  需求拆解、编码、依赖安装、启动验证。
- 关键提示词：见 `evidence/qoderwork-task.png` 与本文末「提示词」。
- Qoder 产出：`paperlens/` 下 15 个文件（Express 服务、seekdb 连接与建表、混合检索、导入 API、
  前端页面、样例数据、seed 脚本、冒烟测试、README）。
- Qoder 自测结论（截图）：npm install 100 packages；模块加载正常；服务监听 :3789；
  前端 200；健康检查正确；向量 384 维 L2 归一化；混合 SQL 单条语句同时含
  `MATCH(title,content) AGAINST(?)` 与 `COSINE_DISTANCE(embedding,?)`。
- 站点发布：由 Qoder CLI 的 Sites 技能完成 `prepare_site → publish_site → 设为 public`，
  发布后 curl 验证首页 200、标题正确、静态资源 200。

## 运行说明

```bash
# 1. seekdb（任选其一）
docker run -d --name seekdb -p 10000:10000 oceanbase/seekdb:latest -p 10000
# 或使用已有 seekdb：127.0.0.1:10000，root，无密码

# 2. 启动应用
cd qoder/paperlens
npm install
npm run init-db     # 建库 paperlens + documents 表（全文索引 + 向量列）
npm run seed        # 导入 10 篇样例论文
npm start           # http://localhost:3789
npm run smoke-test  # 端到端冒烟测试
```

## 验证样例

1. 启动后打开 `http://localhost:3789`，应看到「PaperLens 论文速查」搜索界面。
2. 在搜索框输入 `vector similarity search`，应返回多条结果，每条显示
   `relevance` / `text` / `vec_dist` 三个分数。
3. 在「导入文档」标签页提交一篇新论文，刷新后应能被检索到。
4. `npm run smoke-test` 应输出 `4 通过 / 0 失败`。

实测记录见 `paperlens/evidence/smoke-test.log`：

```
[2/5] 测试文档导入... ✓
[3/5] 测试混合检索... ✓ 搜索返回 7 条结果，Top-1 relevance=0.4951
[4/5] 测试前端页面... ✓
[5/5] 测试文档列表 API... ✓
=== 结果: 4 通过 / 0 失败 ===
```

## 技术分工

| 技术 | 作用 | 证据 |
|---|---|---|
| Qoder | 主要开发工作（需求拆解、编码、依赖安装、启动验证）+ Qoder Sites 发布 | `evidence/qoderwork-task.png` |
| seekdb | 文档存储；`documents` 表含 FULLTEXT(title,abstract) 与 VECTOR(384)；单条 SQL 完成混合检索与排序 | `paperlens/lib/hybrid-search.js`、`paperlens/evidence/smoke-test.log` |

## 提示词（QoderWork）

> 在当前工作目录创建一个可运行的项目「PaperLens 论文速查」：1) Node.js 20 + Express 本地服务；
> 2) 用 seekdb（npm 包 seekdb@1.3.0，server 模式，连接 127.0.0.1:10000 root 无密码，库 paperlens）
> 存储文档，并实现全文 + 向量混合检索（一条 SQL 同时用 MATCH ... AGAINST 与 COSINE_DISTANCE）；
> 3) 前端支持文档导入、自然语言搜索、结果显示 relevance 分数；4) 附 README、sample data、冒烟测试脚本；
> 5) 代码要能 npm install && npm start 直接跑通。请先给简短计划，然后直接实现，并在本机验证服务能启动、
> 检索能返回结果。

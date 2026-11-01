# 一页技术说明

## 1. 模块结构

`sdd_context` 是一个可被 Agent 或 CI 直接调用的 CLI：

| 命令 | 作用 |
|---|---|
| `index` | 扫描 `spec/`、`src/`、`tests/`，写入 seekdb `sdd_artifacts` 表 |
| `context` | 以任务描述做 BM25 全文检索，返回 `specs / code / tests` 三类来源 |
| `state-save` | 把任务、状态、选中的来源写入 PowerContext memory |
| `state-load` | 新进程按任务检索并读回已保存状态 |
| `generate` | 把上下文交给 MiniMax，要求返回 unified diff |
| `verify` | 运行样例工程的 pytest |

## 2. 上下文选择方式

- 表结构：`sdd_artifacts(kind, path, content, FULLTEXT(path, content) WITH PARSER ik)`。
- 检索：`MATCH(path, content) AGAINST (task)`，按 BM25 分数降序，缺失时回退 `LIKE`。
- 本次任务选出的来源（见 `evidence/seekdb-context.json`）：
  `spec/bookmarks.md`、`src/bookmarks.py`、`tests/test_bookmarks.py`，分数分别为
  1.84 / 0.14 / 0.07。
- 规格更新后重新 `index` 即覆盖同一 `path` 的行（`ON DUPLICATE KEY UPDATE`），上下文随之更新，不会返回固定答案。

## 3. 各项技术的分工

| 技术 | 分工 | 记录 |
|---|---|---|
| **MiniMax（M3）** | 读取任务 + 选中的规格/代码/测试，生成 tag 过滤实现 | `evidence/patch.raw.txt` 是原始回答，`evidence/patch.diff` 是提取出的 diff |
| **seekdb** | 工程资料存储与检索，查询结果直接构成上下文 | `evidence/seekdb-context.json` |
| **PowerContext** | 保存 `task_state`（任务、状态、选中来源），新进程读回 | `evidence/powercontext-save.json`、`evidence/powercontext-load.json` |

## 4. 代码修改与验证

- 目标测试：
  - `test_filter_by_tag_is_case_insensitive`
  - `test_unknown_tag_returns_empty_list`
- 回归测试：`test_add_and_list_newest_first`、`test_limit_and_invalid_limit`、`test_delete_bookmark`。
- 改前：`2 failed, 3 passed`（`evidence/before-tests.log`）。
- MiniMax 生成的实现（节选）：

```python
if tag:
    normalized_tag = str(tag).strip().casefold()
    selected = [
        item
        for item in selected
        if any(
            str(item_tag).casefold() == normalized_tag
            for item_tag in item["tags"]
        )
    ]
```

- 改后：`5 passed`（`evidence/after-tests.log`）。
- 说明：M3 返回的 unified diff 内容正确但 hunk 计数写错，`diffutil.recount_hunks()` 会重算计数；
  本次仍用 `scripts/apply_model_block.py` 按同一代码块落地，`evidence/patch.diff` 保留模型原始输出。

## 5. 已知限制

- 样例工程是内存态演示，生产使用时应把 `_ITEMS` 换成真实存储。
- 检索目前是纯全文（IK 分词）；如需语义召回，可在同一张表上增加向量列与 HNSW 索引。
- MiniMax M3 会在 `content` 中带 `<think>` 推理段，客户端已做剥离并单独保存原始回答。

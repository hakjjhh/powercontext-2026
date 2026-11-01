# 规有所依 · SDD 上下文管理系统（MiniMax 赛道）

给 AI 开发助手用的工程上下文模块：根据开发任务，从 **seekdb** 里检索出适用的规格、代码和测试，
交给 **MiniMax** 生成补丁，并把任务状态写进 **PowerContext**，下次运行可读回复用。

## 目录

```
minimax/
  sdd_context/        上下文模块（CLI）
    store.py          seekdb 建表 / 索引 / 全文检索
    powercontext.py   PowerContext HTTP 客户端（scope + memory）
    minimax_client.py MiniMax Chat Completions 客户端
    diffutil.py       修正 LLM 输出 diff 的 hunk 计数
    cli.py            index / context / state-save / state-load / generate / verify
  sample_project/     验证用的业务工程（spec / src / tests）
  scripts/
    apply_model_block.py  把模型给出的代码块落到锚点
  evidence/           本次验证的全部证据
  requirements.txt
```

## 环境

| 组件 | 版本 / 说明 |
|---|---|
| Python | 3.10+ |
| seekdb | MySQL 协议，默认 `127.0.0.1:10000`，库 `minimax_sdd` |
| PowerContext | `powercontext server run`，默认 `http://127.0.0.1:8000` |
| MiniMax | OpenAI 兼容 `https://api.minimaxi.com/v1`，模型 `MiniMax-M3` |

```bash
python3 -m pip install -r requirements.txt
export MINIMAX_API_KEY=...            # 不要提交到仓库
export MINIMAX_MODEL=MiniMax-M3
export POWERCONTEXT_URL=http://127.0.0.1:8000
export SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=10000 SEEKDB_USER=root SEEKDB_DATABASE=minimax_sdd
```

## 复现步骤

```bash
# 0. 启动 seekdb 与 PowerContext（见仓库文档）

# 1. 建立工程资料索引（spec / src / tests）
python3 -m sdd_context.cli index --root sample_project

# 2. 按任务生成上下文（seekdb 全文检索，BM25 排序）
python3 -m sdd_context.cli context \
  --task "为 list_bookmarks 增加按 tag 过滤（不区分大小写），未知标签返回空列表，保持原有返回字段与排序"

# 3. 任务状态写入 PowerContext，并在新进程里读回
python3 -m sdd_context.cli state-save --task "list_bookmarks tag 过滤 task state" --status in_progress
python3 -m sdd_context.cli state-load --task "tag filter list_bookmarks"

# 4. 改前测试（应为 2 失败 / 3 通过）
cd sample_project && python3 -m pytest -q; cd ..

# 5. 让 MiniMax 依据上下文生成补丁
python3 -m sdd_context.cli generate \
  --task "为 list_bookmarks 增加按 tag 过滤（不区分大小写），未知标签返回空列表，保持原有返回字段与排序" \
  --out evidence/patch.diff

# 6. 应用并复测（应为 5/5 通过）
python3 scripts/apply_model_block.py
cd sample_project && python3 -m pytest -q
```

## 各技术承担的作用

| 技术 | 在本作品中的作用 | 证据 |
|---|---|---|
| MiniMax | 依据上下文生成 `list_bookmarks` 的 tag 过滤实现 | `evidence/patch.raw.txt`、`evidence/patch.diff` |
| seekdb | 建立 `sdd_artifacts` 表（FULLTEXT ik），按任务检索规格/代码/测试 | `evidence/seekdb-context.json` |
| PowerContext | `task_state` 记忆写入，新进程读回 | `evidence/powercontext-save.json`、`evidence/powercontext-load.json` |

## 验证样例

- 任务：为 `list_bookmarks` 增加按 tag 过滤（不区分大小写），未知标签返回空列表，保持原有返回字段与排序。
- 初始状态：`src/bookmarks.py` 的 `list_bookmarks` 忽略 `tag`。
- 预期：`test_filter_by_tag_is_case_insensitive`、`test_unknown_tag_returns_empty_list` 由失败转为通过，
  其余 3 个回归测试始终保持通过。
- 结果：改前 `2 failed, 3 passed`；应用 MiniMax 生成的实现后 `5 passed`。

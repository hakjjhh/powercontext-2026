# Bookmarks 服务规格

## 数据结构

每条书签包含：`id`（自增整数）、`url`、`title`、`tags`（字符串列表）、`created_at`（ISO 时间）。

## 接口

### `add_bookmark(url, title, tags=None) -> int`

- `url` 必填且不能为空串。
- `tags` 去重、去首尾空格，保留原大小写。
- 返回新书签的 `id`。

### `list_bookmarks(tag=None, limit=10) -> list[dict]`

- 返回按 `created_at` 倒序（最新在前）的书签列表。
- `tag` 为空时返回全部；**非空时只返回 tags 中包含该标签的书签，匹配不区分大小写**。
- `limit` 必须是正整数，默认 10；超过时按 `limit` 截断。
- 每条返回 `{id, url, title, tags}`，不含 `created_at`。

### `delete_bookmark(bookmark_id) -> bool`

- 删除成功返回 `True`，不存在返回 `False`。

## 兼容性约束

- 不得改变已有函数签名和返回字段。
- `tag=None` 的行为必须与旧版本完全一致。
- 空列表、未知标签返回空列表，不抛异常。

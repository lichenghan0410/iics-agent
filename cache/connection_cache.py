"""
cache/connection_cache.py — ConnectionCache：内存 dict，session 级别

v0.1 Read-only MVP 占位文件。
Implementation starts in later steps (Step 7)。

约定（实现于 Step 7）:
  - get(connection_id) -> str | None
  - set(connection_id, name) -> None
  - get_or_fetch(connection_id, fetch_fn) -> str
  - 进程退出即清空

本文件当前不实现任何逻辑。
"""

# TODO(Step 7): class ConnectionCache (get / set / get_or_fetch)

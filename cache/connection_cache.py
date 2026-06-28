"""
cache/connection_cache.py — ConnectionCache：内存 dict，session 级别

v0.1 Read-only MVP。Step 7 implements ConnectionCache (get / set / get_or_fetch)。
进程退出即清空。
"""

from collections.abc import Callable


class ConnectionCache:
    """内存缓存，session 级别，进程退出即清空。"""

    def __init__(self):
        self._cache: dict[str, str] = {}

    def get(self, connection_id: str) -> str | None:
        """返回连接名称，未命中返回 None。"""
        if not connection_id:
            return None
        return self._cache.get(connection_id)

    def set(self, connection_id: str, name: str) -> None:
        """写入缓存。"""
        if not connection_id:
            return
        self._cache[connection_id] = name or ""

    def get_or_fetch(
        self,
        connection_id: str,
        fetch_fn: Callable[[str], str],
    ) -> str:
        """命中缓存则直接返回；未命中则调用 fetch_fn 并写入缓存。"""
        cached = self.get(connection_id)
        if cached is not None:
            return cached
        name = fetch_fn(connection_id)
        self.set(connection_id, name)
        return name

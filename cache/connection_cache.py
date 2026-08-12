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


_MISSING = object()


class ObjectCache:
    """通用 id→值 内存缓存（值可为 dict/str 等），session 级别，进程退出即清空。

    用于 Mapping / Runtime Environment 等 by-id enrichment 结果缓存；
    同一 id 一次 export 进程内只会真实 fetch 一次。
    """

    def __init__(self):
        self._cache: dict = {}

    def get(self, key: str):
        if not key:
            return None
        return self._cache.get(key, None)

    def set(self, key: str, value) -> None:
        if not key:
            return
        self._cache[key] = value

    def get_or_fetch(self, key: str, fetch_fn: Callable[[str], object]):
        """命中缓存直接返回（含缓存的 None/空值）；未命中调用 fetch_fn 并写入。"""
        if not key:
            return fetch_fn(key)
        cached = self._cache.get(key, _MISSING)
        if cached is not _MISSING:
            return cached
        value = fetch_fn(key)
        self._cache[key] = value
        return value

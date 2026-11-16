"""
cache/connection_cache.py — ConnectionCache：内存 dict，session 级别

v0.1 Read-only MVP。Step 7 implements ConnectionCache (get / set / get_or_fetch)。
进程退出即清空。
"""

from collections.abc import Callable


class ConnectionCache:
    """内存缓存，session 级别，进程退出即清空。

    除展示用的名称字符串外，另记录「该值是否来自响应的 name / connectionName」
    （has_name）。has_name=False 表示 resolver 用 id 或 "" 兜底。旧接口
    get / set / get_or_fetch 的返回值与行为不变。
    """

    def __init__(self):
        self._cache: dict[str, str] = {}
        self._has_name: dict[str, bool] = {}

    def get(self, connection_id: str) -> str | None:
        """返回连接名称，未命中返回 None。"""
        if not connection_id:
            return None
        return self._cache.get(connection_id)

    def set(self, connection_id: str, name: str, has_name: bool | None = None) -> None:
        """写入缓存。has_name 省略（旧写法）时不记录来源，并清除旧的来源信息。"""
        if not connection_id:
            return
        self._cache[connection_id] = name or ""
        if has_name is None:
            self._has_name.pop(connection_id, None)
        else:
            self._has_name[connection_id] = bool(has_name)

    def get_entry(self, connection_id: str) -> tuple[str, bool] | None:
        """返回 (name, has_name)；未命中或缺少来源信息时返回 None。"""
        if not connection_id or connection_id not in self._has_name:
            return None
        if connection_id not in self._cache:
            return None
        return self._cache[connection_id], self._has_name[connection_id]

    def get_or_fetch_entry(
        self,
        connection_id: str,
        fetch_fn: Callable[[str], tuple[str, bool]],
    ) -> tuple[str, bool]:
        """命中（含来源信息）则直接返回；否则调用 fetch_fn → (name, has_name) 并写入。

        仅当某 id 曾经由旧 set() 写入（无来源信息）时才会补一次 fetch；
        项目内所有 connection 缓存写入都经 resolver 的此接口，正常不会发生。
        """
        entry = self.get_entry(connection_id)
        if entry is not None:
            return entry
        name, has_name = fetch_fn(connection_id)
        self.set(connection_id, name, has_name=bool(has_name))
        return (name or ""), bool(has_name)

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

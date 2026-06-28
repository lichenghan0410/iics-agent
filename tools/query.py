"""
tools/query.py — 只读查询工具：list_tasks / get_task_detail / resolve_connection

v0.1 Read-only MVP。
Step 5 implements normalize_task_type / list_tasks（V3 枚举 + 自动分页）。
Step 6 implements get_task_detail + extract_runtime_agent / extract_connection_ids
（V3 同名消歧 + V2 detail 路由 + best-effort 抽取；不调用 Step 7 的 resolve_connection）。
resolve_connection（Step 7）尚未实现。

约定:
  - 每个工具函数接收 client: IICSClient 作为第一个参数
  - 返回 JSON 可序列化 dict，错误封装为 {"error": str(e), "error_type": type(e).__name__}
  - 不向上层抛异常
"""

# ── task type 归一化 ─────────────────────────────────────────────────────────
_TASK_TYPE_MAP = {
    "DSS": "DSS",
    "SYNCTASK": "DSS",
    "SYNC": "DSS",
    "MTT": "MTT",
    "MTTASK": "MTT",
    "MAPPINGTASK": "MTT",
    "WORKFLOW": "WORKFLOW",
    "LINEARTASKFLOW": "WORKFLOW",
    "TASKFLOW": "WORKFLOW",
}

_VALID_TASK_TYPES = {"DSS", "MTT", "WORKFLOW", "ALL"}


def normalize_task_type(raw_type: str) -> str:
    """归一化 IICS 返回的 type 字段到工具枚举（DSS/MTT/WORKFLOW）。

    - 输入为空返回 ""
    - 忽略大小写、空格、连字符匹配
    - 未识别返回原始 type 的大写形式
    """
    if not raw_type:
        return ""
    key = raw_type.upper().replace(" ", "").replace("-", "").replace("_", "")
    return _TASK_TYPE_MAP.get(key, raw_type.upper())


# ── list_tasks ───────────────────────────────────────────────────────────────
def list_tasks(
    client,
    folder_path: str,
    task_type: str = "ALL",
    page_size: int = 200,
) -> dict:
    """枚举文件夹下的 task（V3 api/v3/objects + nextPageToken 自动分页）。

    返回: {"folder": ..., "count": N, "tasks": [{name,type,id,status}, ...]}
    错误: {"error": str(e), "error_type": type(e).__name__}
    """
    # ── 参数校验 ──────────────────────────────────────────────────────────
    if not folder_path:
        return {"error": "folder_path is required", "error_type": "ValueError"}
    if task_type not in _VALID_TASK_TYPES:
        return {
            "error": f"invalid task_type: {task_type}",
            "error_type": "ValueError",
        }
    if page_size <= 0:
        return {
            "error": "page_size must be positive",
            "error_type": "ValueError",
        }

    try:
        q = f'location=="{folder_path}"'
        if task_type != "ALL":
            q += f' AND type=="{task_type}"'

        tasks = []
        next_page_token = None

        while True:
            params = {"q": q, "pageSize": page_size}
            if next_page_token:
                params["pageToken"] = next_page_token

            resp = client._request(
                "GET", "api/v3/objects", use_v3=True, params=params
            )
            data = resp.json()

            items = data.get("objects")
            if items is None:
                items = data.get("items", [])

            for item in items:
                tasks.append(
                    {
                        "name": item.get("name", ""),
                        "type": normalize_task_type(item.get("type", "")),
                        "id": item.get("id", ""),
                        "status": item.get("status", ""),
                    }
                )

            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break

        return {"folder": folder_path, "count": len(tasks), "tasks": tasks}
    except Exception as e:  # noqa: BLE001 — 工具层统一结构化错误，不向上抛
        return {"error": str(e), "error_type": type(e).__name__}


# ── best-effort 抽取辅助 ─────────────────────────────────────────────────────
_RUNTIME_AGENT_FIELDS = (
    "runtimeEnvironmentName",
    "runtimeEnvironment",
    "runtimeEnvName",
    "agentName",
    "runtime_agent",
)

# V2 detail 路由
_V2_DETAIL_PATH = {
    "DSS": "dsstask/name/",
    "MTT": "mttask/name/",
    "WORKFLOW": "workflow/name/",
}

_DETAIL_TASK_TYPES = {"DSS", "MTT", "WORKFLOW"}


def extract_runtime_agent(raw: dict) -> str:
    """best-effort 抽取 runtime agent 名称，未找到返回 ""，不抛异常。"""
    if not isinstance(raw, dict):
        return ""
    for field in _RUNTIME_AGENT_FIELDS:
        if field not in raw:
            continue
        value = raw[field]
        if isinstance(value, dict):
            name = value.get("name") or value.get("id")
            if name:
                return name
        elif value:
            return value
    return ""


def _get_nested(raw: dict, dotted: str):
    """按点分路径取值，缺失返回 None。"""
    cur = raw
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def extract_connection_ids(raw: dict, task_type: str) -> dict:
    """best-effort 抽取 connection id，缺失不报错。"""
    result = {
        "source_connection_id": "",
        "target_connection_id": "",
        "connection_ids": [],
        "unresolved_connection_fields": [],
    }
    if not isinstance(raw, dict):
        return result

    ordered = []  # 保序去重收集

    def add(cid):
        if isinstance(cid, str) and cid and cid not in ordered:
            ordered.append(cid)

    if task_type == "DSS":
        source_candidates = (
            "sourceConnectionId",
            "source.connectionId",
            "sourceConnection.id",
        )
        target_candidates = (
            "targetConnectionId",
            "target.connectionId",
            "targetConnection.id",
        )
        for path in source_candidates:
            val = _get_nested(raw, path)
            if isinstance(val, str) and val:
                result["source_connection_id"] = val
                break
        for path in target_candidates:
            val = _get_nested(raw, path)
            if isinstance(val, str) and val:
                result["target_connection_id"] = val
                break
        add(result["source_connection_id"])
        add(result["target_connection_id"])

    else:
        # MTT / WORKFLOW：递归扫描 key 归一化后含 connectionid 的字段
        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    norm = k.lower().replace("_", "").replace("-", "")
                    if "connectionid" in norm and isinstance(v, str):
                        add(v)
                    else:
                        walk(v)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(raw)

    result["connection_ids"] = ordered
    return result


def get_task_detail(
    client,
    task_name: str,
    task_type: str,
    folder_path: str = "",
    resolve_connections: bool = True,
) -> dict:
    """获取单个 task 详情（V3 同名消歧 + V2 detail 路由 + best-effort 抽取）。

    注意：Step 6 不调用尚未实现的 resolve_connection()，connection name 留空，
    Step 7 再接入真实解析。
    """
    if not task_name:
        return {"error": "task_name is required", "error_type": "ValueError"}
    if task_type not in _DETAIL_TASK_TYPES:
        return {
            "error": f"invalid task_type: {task_type}",
            "error_type": "ValueError",
        }

    try:
        # ── V3 同名消歧（folder_path 非空时）──────────────────────────────
        if folder_path:
            resp = client._request(
                "GET",
                "api/v3/objects",
                use_v3=True,
                params={
                    "q": f'name=="{task_name}" AND location=="{folder_path}"'
                },
            )
            data = resp.json()
            candidates = data.get("objects")
            if candidates is None:
                candidates = data.get("items", [])
            if len(candidates) > 1:
                return {
                    "error": "同名 task 歧义",
                    "error_type": "AmbiguousTaskError",
                    "candidates": candidates,
                }
            # 0 或 1 个结果均继续走 V2 name lookup（best-effort）

        # ── V2 task detail 路由 ──────────────────────────────────────────
        path = _V2_DETAIL_PATH[task_type] + task_name
        resp = client._request("GET", path, use_v3=False)
        raw = resp.json()

        runtime_agent = extract_runtime_agent(raw)
        conn = extract_connection_ids(raw, task_type)

        detail = {
            "name": task_name,
            "type": task_type,
            "folder": folder_path,
            "runtime_agent": runtime_agent,
            "source_connection": {
                "id": conn["source_connection_id"],
                "name": "",
            },
            "target_connection": {
                "id": conn["target_connection_id"],
                "name": "",
            },
            "connection_ids": conn["connection_ids"],
            "unresolved_connection_fields": conn["unresolved_connection_fields"],
            "raw": raw,
        }
        return detail
    except Exception as e:  # noqa: BLE001 — 工具层统一结构化错误
        return {"error": str(e), "error_type": type(e).__name__}


# TODO(Step 7): def resolve_connection(client, connection_ids: list[str]) -> dict

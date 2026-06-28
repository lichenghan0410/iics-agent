"""
tools/query.py — 只读查询工具：list_tasks / get_task_detail / resolve_connection

v0.1 Read-only MVP。
Step 5 implements normalize_task_type / list_tasks（V3 枚举 + 自动分页）。
get_task_detail（Step 6）、resolve_connection（Step 7）尚未实现。

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


# TODO(Step 6): def get_task_detail(client, task_name, task_type, folder_path="", resolve_connections=True) -> dict
# TODO(Step 7): def resolve_connection(client, connection_ids: list[str]) -> dict

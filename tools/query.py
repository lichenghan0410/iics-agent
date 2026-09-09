"""
tools/query.py — 只读查询工具：list_tasks / get_task_detail / resolve_connection

v0.1 Read-only MVP。
Step 5 implements normalize_task_type / list_tasks（V3 枚举 + 自动分页）。
Step 6 implements get_task_detail + extract_runtime_env_name / extract_connection_ids。
MTT 详情走官方 FRS 路径 mttask/frs/{frs_id}（V3 folder 枚举取 FRS object ID →
跨 folder 同名消歧）；DSS / WORKFLOW 仍走 V2 by-name（Amendment 9：folder 用于 FRS
discovery 取 frs_id，但 by-name detail 无法按 folder 消歧）。三类型统一暴露 id + frs_id。
Step 7 implements resolve_connection（V2 connection/<id> + ConnectionCache），并把
get_task_detail(resolve_connections=True) 接入真实 connection name 解析。

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

# 实测确认的 V3 objects 枚举端点（位于 /saas 下，由 build_v3_url 补全前缀）
_V3_OBJECTS_PATH = "public/core/v3/objects"


def _object_name(item: dict) -> str:
    """V3 objects 响应无 name 字段，名字为 path 末段。"""
    name = item.get("name", "")
    if name:
        return name
    path = item.get("path", "")
    return path.split("/")[-1] if path else ""


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
    """枚举文件夹下的 task（V3 public/core/v3/objects + skip 偏移自动分页）。

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
        # 实测：V3 q 仅支持 location/type；逻辑连接符须小写 and（大写 AND 返回 400）。
        # ALL 时按三种任务类型分别查询并合并，避免混入 mapping 等非任务对象。
        types_to_fetch = (
            ["DSS", "MTT", "WORKFLOW"] if task_type == "ALL" else [task_type]
        )
        tasks = []
        for t in types_to_fetch:
            q = f'location=="{folder_path}" and type=="{t}"'
            skip = 0
            # 实测：无 nextPageToken；pageSize 上限 200；分页用 skip 偏移。
            while True:
                params = {"q": q, "pageSize": page_size, "skip": skip}
                resp = client._request(
                    "GET", _V3_OBJECTS_PATH, use_v3=True, params=params
                )
                data = resp.json()

                items = data.get("objects")
                if items is None:
                    items = data.get("items", [])

                for item in items:
                    tasks.append(
                        {
                            "name": _object_name(item),
                            "type": normalize_task_type(item.get("type", "")),
                            # Amendment 9：V3 /objects 返回的 id 即 FRS object ID，
                            # 统一命名 frs_id（不再用有歧义的 id 表示 FRS ID）。
                            "frs_id": item.get("id", ""),
                            "status": item.get("status", ""),
                            "path": item.get("path", ""),
                        }
                    )

                total = data.get("count")
                skip += len(items)
                if not items:
                    break
                if total is not None and skip >= total:
                    break

        return {"folder": folder_path, "count": len(tasks), "tasks": tasks}
    except Exception as e:  # noqa: BLE001 — 工具层统一结构化错误，不向上抛
        return {"error": str(e), "error_type": type(e).__name__}


# ── best-effort 抽取辅助 ─────────────────────────────────────────────────────
# DSS/WORKFLOW 无可靠 runtime id，best-effort 从 raw 取一个 runtime 名字候选字段。
_RUNTIME_ENV_NAME_FIELDS = (
    "runtimeEnvironmentName",
    "runtimeEnvironment",
    "runtimeEnvName",
)

# V2 detail 路由（DSS / WORKFLOW：保持 by-name 现状；MTT 走 FRS，见 _V2_MTT_FRS）
_V2_DETAIL_PATH = {
    "DSS": "dsstask/name/",
    "WORKFLOW": "workflow/name/",
}

# MTT 详情官方文档明确支持的路径：只接受 FRS object ID
_V2_MTT_FRS = "mttask/frs/"

_DETAIL_TASK_TYPES = {"DSS", "MTT", "WORKFLOW"}


def extract_runtime_env_name(raw: dict) -> str:
    """best-effort 抽取 runtime environment 名称，未找到返回 ""，不抛异常。"""
    if not isinstance(raw, dict):
        return ""
    for field in _RUNTIME_ENV_NAME_FIELDS:
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


def _apply_extraction(client, detail, raw, task_type, resolve_connections):
    """DSS / WORKFLOW：runtime_environment + connection 抽取与解析。

    v0.1.x：公开字段 runtime_environment（{id,name}）。DSS/WORKFLOW 无可靠 runtime id，
    故 id=None、name 为既有 best-effort 解析值（或 None）；不新增 API 调用。
    """
    rt_name = extract_runtime_env_name(raw)
    detail["runtime_environment"] = {"id": None, "name": rt_name or None}
    conn = extract_connection_ids(raw, task_type)
    detail["source_connection"] = {"id": conn["source_connection_id"], "name": ""}
    detail["target_connection"] = {"id": conn["target_connection_id"], "name": ""}
    detail["connection_ids"] = conn["connection_ids"]
    detail["unresolved_connection_fields"] = conn["unresolved_connection_fields"]
    detail["raw"] = raw

    if resolve_connections and detail["connection_ids"]:
        res = resolve_connection(client, detail["connection_ids"])
        resolved_map = res.get("resolved", {})
        detail["resolved_connections"] = resolved_map
        if res.get("failed"):
            detail["connection_resolve_failed"] = res["failed"]
        for key in ("source_connection", "target_connection"):
            cid = detail[key]["id"]
            if cid in resolved_map:
                detail[key]["name"] = resolved_map[cid]
    return detail


# ── MTT enrichment 辅助 ──────────────────────────────────────────────────────
def _transformation_name(param: dict) -> str:
    """transformation 名（Amendment 4）：优先 parameter.name（去首尾 $），
    仅在 name 缺失/空时 fallback parameter.label。

    实测：label 可能只是 connection parameter 的 display label（如
    "DBConnection_awsoracle"），不能作为第一优先级。
    """
    raw_name = param.get("name")
    if isinstance(raw_name, str) and raw_name.strip():
        return raw_name.strip().strip("$")
    label = param.get("label")
    if isinstance(label, str):
        return label.strip()
    return ""


def _extract_source_object(param: dict):
    """返回 (source_object, custom_query)。

    customQuery 非空 → custom_query=该值、source_object=""（隐藏 dummy sourceObject）。
    否则优先级：extendedObject.object.name → extendedObject.objects[].name（保序去重，
    多值返回 list）→ sourceObject → objectName。
    """
    cq = param.get("customQuery")
    if isinstance(cq, str) and cq.strip():
        return "", cq

    eo = param.get("extendedObject")
    if isinstance(eo, dict):
        obj = eo.get("object")
        if isinstance(obj, dict) and obj.get("name"):
            return obj["name"], None
        objs = eo.get("objects")
        if isinstance(objs, list) and objs:
            names = []
            for o in objs:
                if isinstance(o, dict):
                    nm = o.get("name")
                    if nm and nm not in names:
                        names.append(nm)
            if names:
                return (names if len(names) > 1 else names[0]), None

    for f in ("sourceObject", "objectName"):
        v = param.get(f)
        if isinstance(v, str) and v:
            return v, None
    return "", None


def _extract_target_object(param: dict) -> str:
    """target object：targetObjectLabel → targetObject → objectLabel → objectName。"""
    for f in ("targetObjectLabel", "targetObject", "objectLabel", "objectName"):
        v = param.get(f)
        if isinstance(v, str) and v:
            return v
    return ""


def _extract_mtt_transformations(raw: dict) -> list:
    """从 MTT raw.parameters[] 严格按顺序展开 Source/Target transformation。

    以存在 sourceConnectionId / targetConnectionId 为可展开依据；不去重、不取首尾。
    connection name 由调用方后续解析回填。
    """
    out = []
    params = raw.get("parameters") if isinstance(raw, dict) else None
    if not isinstance(params, list):
        return out
    for p in params:
        if not isinstance(p, dict):
            continue
        if p.get("sourceConnectionId"):
            src_obj, cq = _extract_source_object(p)
            out.append({
                "name": _transformation_name(p),
                "kind": "source",
                "source_connection": {"id": p["sourceConnectionId"], "name": ""},
                "source_object": src_obj,
                "custom_query": cq,
                "target_connection": None,
                "target_object": None,
                "operation": None,
            })
        elif p.get("targetConnectionId"):
            out.append({
                "name": _transformation_name(p),
                "kind": "target",
                "source_connection": None,
                "source_object": None,
                "custom_query": None,
                "target_connection": {"id": p["targetConnectionId"], "name": ""},
                "target_object": _extract_target_object(p),
                "operation": p.get("operationType"),
            })
    return out


def _resolve_runtime_env(client, rt_id, warnings):
    """runtimeEnvironmentId → runtimeEnvironment/{id} → name（带缓存）。失败记 warning。"""
    if not rt_id:
        return {"id": None, "name": None}

    def fetch(k):
        resp = client._request("GET", f"runtimeEnvironment/{k}", use_v3=False)
        d = resp.json()
        return d.get("name")

    try:
        name = client.runtime_cache.get_or_fetch(rt_id, fetch)
    except Exception as e:  # noqa: BLE001 — enrichment 失败降级为 warning
        warnings.append({"kind": "runtime_environment", "id": rt_id,
                         "error": str(e), "error_type": type(e).__name__})
        name = None
    return {"id": rt_id, "name": name}


def _resolve_mapping(client, mapping_id, warnings):
    """mappingId → mapping/{id} → {id,name,location(None),updated_on}（带缓存）。失败记 warning。

    Mapping API 无可靠 location/folder，故 location 恒 None，不等于 Task Location。
    """
    if not mapping_id:
        return {"id": None, "name": None, "location": None, "updated_on": None}

    def fetch(k):
        resp = client._request("GET", f"mapping/{k}", use_v3=False)
        d = resp.json()
        return {"name": d.get("name"), "updated_on": d.get("updateTime")}

    try:
        m = client.mapping_cache.get_or_fetch(mapping_id, fetch)
    except Exception as e:  # noqa: BLE001
        warnings.append({"kind": "mapping", "id": mapping_id,
                         "error": str(e), "error_type": type(e).__name__})
        return {"id": mapping_id, "name": None, "location": None, "updated_on": None}
    return {"id": mapping_id, "name": m.get("name"), "location": None,
            "updated_on": m.get("updated_on")}


def _build_mtt_detail(client, raw, frs_id, task_name, folder_path,
                      resolve_connections):
    """把 MTT FRS raw 规范化为增强 detail（不直接 merge raw，显式映射字段）。"""
    warnings = []

    rt = _resolve_runtime_env(client, raw.get("runtimeEnvironmentId"), warnings)
    mapping = _resolve_mapping(client, raw.get("mappingId"), warnings)
    transformations = _extract_mtt_transformations(raw)

    # 解析每个 transformation 的 source/target connection 名（带缓存 + warning 分级）
    if resolve_connections and transformations:
        conn_ids = []
        for t in transformations:
            for key in ("source_connection", "target_connection"):
                c = t.get(key)
                if c and c.get("id") and c["id"] not in conn_ids:
                    conn_ids.append(c["id"])
        if conn_ids:
            res = resolve_connection(client, conn_ids)
            resolved = res.get("resolved", {})
            for t in transformations:
                for key in ("source_connection", "target_connection"):
                    c = t.get(key)
                    if c and c.get("id") in resolved:
                        c["name"] = resolved[c["id"]]
            for fid in res.get("failed", []):
                warnings.append({"kind": "connection", "id": fid,
                                 "error": "connection resolve failed",
                                 "error_type": "EnrichmentError"})

    return {
        "@type": raw.get("@type", ""),
        "id": raw.get("id", ""),               # classic V2 id（来自 FRS payload）
        "orgId": raw.get("orgId", ""),
        "frs_id": frs_id,          # V3/FRS id（用于 FRS 请求）
        "detail_route": "v2-mttask-frs",
        "task_name": task_name or raw.get("name", ""),
        "task_type": "MTT",
        "task_location": folder_path,
        "task_updated_on": raw.get("updateTime", ""),
        "runtime_environment": rt,
        "task_mapping": mapping,
        "transformations": transformations,
        "parameter_file": {
            "directory": raw.get("parameterFileDir"),
            "name": raw.get("parameterFileName"),
        },
        "advanced_options": {
            "pre_processing_commands": raw.get("preProcessingCmd"),
            "post_processing_commands": raw.get("postProcessingCmd"),
        },
        "enrichment_warnings": warnings,
        "raw": raw,
    }


def _discover_frs_id(client, folder_path, task_type, task_name):
    """共享 FRS discovery（Amendment 9）：folder + type 枚举 → 精确 name 匹配 → frs_id。

    三种 task type 复用同一 0/1/>1 判定与 candidates 结构，避免各自重复实现。
    返回 (frs_id, error_dict|None)：
      - 枚举失败 → (None, listed_error)
      - 0 match → (None, TaskNotFoundError)
      - >1 match → (None, AmbiguousTaskError, candidates 用 frs_id key)
      - 1 match → (frs_id, None)

    AmbiguousTaskError 仅表示「V3 discovery 结果内无法唯一确定 FRS object」，
    不代表 DSS/WORKFLOW 的 V2 by-name detail 已能按 folder 消歧。
    """
    listed = list_tasks(client, folder_path=folder_path, task_type=task_type)
    if isinstance(listed, dict) and "error" in listed:
        return None, listed
    matches = [
        t for t in listed.get("tasks", [])
        if t.get("name") == task_name and t.get("type") == task_type
    ]
    if not matches:
        return None, {"error": f"{task_type} task not found in folder",
                      "error_type": "TaskNotFoundError"}
    if len(matches) > 1:
        candidates = [
            {
                "frs_id": m.get("frs_id", ""),
                "path": m.get("path", ""),
                "type": m.get("type", ""),
                "name": m.get("name", ""),
            }
            for m in matches
        ]
        return None, {
            "error": f"Multiple {task_type} tasks matched in the same folder",
            "error_type": "AmbiguousTaskError",
            "candidates": candidates,
        }
    return matches[0].get("frs_id", ""), None


def _mtt_detail_by_frs(client, frs_id, task_name, folder_path, resolve_connections):
    """MTT：已知 frs_id → mttask/frs/{frs_id} → 归一化 detail。"""
    try:
        resp = client._request("GET", _V2_MTT_FRS + frs_id, use_v3=False)
        raw = resp.json()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "error_type": type(e).__name__}
    return _build_mtt_detail(
        client, raw, frs_id, task_name, folder_path, resolve_connections
    )


def _dss_wf_detail_by_name(client, task_name, task_type, frs_id, resolve_connections):
    """DSS / WORKFLOW：V2 by-name detail（Amendment 9：注入 id=classic + frs_id）。

    frs_id 来自 folder-scoped V3 discovery，仅用于回填结果，不参与 by-name detail 消歧。
    id 为 by-name detail response 的 classic V2 ID。
    """
    try:
        path = _V2_DETAIL_PATH[task_type] + task_name
        resp = client._request("GET", path, use_v3=False)
        raw = resp.json()
        detail = {
            "name": task_name,
            "type": task_type,
            "id": raw.get("id", ""),   # classic V2 ID（by-name response）
            "frs_id": frs_id,          # FRS object ID（folder-scoped discovery）
        }
        return _apply_extraction(client, detail, raw, task_type, resolve_connections)
    except Exception as e:  # noqa: BLE001 — 工具层统一结构化错误
        return {"error": str(e), "error_type": type(e).__name__}


def get_task_detail(
    client,
    task_name: str = "",
    task_type: str = "",
    folder_path: str = "",
    task_id: str = "",
    task_id_type: str = "",
    resolve_connections: bool = True,
) -> dict:
    """获取单个 task 详情（Amendment 9：三类型统一 FRS discovery + 双 ID 契约）。

    - 三种类型均以 name + folder 经共享 _discover_frs_id 取 frs_id：
      MTT → mttask/frs/{frs_id}；DSS/WORKFLOW → dsstask|workflow/name/{name}。
    - 内部编排层可传 task_id(=frs_id) + task_id_type="frs" 跳过 discovery（三类型一致）；
      CLI / selected 输入不暴露该参数。
    - 返回统一含 id=classic V2 ID、frs_id=FRS object ID。
    """
    if task_type not in _DETAIL_TASK_TYPES:
        return {
            "error": f"invalid task_type: {task_type}",
            "error_type": "ValueError",
        }

    # 参数冲突：task_id(=frs_id 直传) 不得与 folder_path（discovery 驱动）并存。
    # 三类型一致；DSS/WORKFLOW 直传仍需 task_name 构造 by-name URL，故 name 不算冲突。
    if task_id and folder_path:
        return {
            "error": "task_id cannot be combined with folder_path",
            "error_type": "ConflictingArgumentsError",
        }

    # ── 内部 FRS 直传旁路：task_id + task_id_type="frs"（三类型一致校验）──────
    if task_id or task_id_type:
        if task_id_type != "frs":
            # classic / 缺失 / 其它 → InvalidIdTypeError，不发 HTTP
            return {
                "error": "FRS detail lookup requires task_id_type='frs'",
                "error_type": "InvalidIdTypeError",
            }
        if not task_id:
            return {"error": "task_id is required when task_id_type='frs'",
                    "error_type": "ValueError"}
        if task_type == "MTT":
            return _mtt_detail_by_frs(client, task_id, "", "", resolve_connections)
        # DSS / WORKFLOW：by-name 仍需 name；frs_id 回填
        if not task_name:
            return {"error": "task_name is required for DSS/WORKFLOW by-name detail",
                    "error_type": "ValueError"}
        return _dss_wf_detail_by_name(
            client, task_name, task_type, task_id, resolve_connections
        )

    # ── name + folder 模式（三类型统一 FRS discovery）────────────────────────
    if not task_name:
        return {"error": "task_name is required", "error_type": "ValueError"}
    if not folder_path:
        return {"error": f"folder_path is required for {task_type} detail lookup",
                "error_type": "MissingFolderError"}

    frs_id, err = _discover_frs_id(client, folder_path, task_type, task_name)
    if err:
        return err
    if task_type == "MTT":
        return _mtt_detail_by_frs(
            client, frs_id, task_name, folder_path, resolve_connections
        )
    return _dss_wf_detail_by_name(
        client, task_name, task_type, frs_id, resolve_connections
    )


def resolve_connection(client, connection_ids: list) -> dict:
    """把 connection id 解析成 connection name（V2 connection/<id>，带缓存）。

    返回: {"resolved": {id: name}, "failed": [id, ...]}
    单个 id 失败不影响其他 id；整体不向上抛异常。
    """
    if not connection_ids or not isinstance(connection_ids, list):
        return {"resolved": {}, "failed": []}

    # 去重保序、跳过空值
    ordered = []
    for cid in connection_ids:
        if cid and isinstance(cid, str) and cid not in ordered:
            ordered.append(cid)

    resolved = {}
    failed = []

    def _fetch(cid):
        resp = client._request("GET", f"connection/{cid}", use_v3=False)
        data = resp.json()
        return data.get("name") or data.get("connectionName") or data.get("id") or ""

    for cid in ordered:
        try:
            cache = getattr(client, "connection_cache", None)
            if cache is not None:
                name = cache.get_or_fetch(cid, _fetch)
            else:
                name = _fetch(cid)
            resolved[cid] = name
        except Exception:  # noqa: BLE001 — 单 id 失败记入 failed，继续
            failed.append(cid)

    return {"resolved": resolved, "failed": failed}

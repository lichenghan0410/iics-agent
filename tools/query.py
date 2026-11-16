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

import math
import re
from datetime import datetime, timedelta, timezone

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


# ── MTT Last Run（Amendment 11）───────────────────────────────────────────────
_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)
_NUMERIC_STR_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_LAST_RUN_FIELD = "Last Run"


def _short_repr(value, limit: int = 60) -> str:
    s = repr(value)
    return s if len(s) <= limit else s[:limit - 3] + "..."


# Last Run 支持的 ISO 8601 输入子集（extended 格式；Python 3.10 / 3.11+ 行为一致，
# 不使用各版本可接受格式不同的 datetime.fromisoformat）：
#   YYYY-MM-DD(T|t|空格)HH:MM[:SS[.f{1,6}]](Z|z|±HH:MM|±HHMM)
# 时区必填；小数秒 1–6 位（右补零到微秒，输出截断到毫秒）；偏移 hh ≤ 23、mm ≤ 59。
# 不支持：basic 格式（无分隔符）、仅日期、小数秒 > 6 位、±HH、逗号小数、闰秒 60。
_ISO_DATETIME_RE = re.compile(
    r"^(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})[Tt ]"
    r"(?P<h>\d{2}):(?P<mi>\d{2})(?::(?P<s>\d{2})(?:\.(?P<f>\d{1,6}))?)?"
    r"(?P<tz>[Zz]|[+-]\d{2}:?\d{2})?$"
)


def _parse_iso_datetime(s: str):
    """支持子集内的 ISO 字符串 → (aware datetime, None)；否则 (None, reason)。"""
    m = _ISO_DATETIME_RE.match(s)
    if not m:
        return None, "unparseable datetime string"
    tz = m.group("tz")
    if tz is None:
        return None, "datetime without timezone"
    if tz in ("Z", "z"):
        offset = timedelta(0)
    else:
        digits = tz[1:].replace(":", "")
        hh, mm = int(digits[:2]), int(digits[2:])
        if hh > 23 or mm > 59:
            return None, "invalid timezone offset"
        offset = timedelta(hours=hh, minutes=mm)
        if tz[0] == "-":
            offset = -offset
    frac = m.group("f")
    micro = int(frac.ljust(6, "0")) if frac else 0
    try:
        dt = datetime(int(m.group("y")), int(m.group("mo")), int(m.group("d")),
                      int(m.group("h")), int(m.group("mi")), int(m.group("s") or 0),
                      micro, tzinfo=timezone(offset))
    except ValueError:
        return None, "unparseable datetime string"
    return dt, None


def _format_utc_ms(dt: datetime) -> str:
    """aware datetime → UTC `yyyy-MM-ddTHH:mm:ss.SSSZ`（不依赖本机时区）。"""
    dt = dt.astimezone(timezone.utc)
    return (f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}T{dt.hour:02d}:"
            f"{dt.minute:02d}:{dt.second:02d}.{dt.microsecond // 1000:03d}Z")


def parse_last_run_time(value):
    """MTT raw.lastRunTime → (last_run | None, warning_message | None)。

    - 正数 JSON 数值（int / 有限 float）按 Unix epoch **毫秒**转换（不猜测为秒）。
    - 带明确时区的 ISO 日期时间（含 Z；支持子集见 _ISO_DATETIME_RE）→ 转 UTC 并规范化。
    - 缺失 / None / 空字符串 / 数值 0 → (None, None)：不宣称「从未运行」，不告警。
    - 数值字符串、非法字符串、布尔值、负数、非有限值、越界值、不含时区的日期时间
      → (None, warning)；不推测时区，不把数值字符串当 epoch。
    实测（API Notes §7.7）：lastRunTime 为 epoch 毫秒，与官方「UTC ISO」约定不符。
    """
    def bad(reason):
        return None, f"{_LAST_RUN_FIELD}: invalid value {_short_repr(value)} ({reason})"

    if value is None:
        return None, None
    if isinstance(value, bool):          # bool 是 int 子类，必须先排除
        return bad("boolean")
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return bad("non-finite number")
        if value == 0:
            return None, None
        if value < 0:
            return bad("negative epoch milliseconds")
        try:
            return _format_utc_ms(_EPOCH_UTC + timedelta(milliseconds=value)), None
        except (OverflowError, ValueError):
            return bad("epoch milliseconds out of range")
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None, None
        if _NUMERIC_STR_RE.match(s):
            return bad("numeric string is not parsed as epoch")
        dt, reason = _parse_iso_datetime(s)
        if dt is None:
            return bad(reason)
        try:
            return _format_utc_ms(dt), None
        except (OverflowError, ValueError):
            return bad("datetime out of range")
    return bad(f"unsupported type {type(value).__name__}")


# ── MTT connection parameter（Amendment 11）──────────────────────────────────
# uiProperties / runtimeParameterData 不在官方公开字段表中：按已知样本兼容解析，
# 不把字段命名推测当作官方语义。
def _parse_bool_flag(value):
    """安全解析 JSON boolean 或 "true"/"false" 字符串（去空白、忽略大小写）。

    返回 (True | False | None, invalid)。None → (None, False)（视为缺失）；
    其它无法解析的值 → (None, True)。禁止 bool("false") 式误判。
    """
    if value is None:
        return None, False
    if isinstance(value, bool):
        return value, False
    if isinstance(value, str):
        s = value.strip().lower()
        if s == "true":
            return True, False
        if s == "false":
            return False, False
    return None, True


def _nonempty_str(value):
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _connection_parameter_info(param: dict, side: str, note) -> dict:
    """本侧连接的参数化信息（不含 id / name / display_value）。

    side = "Source" / "Target"；note(field, message) 收集 enrichment issue。
    - parameterized：uiProperties.connectionParameterized 明确 true/false 优先；字段缺失时
      仅当 paramType-mapping == "Connection" 或 runtimeParameterData.connectionParameterName
      非空才判 True，否则 None；非法值 → None + issue。明确 false 与参数名冲突 → False + issue。
    - parameter_name：仅对 parameterized=True 提取；connectionParameterName 优先，
      fallback uiProperties.paramName；冲突取前者 + issue；缺名 → None + issue。
    - configured_value：uiProperties.connDefault（缺失 None）。
    - is_connection_runtime_parameter：仅映射 runtimeParameterData.isConnectionRuntimeParameter，
      不参与 parameterized 判定，语义未确认。
    """
    ui = param.get("uiProperties")
    ui = ui if isinstance(ui, dict) else {}
    rpd = param.get("runtimeParameterData")
    rpd = rpd if isinstance(rpd, dict) else {}
    field = f"{side} Connection Parameter"

    rpd_name = _nonempty_str(rpd.get("connectionParameterName"))
    ui_name = _nonempty_str(ui.get("paramName"))
    flag_raw = ui.get("connectionParameterized")
    flag, invalid = _parse_bool_flag(flag_raw)

    if invalid:
        parameterized = None
        note(field, f"invalid connectionParameterized value {_short_repr(flag_raw)}")
    elif flag is not None:
        parameterized = flag
        if flag is False and (rpd_name or ui_name):
            note(field, "connectionParameterized=false conflicts with parameter name "
                        f"{_short_repr(rpd_name or ui_name)}; treated as not parameterized")
    else:
        mapping_type = ui.get("paramType-mapping")
        evidence = (isinstance(mapping_type, str) and mapping_type.strip() == "Connection")
        parameterized = True if (evidence or rpd_name) else None

    parameter_name = None
    if parameterized is True:
        if rpd_name:
            parameter_name = rpd_name
            if ui_name and ui_name != rpd_name:
                note(field, f"connectionParameterName {_short_repr(rpd_name)} conflicts with "
                            f"paramName {_short_repr(ui_name)}; using connectionParameterName")
        elif ui_name:
            parameter_name = ui_name
        else:
            note(field, "parameterized but name missing")

    is_runtime, _ = _parse_bool_flag(rpd.get("isConnectionRuntimeParameter"))
    return {
        "parameterized": parameterized,
        "parameter_name": parameter_name,
        "configured_value": _nonempty_str(ui.get("connDefault")),
        "is_connection_runtime_parameter": is_runtime,
    }


def _mtt_connection(conn_id: str, param: dict, side: str, note) -> dict:
    """MTT 本侧 connection 的 normalized 结构；display_value 初始为 ID（--no-resolve）。"""
    info = _connection_parameter_info(param, side, note)
    return {"id": conn_id, "name": "", **info, "display_value": conn_id}


class _FieldIssues:
    """connection_parameter warning 合并器：同一 transformation 的同一字段最多 1 条。"""

    def __init__(self):
        self._items = {}

    def add(self, pos, transformation_name, field, message):
        item = self._items.setdefault((pos, field), {
            "transformation_name": transformation_name, "field": field, "messages": []})
        if message not in item["messages"]:
            item["messages"].append(message)

    def flush(self, warnings: list) -> None:
        for key in sorted(self._items):
            item = self._items[key]
            label = item["transformation_name"] or "<unnamed>"
            warnings.append({
                "kind": "connection_parameter",
                "transformation_name": item["transformation_name"],
                "field": item["field"],
                "error_type": "EnrichmentError",
                "error": f"{label} / {item['field']}: " + "; ".join(item["messages"]),
            })


def _extract_mtt_transformations(raw: dict, issues=None, positions=None) -> list:
    """从 MTT raw.parameters[] 严格按顺序展开 Source/Target transformation。

    以存在 sourceConnectionId / targetConnectionId 为可展开依据（未按 type 过滤）；
    不去重、不取首尾。connection name 由调用方后续解析回填。
    issues（_FieldIssues）收集连接参数 warning；positions 记录每行对应的 parameters 下标。
    明确参数化但缺少 connection ID 的 SOURCE/TARGET 项：只记 1 条 warning，不生成行。
    """
    if issues is None:
        issues = _FieldIssues()
    out = []
    params = raw.get("parameters") if isinstance(raw, dict) else None
    if not isinstance(params, list):
        return out
    for pos, p in enumerate(params):
        if not isinstance(p, dict):
            continue
        tname = _transformation_name(p)

        def note(field, msg, _pos=pos, _tname=tname):
            issues.add(_pos, _tname, field, msg)

        if p.get("sourceConnectionId"):
            src_obj, cq = _extract_source_object(p)
            out.append({
                "name": tname,
                "kind": "source",
                "source_connection": _mtt_connection(
                    p["sourceConnectionId"], p, "Source", note),
                "source_object": src_obj,
                "custom_query": cq,
                "target_connection": None,
                "target_object": None,
                "operation": None,
            })
        elif p.get("targetConnectionId"):
            out.append({
                "name": tname,
                "kind": "target",
                "source_connection": None,
                "source_object": None,
                "custom_query": None,
                "target_connection": _mtt_connection(
                    p["targetConnectionId"], p, "Target", note),
                "target_object": _extract_target_object(p),
                "operation": p.get("operationType"),
            })
        else:
            ptype = p.get("type")
            ptype = ptype.strip().upper() if isinstance(ptype, str) else ""
            if ptype in ("SOURCE", "TARGET"):
                side = "Source" if ptype == "SOURCE" else "Target"
                info = _connection_parameter_info(p, side, lambda _f, _m: None)
                if info["parameterized"] is True:
                    note(f"{side} Connection",
                         "parameterized connection has no connection ID; no row generated")
            continue
        if positions is not None:
            positions.append(pos)
    return out


def _real_connection_name(conn_id, resolved: dict, failed, has_name: dict) -> str | None:
    """「真实解析名称」判定：只看 resolver 记录的名称来源，不比较名称与 ID。

    id 在 failed 或不在 resolved → 无；has_name[id] 不为 True（响应无 name /
    connectionName，resolver 用 id 或 "" 兜底）→ 无；否则 resolved[id] 即真实名称
    ——即使它恰好等于 connection ID。
    """
    if conn_id in failed or conn_id not in resolved:
        return None
    if has_name.get(conn_id) is not True:
        return None
    name = resolved.get(conn_id)
    if name is None or name == "":
        return None
    return name if isinstance(name, str) else str(name)


def _apply_mtt_connection_display(conn: dict, resolved: dict, failed, has_name: dict,
                                  field: str, note) -> None:
    """默认模式 Connection 展示：真实解析名称 → connDefault（仅确认参数化）→ ID。

    请求失败已有 kind=connection warning，connDefault 兜底时不再另加；
    仅响应无名称（ID 兜底）时为该字段记 1 条 warning；名称与 connDefault 不一致记差异。
    """
    cid = conn.get("id")
    real = _real_connection_name(cid, resolved, failed, has_name)
    configured = conn.get("configured_value")
    parameterized = conn.get("parameterized") is True
    request_failed = cid in failed
    if real:
        conn["name"] = real
        conn["display_value"] = real
        if parameterized and configured and configured != real:
            note(field, f"resolved name {_short_repr(real)} differs from connDefault "
                        f"{_short_repr(configured)}; showing resolved name")
    elif parameterized and configured:
        conn["display_value"] = configured
        if not request_failed:
            note(field, f"connection {cid} returned no name; showing connDefault "
                        f"{_short_repr(configured)}")
    else:
        conn["display_value"] = cid
        if not request_failed:
            note(field, f"connection {cid} returned no name; showing connection ID")


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

    # Last Run（Amendment 11）：唯一来源 raw.lastRunTime；非法值只记 warning
    last_run, last_run_error = parse_last_run_time(raw.get("lastRunTime"))
    if last_run_error:
        warnings.append({"kind": "last_run", "field": _LAST_RUN_FIELD,
                         "error_type": "EnrichmentError", "error": last_run_error})

    issues = _FieldIssues()
    positions = []
    transformations = _extract_mtt_transformations(raw, issues, positions)

    # 解析每个 transformation 的 source/target connection 名（带缓存 + warning 分级）。
    # --no-resolve：不调用 resolver，display_value 保持 connection ID，不产生失败告警。
    if resolve_connections and transformations:
        conn_ids = []
        for t in transformations:
            for key in ("source_connection", "target_connection"):
                c = t.get(key)
                if c and c.get("id") and c["id"] not in conn_ids:
                    conn_ids.append(c["id"])
        if conn_ids:
            res = _resolve_connection_entries(client, conn_ids)
            resolved = res.get("resolved", {})
            failed = set(res.get("failed", []))
            has_name = res.get("has_name", {})
            for pos, t in zip(positions, transformations):
                for key, field in (("source_connection", "Source Connection"),
                                   ("target_connection", "Target Connection")):
                    c = t.get(key)
                    if c and c.get("id"):
                        _apply_mtt_connection_display(
                            c, resolved, failed, has_name, field,
                            lambda f, m, _p=pos, _n=t.get("name", ""):
                                issues.add(_p, _n, f, m))
            for fid in res.get("failed", []):
                warnings.append({"kind": "connection", "id": fid,
                                 "error": "connection resolve failed",
                                 "error_type": "EnrichmentError"})
    issues.flush(warnings)

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
        "last_run": last_run,      # Amendment 11：UTC ISO（毫秒，Z）或 None
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


def _resolve_connection_entries(client, connection_ids: list) -> dict:
    """resolve_connection 的内部实现，额外保留名称来源（Amendment 11 review 修正）。

    返回: {"resolved": {id: name}, "failed": [id, ...], "has_name": {id: bool}}
    - resolved 的值与既有契约完全相同：name → connectionName → id → ""。
    - has_name[id] 表示该值是否真的来自响应的 name / connectionName；为 False 时是
      resolver 的 id / "" 兜底。来源信息与名称一起写入 ConnectionCache，缓存命中时同样可用。
    """
    if not connection_ids or not isinstance(connection_ids, list):
        return {"resolved": {}, "failed": [], "has_name": {}}

    # 去重保序、跳过空值
    ordered = []
    for cid in connection_ids:
        if cid and isinstance(cid, str) and cid not in ordered:
            ordered.append(cid)

    resolved = {}
    failed = []
    has_name = {}

    def _fetch_entry(cid):
        resp = client._request("GET", f"connection/{cid}", use_v3=False)
        data = resp.json()
        for key in ("name", "connectionName"):
            value = data.get(key)
            if value:                      # 与既有 `or` 链同一真值判断
                return value, True
        return (data.get("id") or ""), False

    for cid in ordered:
        try:
            cache = getattr(client, "connection_cache", None)
            if cache is not None:
                name, named = cache.get_or_fetch_entry(cid, _fetch_entry)
            else:
                name, named = _fetch_entry(cid)
            resolved[cid] = name
            has_name[cid] = named
        except Exception:  # noqa: BLE001 — 单 id 失败记入 failed，继续
            failed.append(cid)

    return {"resolved": resolved, "failed": failed, "has_name": has_name}


def resolve_connection(client, connection_ids: list) -> dict:
    """把 connection id 解析成 connection name（V2 connection/<id>，带缓存）。

    返回: {"resolved": {id: name}, "failed": [id, ...]}
    单个 id 失败不影响其他 id；整体不向上抛异常。
    （名称来源信息只供 MTT 内部使用，不进入本函数输出——resolve-connection CLI /
    DSS 调用方契约不变。）
    """
    res = _resolve_connection_entries(client, connection_ids)
    return {"resolved": res["resolved"], "failed": res["failed"]}

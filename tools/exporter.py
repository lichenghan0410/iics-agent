"""
tools/exporter.py — export_results（openpyxl）

Excel 业务数据主 sheet 为 `Task Summary`，错误/警告记录 sheet 为 `Export Errors`。
- DSS / WORKFLOW：1 task = 1 行。
- MTT：每个有效 Source/Target transformation = 1 行（任务级字段在每行重复，不 merge cells）；
  无 transformation 或详情失败时仍保留至少 1 行任务基础信息。
JSON 导出整份 data（含 details / failed_details）。

计数语义：
- task_count = list_tasks 得到的逻辑 task 数（== list_tasks.count）。
- rows       = Task Summary 实际物理数据行数（不含表头；MTT 展开后可能 > task_count）。
- failed_details_count     = 主详情请求失败的 task 数。
- enrichment_warnings_count= mapping/runtime/connection enrichment 警告数（不计入 failed）。
"""

import json
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment

# Task Summary 列顺序（23 列）
_COLS = [
    "@type", "id", "orgId",
    "Task Name", "Task Type", "Task Location", "Runtime Environment",
    "Task Updated On",
    "Mapping Name", "Mapping Location", "Mapping Updated On",
    "Transformation Name", "Source Connection", "Source Object", "Custom Query",
    "Target Connection", "Operation", "Target Object",
    "Parameter File Directory", "Parameter File Name",
    "Pre-Processing Commands", "Post-Processing Commands",
    "Status",
]
_SOURCE_OBJECT_COL = _COLS.index("Source Object")  # 0-based；用于 wrap text

_EXPORT_ERROR_HEADERS = ["level", "task_name", "task_type", "folder", "error_type", "error"]


def _conn_display(conn) -> str:
    """优先 connection name，否则 id，否则空。"""
    if not isinstance(conn, dict):
        return ""
    return conn.get("name") or conn.get("id") or ""


def _val_to_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        seen = []
        for item in v:
            s = _val_to_str(item)
            if s and s not in seen:
                seen.append(s)
        return ", ".join(seen)
    if isinstance(v, dict):
        for k in ("name", "objectName", "label", "id"):
            if v.get(k):
                return str(v[k])
        s = json.dumps(v, ensure_ascii=False)
        return s if len(s) <= 120 else s[:117] + "..."
    return str(v)


def _get_nested(raw: dict, dotted: str):
    cur = raw
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def extract_source_target_objects(raw: dict, task_type: str) -> dict:
    """DSS best-effort 源/目标对象名（沿用；供 DSS 行填充 Source/Target Object）。"""
    result = {"source_objects": "", "target_object": ""}
    if not isinstance(raw, dict):
        return result
    if task_type == "DSS":
        for f in ("sourceObject", "sourceObjects", "source.object", "source.objects"):
            v = _get_nested(raw, f)
            if v not in (None, "", [], {}):
                result["source_objects"] = _val_to_str(v)
                break
        for f in ("targetObject", "target.object"):
            v = _get_nested(raw, f)
            if v not in (None, "", [], {}):
                result["target_object"] = _val_to_str(v)
                break
    else:
        srcs, tgts = [], []

        def _merge(bucket, s):
            for part in s.split(", "):
                if part and part not in bucket:
                    bucket.append(part)

        def walk(node):
            if isinstance(node, dict):
                for k, val in node.items():
                    kl = k.lower()
                    if "source" in kl and "object" in kl:
                        _merge(srcs, _val_to_str(val))
                    elif "target" in kl and "object" in kl:
                        _merge(tgts, _val_to_str(val))
                    else:
                        walk(val)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(raw)
        result["source_objects"] = ", ".join(srcs)
        result["target_object"] = ", ".join(tgts)
    return result


def _task_level_row(rec: dict, top_folder: str) -> dict:
    """从 detail / task 记录抽取任务级列值（transformation 列默认空）。"""
    is_mtt = rec.get("task_type") == "MTT" or rec.get("type") == "MTT"
    name = rec.get("task_name") or rec.get("name") or ""
    ttype = rec.get("task_type") or rec.get("type") or ""
    loc = rec.get("task_location") or rec.get("folder") or top_folder
    rt = rec.get("runtime_environment")
    rt_name = rt.get("name") if isinstance(rt, dict) else (rt or "")
    mapping = rec.get("task_mapping") or {}
    pf = rec.get("parameter_file") or {}
    ao = rec.get("advanced_options") or {}

    row = {c: "" for c in _COLS}
    row["@type"] = rec.get("@type", "")
    row["id"] = rec.get("id", "")
    row["orgId"] = rec.get("orgId", "")
    row["Task Name"] = name
    row["Task Type"] = ttype
    row["Task Location"] = loc
    row["Runtime Environment"] = rt_name or ""
    row["Task Updated On"] = rec.get("task_updated_on", "")
    row["Mapping Name"] = mapping.get("name") or ""
    row["Mapping Location"] = mapping.get("location") or ""   # 当前恒 None → 空
    row["Mapping Updated On"] = mapping.get("updated_on") or ""
    row["Parameter File Directory"] = pf.get("directory")
    row["Parameter File Name"] = pf.get("name")
    row["Pre-Processing Commands"] = ao.get("pre_processing_commands")
    row["Post-Processing Commands"] = ao.get("post_processing_commands")
    row["Status"] = rec.get("status", "")

    if not is_mtt:
        # DSS / WORKFLOW：填 Source/Target Connection + Object（现状抽取）
        row["Source Connection"] = _conn_display(rec.get("source_connection"))
        row["Target Connection"] = _conn_display(rec.get("target_connection"))
        objs = extract_source_target_objects(rec.get("raw") or {}, ttype)
        row["Source Object"] = objs["source_objects"]
        row["Target Object"] = objs["target_object"]
    return row


def _rows_for_record(rec: dict, top_folder: str) -> list:
    """一条 detail/task 记录 → 若干 Task Summary 行（MTT 按 transformation 展开）。"""
    base = _task_level_row(rec, top_folder)
    is_mtt = rec.get("task_type") == "MTT" or rec.get("type") == "MTT"
    transforms = rec.get("transformations") if is_mtt else None

    if not (isinstance(transforms, list) and transforms):
        return [base]   # 非 MTT、或 MTT 无 transformation/失败 → 1 基础行

    out = []
    for t in transforms:
        row = dict(base)
        row["Transformation Name"] = t.get("name") or ""
        sc = t.get("source_connection")
        row["Source Connection"] = _conn_display(sc) if sc else ""
        row["Source Object"] = t.get("source_object")     # str / list / "" / None
        row["Custom Query"] = t.get("custom_query")
        tc = t.get("target_connection")
        row["Target Connection"] = _conn_display(tc) if tc else ""
        row["Operation"] = t.get("operation")
        row["Target Object"] = t.get("target_object")
        out.append(row)
    return out


def _build_rows(data: dict) -> list:
    details = data.get("details")
    top_folder = data.get("folder", "")
    if isinstance(details, list) and details:
        records = details
    else:
        records = data.get("tasks") or []
    rows = []
    for rec in records:
        if isinstance(rec, dict):
            rows.extend(_rows_for_record(rec, top_folder))
    return rows


def _cell(v):
    """列值 → 单元格值。None→空；list→换行连接（多对象）；其它→原值。"""
    if v is None:
        return None
    if isinstance(v, list):
        parts, seen = [], []
        for x in v:
            s = "" if x is None else str(x)
            if s and s not in seen:
                seen.append(s)
                parts.append(s)
        return "\n".join(parts)
    return v


def _collect_enrichment_warnings(data: dict) -> list:
    out = []
    for rec in (data.get("details") or []):
        if not isinstance(rec, dict):
            continue
        for w in (rec.get("enrichment_warnings") or []):
            ew = dict(w)
            ew.setdefault("task_name", rec.get("task_name") or rec.get("name", ""))
            ew.setdefault("task_type", rec.get("task_type") or rec.get("type", ""))
            ew.setdefault("folder", rec.get("task_location") or rec.get("folder", ""))
            out.append(ew)
    return out


def _ensure_suffix(filename: str, suffix: str) -> str:
    p = Path(filename)
    if p.suffix.lower() != suffix:
        p = p.with_name(p.name + suffix)
    return str(p)


def export_results(
    client,
    data: dict,
    format: str = "excel",
    filename: str = "",
) -> dict:
    """导出为 Excel（Task Summary + Export Errors）/ JSON。client 未使用，保留契约。"""
    try:
        if format not in ("excel", "json"):
            return {"error": "format must be 'excel' or 'json'",
                    "error_type": "ValueError"}
        if not isinstance(data, dict):
            return {"error": "data must be a dict", "error_type": "ValueError"}

        suffix = ".xlsx" if format == "excel" else ".json"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if not filename:
            filename = f"iics_export_{timestamp}{suffix}"
        filename = _ensure_suffix(filename, suffix)
        out_path = Path(filename).resolve()

        data_rows = _build_rows(data)
        rows = len(data_rows)
        task_count = len(data.get("tasks") or [])

        failed = data.get("failed_details")
        if not isinstance(failed, list):
            failed = []
        warnings = _collect_enrichment_warnings(data)

        if format == "json":
            out_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        else:
            wb = Workbook()
            ws = wb.active
            ws.title = "Task Summary"
            ws.append(_COLS)
            wrap = Alignment(wrap_text=True, vertical="top")
            for r in data_rows:
                ws.append([_cell(r.get(c)) for c in _COLS])
                # 多对象 Source Object 换行 → wrap text
                so = r.get("Source Object")
                if isinstance(so, list) or (isinstance(so, str) and "\n" in so):
                    ws.cell(row=ws.max_row, column=_SOURCE_OBJECT_COL + 1).alignment = wrap

            ws_err = wb.create_sheet("Export Errors")
            ws_err.append(_EXPORT_ERROR_HEADERS)
            if not failed and not warnings:
                ws_err.append(["No export errors"])
            else:
                for f in failed:
                    ws_err.append(["error", f.get("name", ""), f.get("type", ""),
                                   f.get("folder", ""), f.get("error_type", ""),
                                   f.get("error", "")])
                for w in warnings:
                    ws_err.append(["warning", w.get("task_name", ""),
                                   w.get("task_type", ""), w.get("folder", ""),
                                   w.get("error_type", ""),
                                   f"{w.get('kind', '')}: {w.get('error', '')}"])
            wb.save(out_path)

        summary = f"已导出 {rows} 行（task_count={task_count}）至 {out_path}"
        if failed:
            summary += f"；{len(failed)} 条主详情失败"
        if warnings:
            summary += f"；{len(warnings)} 条 enrichment 警告"

        return {
            "file_path": str(out_path),
            "format": format,
            "task_count": task_count,
            "rows": rows,
            "failed_details_count": len(failed),
            "failed_details": failed,
            "enrichment_warnings_count": len(warnings),
            "enrichment_warnings": warnings,
            "summary": summary,
        }
    except Exception as e:  # noqa: BLE001 — 工具层统一结构化错误
        return {"error": str(e), "error_type": type(e).__name__}

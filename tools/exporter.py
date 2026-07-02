"""
tools/exporter.py — export_results（openpyxl，单 sheet）

v0.1.1：Excel 导出两个 sheet：
  - Task Summary : name, type, folder, runtime_agent, source_conn, target_conn,
                   source_objects, target_object, status
  - Export Errors: task_name, task_type, folder, error_type, error
                   （data["failed_details"]；无错误也建表并写 "No export errors"）
原 Field Mapping / Truncation Risk 已移除。JSON 导出整份 data（含 failed_details）。
"""

import json
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

_SUMMARY_HEADERS = [
    "name",
    "type",
    "folder",
    "runtime_agent",
    "source_conn",
    "target_conn",
    "source_objects",
    "target_object",
    "status",
]
_EXPORT_ERROR_HEADERS = ["task_name", "task_type", "folder", "error_type", "error"]


def _conn_display(conn) -> str:
    """优先取 connection name，否则 id，否则空。"""
    if not isinstance(conn, dict):
        return ""
    return conn.get("name") or conn.get("id") or ""


def _val_to_str(v) -> str:
    """best-effort 把字段值转成可读字符串。

    - list → 去重保序逗号分隔
    - dict → name/objectName/label/id，否则短 JSON
    - 其它 → str()
    """
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
    """按点分路径取值，缺失返回 None。"""
    cur = raw
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def extract_source_target_objects(raw: dict, task_type: str) -> dict:
    """best-effort 从 task detail raw 中提取源/目标对象名，缺失留空、不报错。

    返回: {"source_objects": "...", "target_object": "..."}
    """
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
        # MTT / WORKFLOW：递归扫描 key 含 source+object / target+object 的字段
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


def _build_summary_rows(data: dict) -> list:
    """兼容 list_tasks 形态(A) 与 CLI 聚合形态(B)，details 优先。"""
    details = data.get("details")
    if isinstance(details, list) and details:
        records = details
    else:
        records = data.get("tasks") or []

    top_folder = data.get("folder", "")
    rows = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        objs = extract_source_target_objects(
            rec.get("raw") or {}, rec.get("type", "")
        )
        rows.append(
            [
                rec.get("name", ""),
                rec.get("type", ""),
                rec.get("folder", top_folder),
                rec.get("runtime_agent", ""),
                _conn_display(rec.get("source_connection")),
                _conn_display(rec.get("target_connection")),
                objs["source_objects"],
                objs["target_object"],
                rec.get("status", ""),
            ]
        )
    return rows


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
    """导出查询结果为 Excel（单 sheet Task Summary）/ JSON。client 未使用，保留契约。

    返回: {"file_path","format","rows","summary"} 或 {"error","error_type"}
    """
    try:
        if format not in ("excel", "json"):
            return {
                "error": "format must be 'excel' or 'json'",
                "error_type": "ValueError",
            }
        if not isinstance(data, dict):
            return {
                "error": "data must be a dict",
                "error_type": "ValueError",
            }

        suffix = ".xlsx" if format == "excel" else ".json"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if not filename:
            filename = f"iics_export_{timestamp}{suffix}"
        filename = _ensure_suffix(filename, suffix)
        out_path = Path(filename).resolve()

        summary_rows = _build_summary_rows(data)
        rows = len(summary_rows)

        failed = data.get("failed_details")
        if not isinstance(failed, list):
            failed = []

        if format == "json":
            out_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        else:
            wb = Workbook()
            ws_summary = wb.active
            ws_summary.title = "Task Summary"
            ws_summary.append(_SUMMARY_HEADERS)
            for row in summary_rows:
                ws_summary.append(row)

            # Export Errors sheet（无错误也创建并写提示行）
            ws_err = wb.create_sheet("Export Errors")
            ws_err.append(_EXPORT_ERROR_HEADERS)
            if failed:
                for f in failed:
                    ws_err.append(
                        [
                            f.get("name", ""),
                            f.get("type", ""),
                            f.get("folder", ""),
                            f.get("error_type", ""),
                            f.get("error", ""),
                        ]
                    )
            else:
                ws_err.append(["No export errors"])

            wb.save(out_path)

        return {
            "file_path": str(out_path),
            "format": format,
            "rows": rows,
            "failed_details_count": len(failed),
            "failed_details": failed,
            "summary": f"已导出 {rows} 条记录至 {out_path}"
            + (f"（{len(failed)} 条详情失败）" if failed else ""),
        }
    except Exception as e:  # noqa: BLE001 — 工具层统一结构化错误
        return {"error": str(e), "error_type": type(e).__name__}

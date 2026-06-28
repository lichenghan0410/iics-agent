"""
tools/exporter.py — export_results（openpyxl，3 sheet）

v0.1 Read-only MVP。Step 8 implements export_results（Excel 三 sheet / JSON）。

Excel 输出:
  - Task Summary    : name, type, folder, runtime_agent, source_conn, target_conn, status
  - Field Mapping   : 仅 DSS，best-effort；无法解析写注释行"字段映射不可用"
  - Truncation Risk : best-effort；无法解析写注释行"截断风险分析不可用"
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
    "status",
]
_FIELD_MAPPING_HEADERS = ["task_name", "source_field", "target_field", "type", "length"]
_TRUNCATION_HEADERS = ["task_name", "field", "source_len", "target_len"]


def _conn_display(conn) -> str:
    """优先取 connection name，否则 id，否则空。"""
    if not isinstance(conn, dict):
        return ""
    return conn.get("name") or conn.get("id") or ""


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
        rows.append(
            [
                rec.get("name", ""),
                rec.get("type", ""),
                rec.get("folder", top_folder),
                rec.get("runtime_agent", ""),
                _conn_display(rec.get("source_connection")),
                _conn_display(rec.get("target_connection")),
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
    """导出查询结果为 Excel / JSON。client 参数保留以符合契约，未使用。

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

            # Field Mapping — best-effort 最小版本
            ws_fm = wb.create_sheet("Field Mapping")
            ws_fm.append(_FIELD_MAPPING_HEADERS)
            ws_fm.append(["字段映射不可用"])

            # Truncation Risk — best-effort 最小版本
            ws_tr = wb.create_sheet("Truncation Risk")
            ws_tr.append(_TRUNCATION_HEADERS)
            ws_tr.append(["截断风险分析不可用"])

            wb.save(out_path)

        return {
            "file_path": str(out_path),
            "format": format,
            "rows": rows,
            "summary": f"已导出 {rows} 条记录至 {out_path}",
        }
    except Exception as e:  # noqa: BLE001 — 工具层统一结构化错误
        return {"error": str(e), "error_type": type(e).__name__}

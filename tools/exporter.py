"""
tools/exporter.py — export_results（openpyxl，3 sheet）

v0.1 Read-only MVP 占位文件。
Implementation starts in later steps (Step 8)。

Excel 输出（实现于 Step 8）:
  - Task Summary    : name, type, folder, runtime_agent, source_conn, target_conn, status
  - Field Mapping   : 仅 DSS，best-effort
  - Truncation Risk : source_len > target_len 的字段，best-effort

本文件当前不实现任何 IICS API 调用或导出逻辑。
"""

# TODO(Step 8): def export_results(client, data: dict, format="excel", filename="") -> dict

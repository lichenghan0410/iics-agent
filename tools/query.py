"""
tools/query.py — 只读查询工具：list_tasks / get_task_detail / resolve_connection

v0.1 Read-only MVP 占位文件。
Implementation starts in later steps:
  - Step 5: list_tasks（含自动分页，V3 枚举）
  - Step 6: get_task_detail（同名消歧 + V2 路由 + connection 抽取）
  - Step 7: resolve_connection（ConnectionCache 读写）

约定（实现于后续 step）:
  - 每个工具函数接收 client: IICSClient 作为第一个参数
  - 返回 JSON 可序列化 dict，错误封装为 {"error": str(e), "error_type": type(e).__name__}
  - 不向上层抛异常

本文件当前不实现任何 IICS API 调用逻辑。
"""

# TODO(Step 5): def list_tasks(client, folder_path, task_type="ALL", page_size=200) -> dict
# TODO(Step 6): def get_task_detail(client, task_name, task_type, folder_path="", resolve_connections=True) -> dict
# TODO(Step 7): def resolve_connection(client, connection_ids: list[str]) -> dict

"""
iics_agent.py — Mode B 入口：Cowork Interactive Agent 对话循环

v0.1 Read-only MVP 占位文件。
Implementation starts in later steps (Step 10 完成 Mode B，依赖 anthropic SDK)。

v0.1 工具注册边界（实现于 Step 10）:
  - 只注册 4 个只读工具：list_tasks / get_task_detail / resolve_connection / export_results
  - 不出现任何写工具的 import 或注册

安全边界:
  - v0.1 只读，系统提示不提及写操作
  - 不得输出、保存或泄露 IICS_SESSION_TOKEN

本文件当前不实现任何 IICS API 调用或 Anthropic API 调用逻辑。
"""

# TODO(Step 10): SYSTEM_PROMPT / TOOLS / TOOL_MAP / Anthropic 对话主循环

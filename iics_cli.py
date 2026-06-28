"""
iics_cli.py — Mode A 入口：Claude Code CLI 通过 bash 调用的 CLI 子命令

v0.1 Read-only MVP 占位文件。
Implementation starts in later steps (Step 9 完成 Mode A)。

子命令占位（实现于 Step 9）:
  python iics_cli.py list-tasks <folder> [--type DSS|MTT|WORKFLOW|ALL] [--page-size N]
  python iics_cli.py get-task <task_name> <task_type> [--folder PATH] [--no-resolve]
  python iics_cli.py resolve-connection <id> [<id>...]
  python iics_cli.py export <folder> [--type ALL] [--format excel|json] [--filename NAME]

行为约定（实现于 Step 9）:
  - stdout 只输出 JSON；stderr 输出人类可读日志
  - IICS_SESSION_TOKEN 未设置时 stdout 报错并 sys.exit(1)
  - 工具返回 {"error": ...} 时 sys.exit(1)

安全边界:
  - v0.1 只读，不实现任何写 / update / batch 子命令
  - 不得输出、保存或泄露 IICS_SESSION_TOKEN

本文件当前不实现任何 IICS API 调用逻辑。
"""

# TODO(Step 9): argparse 子命令 list-tasks / get-task / resolve-connection / export

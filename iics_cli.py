#!/usr/bin/env python3
"""
iics_cli.py — Mode A 入口：Claude Code CLI 通过 bash 调用的 CLI 子命令

v0.1 Read-only MVP。Step 9 implements 4 个只读子命令，完成 Mode A。

Session token:
  export IICS_SESSION_TOKEN="<USER_SESSION 值>"

子命令:
  python iics_cli.py list-tasks <folder> [--type DSS|MTT|WORKFLOW|ALL] [--page-size N]
  python iics_cli.py get-task <task_name> <task_type> [--folder PATH] [--no-resolve]
  python iics_cli.py resolve-connection <id> [<id>...]
  python iics_cli.py export <folder> [--type ALL] [--details|--summary-only]
                       [--format excel|json] [--filename NAME]

行为约定:
  - stdout 只输出 JSON；stderr 输出人类可读日志
  - IICS_SESSION_TOKEN 未设置 / 工具返回 {"error":...} / 异常 → 退出码 1
  - 成功 → 退出码 0
  - 不打印 / 不保存 / 不泄露 token；不读取 IICS_USER / IICS_PASS；不调用 login API
  - 工具逻辑全部复用 tools/query.py 与 tools/exporter.py，CLI 不重复实现
"""

import argparse
import json
import os
import sys
from pathlib import Path

from iics_client import SessionManager, IICSClient
from tools.query import list_tasks, get_task_detail, resolve_connection
from tools.exporter import export_results, build_task_summary_rows, compact_record
from tools.selected_exporter import validate_selected_tasks, export_selected_tasks


def create_client() -> IICSClient:
    """从环境变量构造只读 client；token 缺失抛 ValueError。不打印/保存 token。"""
    token = os.environ.get("IICS_SESSION_TOKEN", "").strip()
    if not token:
        raise ValueError("IICS_SESSION_TOKEN 未设置")
    session = SessionManager.from_sso_token(token)
    return IICSClient(session)


def print_json(result: dict) -> None:
    """stdout 只输出 JSON。"""
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _log(msg: str) -> None:
    """人类可读日志 → stderr，避免污染 stdout JSON。"""
    print(f"[IICS CLI] {msg}", file=sys.stderr)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="iics_cli.py",
        description="Takeda Japan IICS 运维工具（v0.1 只读）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list-tasks
    p_list = sub.add_parser("list-tasks", help="枚举文件夹下的 task")
    p_list.add_argument("folder")
    p_list.add_argument(
        "--type", default="ALL", choices=["DSS", "MTT", "WORKFLOW", "ALL"]
    )
    p_list.add_argument("--page-size", type=int, default=200)

    # get-task
    p_get = sub.add_parser("get-task", help="获取单个 task 详情")
    p_get.add_argument("name", help="task 名称")
    p_get.add_argument("type", choices=["DSS", "MTT", "WORKFLOW"])
    p_get.add_argument(
        "--folder", default="",
        help="MTT 必需（用于 V3 定位 federated id）；DSS/WORKFLOW 可选",
    )
    p_get.add_argument(
        "--full", action="store_true",
        help="MTT：输出完整 normalized detail（诊断用，只读，不涉及写操作）；"
             "默认输出 Task Summary 同款业务字段（compact records）",
    )
    p_get.add_argument("--no-resolve", action="store_true")

    # resolve-connection
    p_res = sub.add_parser("resolve-connection", help="解析 connection id 为名称")
    p_res.add_argument("ids", nargs="+")

    # export
    p_exp = sub.add_parser("export", help="导出文件夹 task 为 Excel / JSON")
    p_exp.add_argument("folder")
    p_exp.add_argument(
        "--type", default="ALL", choices=["DSS", "MTT", "WORKFLOW", "ALL"]
    )
    p_exp.add_argument("--details", action="store_true", default=True)
    p_exp.add_argument("--summary-only", action="store_true")
    p_exp.add_argument("--format", default="excel", choices=["excel", "json"])
    p_exp.add_argument("--filename", default="")

    # export-selected（只读；只导出输入 list 中指定的 task，按类型分别生成 3 个 Excel）
    p_sel = sub.add_parser(
        "export-selected",
        help="只导出给定 task list（name/type/folder JSON）中指定的 task",
    )
    p_sel.add_argument("--input", required=True, help="selected task JSON 文件路径")
    p_sel.add_argument("--output-dir", default=".", help="Excel 输出目录（不存在则创建）")

    return parser


def _cmd_list_tasks(client, args) -> dict:
    _log(f"running list-tasks {args.folder} type={args.type}")
    return list_tasks(
        client,
        folder_path=args.folder,
        task_type=args.type,
        page_size=args.page_size,
    )


def _cmd_get_task(client, args) -> dict:
    _log(f"running get-task {args.name} {args.type} folder={args.folder}")
    detail = get_task_detail(
        client,
        task_name=args.name,
        task_type=args.type,
        folder_path=args.folder,
        resolve_connections=not args.no_resolve,
    )
    if isinstance(detail, dict) and "error" in detail:
        return detail
    # MTT 默认输出 compact records（与 Excel Task Summary 一致）；--full 输出完整 detail。
    # DSS / WORKFLOW 保持现状（扁平 dict），--full 不改变其行为。
    if args.type == "MTT" and not getattr(args, "full", False):
        rows = build_task_summary_rows(detail, args.folder)
        records = [compact_record(r) for r in rows]
        return {"task_count": 1, "rows": len(records), "records": records}
    return detail


def _cmd_resolve_connection(client, args) -> dict:
    _log(f"running resolve-connection {args.ids}")
    return resolve_connection(client, connection_ids=args.ids)


def _cmd_export(client, args) -> dict:
    _log(f"running export {args.folder} type={args.type} format={args.format}")
    list_result = list_tasks(client, folder_path=args.folder, task_type=args.type)
    if isinstance(list_result, dict) and "error" in list_result:
        return list_result

    tasks = list_result.get("tasks", [])
    details = []
    failed_details = []
    if not args.summary_only:
        for task in tasks:
            name = task.get("name", "")
            ttype = task.get("type", "")
            tid = task.get("id", "")
            _log(f"  fetching detail: {name} ({ttype})")
            if ttype == "MTT":
                # MTT：复用 list_tasks 已返回的 federated id 走 FRS，
                # 不传 name/folder（避免 ConflictingArgumentsError），后续回填
                detail = get_task_detail(
                    client,
                    task_type="MTT",
                    task_id=tid,
                    task_id_type="federated",
                )
                if isinstance(detail, dict) and "error" not in detail:
                    # MTT 归一化 detail 用 task_name/task_location；method B 返回时为空，回填
                    if not detail.get("task_name"):
                        detail["task_name"] = name
                    if not detail.get("task_location"):
                        detail["task_location"] = args.folder
            else:
                # DSS / WORKFLOW：保持现状 by-name
                detail = get_task_detail(
                    client,
                    task_name=name,
                    task_type=ttype,
                    folder_path=args.folder,
                )
            if isinstance(detail, dict) and "error" in detail:
                # 单个 task 详情失败：记录，不中断整批 export
                failed_details.append(
                    {
                        "name": name,
                        "type": ttype,
                        "folder": args.folder,
                        "error_type": detail.get("error_type", ""),
                        "error": detail.get("error", ""),
                    }
                )
                # 保留最小占位行，Task Summary 不丢该 task
                details.append(
                    {
                        "name": name,
                        "type": ttype,
                        "folder": args.folder,
                        "status": task.get("status", ""),
                    }
                )
            else:
                details.append(detail)

    data = {
        "folder": args.folder,
        "tasks": tasks,
        "details": details,
        "failed_details": failed_details,
    }
    return export_results(
        client, data=data, format=args.format, filename=args.filename,
        export_type=args.type,
    )


def _cmd_export_selected(client, args) -> dict:
    _log(f"running export-selected input={args.input} output-dir={args.output_dir}")
    # 输入校验先于任何 IICS 请求；无效输入 → 顶层 fatal error（exit 1）
    try:
        text = Path(args.input).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"error": f"input file not found: {args.input}",
                "error_type": "SelectedTaskInputError"}
    except OSError as e:
        return {"error": f"cannot read input file: {e}",
                "error_type": "SelectedTaskInputError"}
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        return {"error": f"invalid JSON in input file: {e}",
                "error_type": "SelectedTaskInputError"}

    normalized, input_errors = validate_selected_tasks(raw)
    if input_errors:
        return {"error": "Invalid selected task input",
                "error_type": "SelectedTaskInputError",
                "input_errors": input_errors}

    return export_selected_tasks(client, normalized, args.output_dir)


_DISPATCH = {
    "list-tasks": _cmd_list_tasks,
    "get-task": _cmd_get_task,
    "resolve-connection": _cmd_resolve_connection,
    "export": _cmd_export,
    "export-selected": _cmd_export_selected,
}


def main(argv=None) -> int:
    # 保证非 ASCII（中文/符号）JSON 在 GBK/cp936 控制台也能正常输出
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        client = create_client()
        handler = _DISPATCH[args.command]
        result = handler(client, args)
    except Exception as e:  # noqa: BLE001 — 顶层统一结构化错误
        print_json({"error": str(e), "error_type": type(e).__name__})
        return 1

    print_json(result)
    if isinstance(result, dict) and "error" in result:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

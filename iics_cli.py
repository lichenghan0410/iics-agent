#!/usr/bin/env python3
"""
iics_cli.py — Mode A 入口：Claude Code CLI 通过 bash 调用的 CLI 子命令

v0.1 Read-only MVP（Amendment 10：多环境 username/password 自动 Login）。

环境选择（不默认 PROD，未指定即 fail closed）:
  python iics_cli.py --env TEST <subcommand> ...
  或设置环境变量 IICS_ENV=TEST

Credential 配置（由使用者本人手工维护，Agent 不填写）:
  默认 <repo_root>/iics_credentials.json
  可选 override：IICS_CONFIG_FILE=<path>

认证初始化（首次每个环境各做一次，必须人工确认 org 身份）:
  python iics_cli.py auth-check --env TEST     # 只读：展示 org_id / org_uuid / server_host
  python iics_cli.py auth-pin   --env TEST     # 人工确认无误后，才执行绑定

只读子命令:
  python iics_cli.py --env TEST list-tasks <folder> [--type DSS|MTT|WORKFLOW|ALL] [--page-size N]
  python iics_cli.py --env TEST get-task <task_name> <task_type> --folder PATH [--no-resolve]
  python iics_cli.py --env TEST resolve-connection <id> [<id>...]
  python iics_cli.py --env TEST export <folder> [--type ALL] [--details|--summary-only]
                       [--format excel|json] [--filename NAME]
  python iics_cli.py --env TEST export-selected --input <json> [--output-dir DIR]

行为约定:
  - stdout 只输出 JSON；stderr 输出人类可读日志
  - 环境缺失 / credential 缺失 / 未 pin / org mismatch / 工具返回 {"error":...} → 退出码 1
  - 成功 → 退出码 0
  - 不打印 / 不保存 / 不泄露 username / password / icSessionId
  - 业务逻辑全部复用 tools/query.py 与 tools/exporter.py，CLI 不重复实现

Legacy（仅调试，需显式开启，不参与环境 pin 保护）:
  IICS_AUTH_MODE=legacy_token + IICS_SESSION_TOKEN
"""

import argparse
import json
import os
import sys
from pathlib import Path

from auth.credentials import (CredentialConfig, resolve_environment_name,
                              CREDENTIALS_FILENAME, TEMPLATE_FILENAME)
from auth.errors import (CredentialConfigError,
                         DuplicateEnvironmentArgumentError)
from auth.login import perform_login
from auth.pins import EnvironmentPinStore
from iics_client import SessionManager, IICSClient
from tools.query import list_tasks, get_task_detail, resolve_connection
from tools.exporter import (export_results, build_task_summary_rows,
                            build_dss_summary_rows, compact_record, DSS_HEADERS)
from tools.selected_exporter import validate_selected_tasks, export_selected_tasks


LEGACY_MODE_VALUES = {"legacy", "legacy_token", "legacy-token"}


def _legacy_mode_requested() -> bool:
    return os.environ.get("IICS_AUTH_MODE", "").strip().lower() in LEGACY_MODE_VALUES


def _resolve_env_and_credentials(cli_env):
    """--env / IICS_ENV → 规范环境键 + 该环境凭据。不打印任何 credential。"""
    config = CredentialConfig.load()
    environment = resolve_environment_name(
        config, cli_env, os.environ.get("IICS_ENV"))
    credentials = config.get(environment)
    return environment, credentials


def create_client(args) -> IICSClient:
    """environment-aware bootstrap：解析环境 → 读凭据 → Login → pin guard → client。

    不打印 / 不保存 username / password / icSessionId。
    """
    if _legacy_mode_requested():
        token = os.environ.get("IICS_SESSION_TOKEN", "").strip()
        if not token:
            raise CredentialConfigError(
                "IICS_AUTH_MODE=legacy_token 已开启，但 IICS_SESSION_TOKEN 未设置。")
        _log("WARNING: legacy token 模式已开启 —— 不进行环境解析，也不受 environment "
             "pin 保护；请仅用于调试。")
        return IICSClient(SessionManager.from_sso_token(token))

    environment, credentials = _resolve_env_and_credentials(
        getattr(args, "env", None))
    _log(f"environment={environment}")
    session = SessionManager.from_credentials(
        environment, credentials, pin_store=EnvironmentPinStore.default())
    # fail closed：Login 与 environment pin 校验在任何业务请求之前完成
    session.ensure_valid()
    return IICSClient(session)


def print_json(result: dict) -> None:
    """stdout 只输出 JSON。"""
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _log(msg: str) -> None:
    """人类可读日志 → stderr，避免污染 stdout JSON。"""
    print(f"[IICS CLI] {msg}", file=sys.stderr)


def count_env_flags(argv) -> int:
    """统计一条 invocation 中显式出现的 --env 次数（含 --env=VALUE 形式）。"""
    return sum(1 for a in (argv or [])
               if a == "--env" or a.startswith("--env="))


def assert_single_env_flag(argv) -> None:
    """同一条命令只允许指定一次 --env；重复即 fail closed（不做 first/last wins）。

    与 IICS_ENV 无关：CLI 显式 --env 覆盖 IICS_ENV 属于既有优先级设计，不算重复。
    """
    n = count_env_flags(argv)
    if n > 1:
        raise DuplicateEnvironmentArgumentError(
            f"同一条命令中出现了 {n} 次 --env。为避免环境来源歧义，"
            f"每条命令只允许显式指定一次 --env（即使两次取值相同也不允许）。"
            f"请删除多余的 --env 后重试。")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="iics_cli.py",
        description="Takeda Japan IICS 运维工具（v0.1 只读，多环境）",
    )
    parser.add_argument(
        "--env", default="",
        help="目标 IICS 环境（DEV / TEST / PROD 或 credential 配置中定义的 alias）。"
             "未提供时回退到环境变量 IICS_ENV；两者都没有则失败，不默认 PROD。"
             "每条命令只允许出现一次 --env，重复指定会直接失败。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # auth-check（只读：Login + 展示非敏感 org 身份，不写 pin）
    p_auth_check = sub.add_parser(
        "auth-check",
        help="只读认证检查：Login 并展示 org_id / org_uuid / server_host 供人工核对",
    )
    p_auth_check.add_argument("--env", default=argparse.SUPPRESS, help="目标环境（同全局 --env）")

    # auth-pin（人工确认 org 身份无误后，才执行环境绑定）
    p_auth_pin = sub.add_parser(
        "auth-pin",
        help="绑定环境 org 身份（必须在用户人工确认 auth-check 结果之后执行）",
    )
    p_auth_pin.add_argument("--env", default=argparse.SUPPRESS, help="目标环境（同全局 --env）")
    p_auth_pin.add_argument(
        "--force", action="store_true",
        help="重新绑定：覆盖该环境已有的不同 org_uuid。仅供使用者本人显式执行。",
    )

    # list-tasks
    p_list = sub.add_parser("list-tasks", help="枚举文件夹下的 task")
    p_list.add_argument("--env", default=argparse.SUPPRESS, help="目标环境（同全局 --env）")
    p_list.add_argument("folder")
    p_list.add_argument(
        "--type", default="ALL", choices=["DSS", "MTT", "WORKFLOW", "ALL"]
    )
    p_list.add_argument("--page-size", type=int, default=200)

    # get-task
    p_get = sub.add_parser("get-task", help="获取单个 task 详情")
    p_get.add_argument("--env", default=argparse.SUPPRESS, help="目标环境（同全局 --env）")
    p_get.add_argument("name", help="task 名称")
    p_get.add_argument("type", choices=["DSS", "MTT", "WORKFLOW"])
    p_get.add_argument(
        "--folder", default="",
        help="三类型均必需（Amendment 9：用于 V3 folder discovery 取 FRS object ID）。"
             "MTT 的 folder 同时参与 detail 消歧；DSS / WORKFLOW 的 folder 仅用于 FRS "
             "discovery，其 detail 仍按 name 解析、无法按 folder 消歧。缺失 → MissingFolderError",
    )
    p_get.add_argument(
        "--full", action="store_true",
        help="MTT / DSS：输出完整 normalized detail（诊断用，只读，不涉及写操作）；"
             "默认输出对应 schema 的 compact records。WORKFLOW 始终为扁平 detail，"
             "不同 task type 返回结构不同",
    )
    p_get.add_argument("--no-resolve", action="store_true")

    # resolve-connection
    p_res = sub.add_parser("resolve-connection", help="解析 connection id 为名称")
    p_res.add_argument("--env", default=argparse.SUPPRESS, help="目标环境（同全局 --env）")
    p_res.add_argument("ids", nargs="+")

    # export
    p_exp = sub.add_parser("export", help="导出文件夹 task 为 Excel / JSON")
    p_exp.add_argument("--env", default=argparse.SUPPRESS, help="目标环境（同全局 --env）")
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
    p_sel.add_argument("--env", default=argparse.SUPPRESS, help="目标环境（同全局 --env）")
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
    # MTT / DSS 默认输出 compact records（与对应 Excel schema 一致）；--full 输出完整 detail。
    # WORKFLOW 保持现状（扁平 dict）。返回结构因 task_type 而异，脚本调用先判 task_type。
    if not getattr(args, "full", False):
        if args.type == "MTT":
            rows = build_task_summary_rows(detail, args.folder)
            records = [compact_record(r) for r in rows]
            return {"task_count": 1, "rows": len(records), "records": records}
        if args.type == "DSS":
            rows = build_dss_summary_rows(detail, args.folder)
            records = [compact_record(r, DSS_HEADERS) for r in rows]
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
            # Amendment 9：list_tasks 已返回 frs_id；三类型均复用它直传 detail，
            # 不再为 DSS/WORKFLOW 二次 discovery。
            frs_id = task.get("frs_id", "")
            _log(f"  fetching detail: {name} ({ttype})")
            if ttype == "MTT":
                detail = get_task_detail(
                    client,
                    task_type="MTT",
                    task_id=frs_id,
                    task_id_type="frs",
                )
                if isinstance(detail, dict) and "error" not in detail:
                    # MTT 归一化 detail 用 task_name/task_location；直传时为空，回填
                    if not detail.get("task_name"):
                        detail["task_name"] = name
                    if not detail.get("task_location"):
                        detail["task_location"] = args.folder
            else:
                # DSS / WORKFLOW：by-name detail + 直传 frs_id 回填（跳过二次 discovery）
                detail = get_task_detail(
                    client,
                    task_name=name,
                    task_type=ttype,
                    task_id=frs_id,
                    task_id_type="frs",
                )
            if isinstance(detail, dict) and "error" in detail:
                # 单个 task 详情失败：记录，不中断整批 export
                # Amendment 9：三类型 folder 均为合法 FRS-discovery 输入，统一记录 args.folder
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
                        "frs_id": frs_id,
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



# ── 认证 / 环境初始化命令的 stdout contract ──────────────────────────────────
# stdout = machine-readable data；stderr = diagnostics / 人工操作提示。
# 每个 status 有明确 allowlist；输出严格按 allowlist 投影，多余字段一律不进 stdout。
AUTH_CHECK_STDOUT_KEYS = {
    # 恰好 5 个字段，不多不少
    "pin_required": ("environment", "status", "org_id", "org_uuid", "server_host"),
    "pinned": ("environment", "status", "org_id", "org_uuid", "server_host",
               "pinned_at"),
    "pin_mismatch": ("environment", "status", "org_id", "org_uuid", "server_host",
                     "pinned_org_uuid", "error_type", "error"),
}

AUTH_PIN_STDOUT_KEYS = {
    "pinned": ("environment", "status", "org_id", "org_uuid", "server_host",
               "pinned_at", "pin_file"),
    "already_pinned": ("environment", "status", "org_id", "org_uuid", "server_host",
                       "pinned_at", "pin_file"),
}

# 任何 status 下都禁止出现在 stdout 的 key（防止 Login 原始响应被透传）
FORBIDDEN_STDOUT_KEYS = frozenset({
    "username", "password",
    "icSessionId", "session_id", "sessionId", "sso_token", "token",
    "id", "user_id", "userId",
    "roles", "usergroups", "userGroups", "emails",
    "securityQuestion", "securityAnswer",
    "raw", "response", "login_response", "headers", "Authorization",
})


def _project_auth_stdout(result: dict, allowlists: dict, command: str) -> dict:
    """按 status allowlist 投影 stdout；未知 status 或出现禁止字段 → fail closed。"""
    status = result.get("status", "")
    allowed = allowlists.get(status)
    if allowed is None:
        raise ValueError(
            f"{command}: 未定义的 stdout status {status!r}，拒绝输出未经审查的字段。")
    leaked = sorted(FORBIDDEN_STDOUT_KEYS & set(result))
    if leaked:
        # 只报 key 名，绝不回显取值
        raise ValueError(
            f"{command}: stdout 构造中出现禁止字段 {leaked}，已阻断输出。")
    return {k: result.get(k, "") for k in allowed}


# ── 认证 / 环境初始化子命令（不经 create_client，不受 pin guard 阻断）─────────
def _cmd_auth_check(args) -> dict:
    """只读：Login 并返回非敏感身份信息，供使用者人工核对。绝不写 pin。

    stdout 严格遵循 AUTH_CHECK_STDOUT_KEYS；人工操作提示走 stderr。
    """
    environment, credentials = _resolve_env_and_credentials(getattr(args, "env", None))
    _log(f"auth-check environment={environment}")
    identity = perform_login(credentials)
    # 只使用最小化身份对象，不接触 Login 原始响应
    public = identity.public_identity()

    store = EnvironmentPinStore.default()
    pin = store.get(environment)
    result = {"environment": environment, **public}

    if pin is None:
        result["status"] = "pin_required"
        # 人工提示属于 diagnostics，只走 stderr，不进 stdout JSON contract
        _log(f"环境 {environment} 尚未绑定：请人工核对上面输出的 org_id / org_uuid "
             f"是否确实属于 {environment} 环境；确认无误后再执行 "
             f"`python iics_cli.py auth-pin --env {environment}`。")
    elif pin.get("org_uuid") != identity.org_uuid:
        result["status"] = "pin_mismatch"
        result["pinned_org_uuid"] = pin.get("org_uuid", "")
        result["error_type"] = "EnvironmentMismatchError"
        result["error"] = (
            f"环境 {environment} 已绑定 org_uuid={pin.get('org_uuid')}，"
            f"与本次 Login 的 org_uuid={identity.org_uuid} 不一致。业务调用已被阻断。")
        _log(f"环境 {environment} 身份不匹配：极可能是 credential 配置里该环境填错了账号。"
             f"请先核对配置，不要直接执行 auth-pin --force。")
    else:
        result["status"] = "pinned"
        result["pinned_at"] = pin.get("pinned_at", "")

    return _project_auth_stdout(result, AUTH_CHECK_STDOUT_KEYS, "auth-check")


def _cmd_auth_pin(args) -> dict:
    """在使用者已人工确认 org 身份之后执行环境绑定。默认不覆盖已有 pin。

    stdout 严格遵循 AUTH_PIN_STDOUT_KEYS。org_uuid 不一致且未 --force 时，
    EnvironmentMismatchError 由 main() 的顶层处理器转为 {"error", "error_type"}。
    """
    environment, credentials = _resolve_env_and_credentials(getattr(args, "env", None))
    _log(f"auth-pin environment={environment} force={bool(getattr(args, 'force', False))}")
    identity = perform_login(credentials)
    store = EnvironmentPinStore.default()
    existing = store.get(environment)

    record = store.set(
        environment,
        org_uuid=identity.org_uuid,
        org_id=identity.org_id,
        force=bool(getattr(args, "force", False)),
    )
    already = (existing is not None
               and existing.get("org_uuid") == identity.org_uuid)
    result = {
        "environment": environment,
        "status": "already_pinned" if already else "pinned",
        "org_id": record.get("org_id", ""),
        "org_uuid": record.get("org_uuid", ""),
        "server_host": identity.server_host,
        "pinned_at": record.get("pinned_at", ""),
        "pin_file": str(store.path),
    }
    return _project_auth_stdout(result, AUTH_PIN_STDOUT_KEYS, "auth-pin")


_AUTH_DISPATCH = {
    "auth-check": _cmd_auth_check,
    "auth-pin": _cmd_auth_pin,
}


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

    if argv is None:
        argv = sys.argv[1:]

    # 环境选择歧义防护：必须先于 parse_args，否则 argparse 会静默用后一个值
    try:
        assert_single_env_flag(argv)
    except DuplicateEnvironmentArgumentError as e:
        _log(str(e))
        print_json({"error": str(e), "error_type": type(e).__name__})
        return 1

    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command in _AUTH_DISPATCH:
            result = _AUTH_DISPATCH[args.command](args)
        else:
            client = create_client(args)
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

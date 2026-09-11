#!/usr/bin/env python3
"""
smoke_test.py — v0.1 只读冒烟测试（Mode A 完成标准）

不启动 Cowork Agent，直接测试客户端与工具函数。

运行（Amendment 10 主路径：username/password 自动 Login）:
  IICS_ENV=TEST python smoke_test.py
  前提：<repo_root>/iics_credentials.json 已由使用者填好该环境账号，且该环境已完成
        auth-check → 人工确认 → auth-pin。

Legacy（仅调试）:
  IICS_AUTH_MODE=legacy_token IICS_SESSION_TOKEN="<USER_SESSION值>" python smoke_test.py

可选环境变量覆盖:
  IICS_TEST_FOLDER     默认 JAPAN_SCRM_DI
  IICS_TEST_TASK_TYPE  默认 DSS
  IICS_TEST_TASK_NAME  默认空（空则取 list_tasks 第一个结果）

不打印 / 不保存 username / password / session。Test 4/5 在目标文件夹无对应 task 时允许 SKIP。
"""

import os
import sys

from auth.credentials import CredentialConfig, resolve_environment_name
from auth.pins import EnvironmentPinStore
from iics_client import SessionManager, IICSClient, build_v2_url, build_v3_url
from tools.query import list_tasks, get_task_detail, resolve_connection
from tools.exporter import export_results


def main() -> int:
    # 避免在 GBK/cp936 控制台打印 ✅ 等字符时 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    legacy = os.environ.get("IICS_AUTH_MODE", "").strip().lower() in (
        "legacy", "legacy_token", "legacy-token")

    if legacy:
        token = os.environ.get("IICS_SESSION_TOKEN", "").strip()
        if not token:
            print("ERROR: legacy 模式下 IICS_SESSION_TOKEN 未设置。", file=sys.stderr)
            return 1
        session = SessionManager.from_sso_token(token)
        env_label = "legacy-token"
    else:
        cli_env = os.environ.get("IICS_ENV", "").strip()
        if not cli_env:
            print("ERROR: 未设置 IICS_ENV（例如 IICS_ENV=TEST）。本工具不默认 PROD。",
                  file=sys.stderr)
            return 1
        config = CredentialConfig.load()
        environment = resolve_environment_name(config, None, cli_env)
        session = SessionManager.from_credentials(
            environment, config.get(environment),
            pin_store=EnvironmentPinStore.default())
        session.ensure_valid()   # Login + environment pin guard
        env_label = environment

    TEST_FOLDER = os.environ.get("IICS_TEST_FOLDER", "JAPAN_SCRM_DI")
    TEST_TYPE = os.environ.get("IICS_TEST_TASK_TYPE", "DSS")
    TEST_TASK = os.environ.get("IICS_TEST_TASK_NAME", "")

    # ── Test 1: SessionManager + URL factory ─────────────────────────────
    assert session.server_url.startswith("https://"), session.server_url
    assert (
        build_v2_url("https://use4.dm-us.informaticacloud.com", "dsstask/name/X")
        == "https://use4.dm-us.informaticacloud.com/saas/api/v2/dsstask/name/X"
    )
    assert (
        build_v3_url("https://use4.dm-us.informaticacloud.com", "public/core/v3/objects")
        == "https://use4.dm-us.informaticacloud.com/saas/public/core/v3/objects"
    )
    assert (
        build_v2_url("https://use4.dm-us.informaticacloud.com/saas", "connection/123")
        == "https://use4.dm-us.informaticacloud.com/saas/api/v2/connection/123"
    )
    print(f"✅ Test 1 passed: SessionManager + URL factory (env={env_label})")

    # ── Test 1b: 安全回归 —— 业务 client 仍然 GET-only ───────────────────
    client = IICSClient(session)
    try:
        client._request("POST", "connection/1")
    except ValueError as e:
        assert "only supports GET" in str(e), e
        print("✅ Test 1b passed: IICSClient 仍为 GET-only（POST 被拒绝）")
    else:
        raise AssertionError("SAFETY REGRESSION: _request 接受了 POST")

    # ── Test 2: list_tasks — all types ───────────────────────────────────
    result = list_tasks(client, folder_path=TEST_FOLDER)
    assert "tasks" in result, f"list_tasks returned error: {result}"
    assert result["count"] >= 0
    print(f"✅ Test 2 passed: list_tasks({TEST_FOLDER}) → {result['count']} tasks")

    # ── Test 3: list_tasks — filtered by type ────────────────────────────
    result_typed = list_tasks(client, folder_path=TEST_FOLDER, task_type=TEST_TYPE)
    assert "tasks" in result_typed, f"list_tasks(type) error: {result_typed}"
    print(f"✅ Test 3 passed: list_tasks type={TEST_TYPE} → {result_typed['count']} tasks")

    # ── Test 4 & 5: 依赖有效 task 存在，count=0 时跳过 ───────────────────
    if result_typed["count"] == 0:
        print(f"⏭️  Test 4 & 5 SKIPPED: {TEST_FOLDER} has no {TEST_TYPE} task")
    else:
        task_name = TEST_TASK or result_typed["tasks"][0]["name"]

        # Amendment 9：三类型均需 folder（V3 folder discovery 取 frs_id）。
        # MTT 的 folder 同时参与 detail 消歧；DSS/WORKFLOW 的 folder 仅用于 FRS discovery。
        detail = get_task_detail(
            client, task_name=task_name, task_type=TEST_TYPE,
            folder_path=TEST_FOLDER,
        )
        assert "error" not in detail, f"get_task_detail error: {detail}"
        assert "runtime_environment" in detail
        # Amendment 9：三类型均暴露 frs_id
        assert detail.get("frs_id"), detail
        if TEST_TYPE == "MTT":
            assert detail["detail_route"] == "v2-mttask-frs", detail
            assert "id_type" not in detail, detail
            assert detail["task_mapping"]["location"] is None, detail
            assert "transformations" in detail and "raw" in detail
        elif TEST_TYPE == "DSS":
            assert "source_connection" in detail
        else:
            assert "raw" in detail
        print(f"✅ Test 4 passed: get_task_detail({task_name}) type={TEST_TYPE}")

        conn_id = (detail.get("source_connection") or {}).get("id")
        if TEST_TYPE == "DSS" and conn_id:
            r1 = resolve_connection(client, connection_ids=[conn_id])
            r2 = resolve_connection(client, connection_ids=[conn_id])  # 应命中缓存
            assert r1["resolved"] == r2["resolved"]
            print("✅ Test 5 passed: resolve_connection + cache hit")
        else:
            print(f"⏭️  Test 5 SKIPPED: no stable source_connection for {TEST_TYPE}")

    # ── Test 6: export_results ───────────────────────────────────────────
    export_result = export_results(client, data=result_typed, format="excel")
    assert "error" not in export_result, f"export_results error: {export_result}"
    assert os.path.exists(export_result["file_path"])
    assert export_result["file_path"].endswith(".xlsx")
    print(f"✅ Test 6 passed: export_results → {export_result['file_path']}")

    print("\n✅ All smoke tests passed — v0.1 完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

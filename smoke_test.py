#!/usr/bin/env python3
"""
smoke_test.py — v0.1 只读冒烟测试（Mode A 完成标准）

不启动 Cowork Agent，直接测试客户端与工具函数。

运行:
  IICS_SESSION_TOKEN="<USER_SESSION值>" python smoke_test.py
可选环境变量覆盖:
  IICS_TEST_FOLDER     默认 JAPAN_SCRM_DI
  IICS_TEST_TASK_TYPE  默认 DSS
  IICS_TEST_TASK_NAME  默认空（空则取 list_tasks 第一个结果）

不打印 / 不保存 token。Test 4/5 在目标文件夹无对应 task 时允许 SKIP。
"""

import os
import sys

from iics_client import SessionManager, IICSClient, build_v2_url, build_v3_url
from tools.query import list_tasks, get_task_detail, resolve_connection
from tools.exporter import export_results


def main() -> int:
    token = os.environ.get("IICS_SESSION_TOKEN", "").strip()
    if not token:
        print("ERROR: IICS_SESSION_TOKEN 未设置，无法运行冒烟测试。", file=sys.stderr)
        print("请先从浏览器获取 USER_SESSION 并 export IICS_SESSION_TOKEN。", file=sys.stderr)
        return 1

    TEST_FOLDER = os.environ.get("IICS_TEST_FOLDER", "JAPAN_SCRM_DI")
    TEST_TYPE = os.environ.get("IICS_TEST_TASK_TYPE", "DSS")
    TEST_TASK = os.environ.get("IICS_TEST_TASK_NAME", "")

    # ── Test 1: SessionManager + URL factory ─────────────────────────────
    session = SessionManager.from_sso_token(token)
    assert session.server_url == "https://use4.dm-us.informaticacloud.com"
    assert (
        build_v2_url(session.server_url, "dsstask/name/X")
        == "https://use4.dm-us.informaticacloud.com/saas/api/v2/dsstask/name/X"
    )
    assert (
        build_v3_url(session.server_url, "api/v3/objects")
        == "https://use4.dm-us.informaticacloud.com/api/v3/objects"
    )
    assert (
        build_v2_url("https://use4.dm-us.informaticacloud.com/saas", "connection/123")
        == "https://use4.dm-us.informaticacloud.com/saas/api/v2/connection/123"
    )
    print("✅ Test 1 passed: SessionManager + URL factory")

    # ── Test 2: list_tasks — all types ───────────────────────────────────
    client = IICSClient(session)
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

        detail = get_task_detail(
            client, task_name=task_name, task_type=TEST_TYPE, folder_path=TEST_FOLDER,
        )
        assert "error" not in detail, f"get_task_detail error: {detail}"
        assert "runtime_agent" in detail
        if TEST_TYPE == "DSS":
            assert "source_connection" in detail
        else:
            assert "raw" in detail
        print(f"✅ Test 4 passed: get_task_detail({task_name})")

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

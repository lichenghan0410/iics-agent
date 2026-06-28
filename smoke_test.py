"""
smoke_test.py — v0.1 只读冒烟测试

v0.1 Read-only MVP 占位文件。
Implementation starts in later steps (Step 9 写完整 smoke test)。

测试覆盖（实现于 Step 9）:
  Test 1: SessionManager + URL factory
  Test 2/3: list_tasks（全部 / 按类型）
  Test 4: get_task_detail
  Test 5: resolve_connection + cache hit
  Test 6: export_results

运行（实现后）:
  IICS_SESSION_TOKEN="<USER_SESSION值>" python smoke_test.py

安全边界:
  - 仅需要 IICS_SESSION_TOKEN；不要求 IICS_LOGIN_URL / IICS_USER / IICS_PASS
  - 不得输出、保存或泄露 IICS_SESSION_TOKEN

本文件当前不实现任何 IICS API 调用逻辑。
"""

# TODO(Step 9): 实现 Test 1–6

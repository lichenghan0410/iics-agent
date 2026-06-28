"""
iics_client.py — 两种模式共享层：SessionManager + URL factory + IICSClient

v0.1 Read-only MVP 占位文件。
Implementation starts in later steps:
  - Step 2: SessionManager.from_sso_token() / headers_v2() / headers_v3() / handle_401()
  - Step 3: build_v2_url() / build_v3_url()
  - Step 4: IICSClient._request()（含 401 重试）

安全边界（v0.1）:
  - 只读模式，禁止任何写路径（POST/PUT/PATCH/DELETE 业务调用）
  - 不得输出、保存或泄露 IICS_SESSION_TOKEN
  - from_env() 仅搭框架，v0.1 不调用、不测试

本文件当前不实现任何 IICS API 调用逻辑。
"""

# TODO(Step 3): def build_v2_url(server_url: str, path: str) -> str
# TODO(Step 3): def build_v3_url(server_url: str, path: str) -> str
# TODO(Step 2): class SessionManager (from_sso_token / headers_v2 / headers_v3 / handle_401)
# TODO(Step 2): class SessionManager.from_env  # 搭框架，v0.1 不调用
# TODO(Step 4): class IICSClient (._request with 401 retry)

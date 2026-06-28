"""
iics_client.py — 两种模式共享层：SessionManager + URL factory + IICSClient

v0.1 Read-only MVP。
Step 2 only implements SessionManager (from_sso_token / from_env /
server_url / headers_v2 / headers_v3 / handle_401)。
Step 3 implements URL factory (build_v2_url / build_v3_url)。
Step 4 implements IICSClient._request()（GET-only，V2/V3 URL + Header 注入，
401 调用 handle_401 并最多重试一次）。

安全边界（v0.1）:
  - 只读模式，禁止任何写路径（POST/PUT/PATCH/DELETE 业务调用）
  - 不得输出、保存或泄露 IICS_SESSION_TOKEN
  - from_env() 仅搭框架，v0.1 不调用 login API、不读取 IICS_USER / IICS_PASS
"""

import os
from dataclasses import dataclass
from typing import Optional

import requests

# ── 模块级常量 ──────────────────────────────────────────────────────────────
LOGIN_URL = os.environ.get(
    "IICS_LOGIN_URL",
    "https://dm-us.informaticacloud.com/ma/api/v2/user/login",
)

DEFAULT_SERVER_URL = "https://use4.dm-us.informaticacloud.com"


# ── URL 工厂（模块级函数）────────────────────────────────────────────────────
# 调用约定:
#   - V2 path 传业务资源路径，如 "dsstask/name/X" / "connection/123"（不含 saas/api/v2 前缀）
#   - V3 path 传 "api/v3/objects" 等
#   - 查询参数（?q=...）不在此处拼接，由 Step 4 _request(..., params={...}) 传入
def build_v2_url(server_url: str, path: str) -> str:
    """构建 V2 URL，集中处理 serverUrl 是否已含 /saas 后缀，防止 /saas/saas/ 重复。"""
    if not server_url:
        raise ValueError("server_url is required")
    if not path:
        raise ValueError("path is required")

    base = server_url.rstrip("/")
    clean_path = path.lstrip("/")

    if base.endswith("/saas"):
        return f"{base}/api/v2/{clean_path}"

    return f"{base}/saas/api/v2/{clean_path}"


def build_v3_url(server_url: str, path: str) -> str:
    """构建 V3 URL，serverUrl 含 /saas 后缀时去除，再拼接 V3 path。"""
    if not server_url:
        raise ValueError("server_url is required")
    if not path:
        raise ValueError("path is required")

    base = server_url.rstrip("/")
    clean_path = path.lstrip("/")

    if base.endswith("/saas"):
        base = base[:-5]

    return f"{base}/{clean_path}"


# ── SessionManager ───────────────────────────────────────────────────────────
@dataclass
class SessionManager:
    """认证会话管理。v0.1 只启用 SSO Cookie 模式（from_sso_token）。"""

    sso_token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    _server_url: str = DEFAULT_SERVER_URL

    # ── 只读属性 ──────────────────────────────────────────────────────────
    @property
    def server_url(self) -> str:
        return self._server_url

    # ── 构造器（v0.1 只用 from_sso_token）────────────────────────────────
    @classmethod
    def from_sso_token(cls, token: str) -> "SessionManager":
        """从浏览器手动提取的 USER_SESSION Cookie 构造会话。

        token 为空或全空白时抛 ValueError；不打印 / 不记录 token 值。
        """
        if token is None or not token.strip():
            raise ValueError("SSO token is required")
        return cls(sso_token=token.strip())

    @classmethod
    def from_env(cls) -> "SessionManager":
        """v0.1 仅搭框架：从环境变量 IICS_SESSION_TOKEN 读取 SSO token。

        不调用 login API，不读取 IICS_USER / IICS_PASS。
        """
        token = os.environ.get("IICS_SESSION_TOKEN")
        if not token:
            raise ValueError("IICS_SESSION_TOKEN is required for v0.1")
        return cls.from_sso_token(token)

    # ── Header 注入 ──────────────────────────────────────────────────────
    def headers_v2(self) -> dict:
        if not self.sso_token:
            raise ValueError("SSO token is required")
        return {
            "icSessionId": self.sso_token,
            "Content-Type": "application/json",
        }

    def headers_v3(self) -> dict:
        if not self.sso_token:
            raise ValueError("SSO token is required")
        return {
            "INFA-SESSION-ID": self.sso_token,
            "Content-Type": "application/json",
        }

    # ── 401 处理 ─────────────────────────────────────────────────────────
    def handle_401(self) -> None:
        """v0.1 不自动刷新 token，直接抛出提示用户重新获取的异常。"""
        raise RuntimeError(
            "IICS session expired or unauthorized. Refresh USER_SESSION "
            "from browser and set IICS_SESSION_TOKEN again."
        )


# ── IICSClient ───────────────────────────────────────────────────────────────
class IICSClient:
    """共享 HTTP 客户端。v0.1 只读：_request 仅支持 GET。"""

    session: SessionManager

    def __init__(self, session: SessionManager):
        self.session = session

    def _request(
        self,
        method: str,
        path: str,
        use_v3: bool = False,
        **kwargs,
    ) -> requests.Response:
        """发出 IICS GET 请求。

        - 按 use_v3 选择 URL 工厂与 Header（V3: INFA-SESSION-ID / V2: icSessionId）
        - 查询参数须经 kwargs 的 params 传入，禁止在 path 内拼 query string
        - 401 时调用 session.handle_401()，最多重试一次；非 401 错误 raise_for_status()
        - 成功返回 requests.Response，调用方负责 .json()
        """
        method = method.upper()
        if method != "GET":
            raise ValueError("v0.1 only supports GET requests")
        if not path:
            raise ValueError("path is required")

        if use_v3:
            url = build_v3_url(self.session.server_url, path)
            default_headers = self.session.headers_v3()
        else:
            url = build_v2_url(self.session.server_url, path)
            default_headers = self.session.headers_v2()

        # 调用方 headers 覆盖默认 headers
        caller_headers = kwargs.pop("headers", None)
        headers = dict(default_headers)
        if caller_headers:
            headers.update(caller_headers)

        # 最多两次 attempt：attempt 0 → 401 → handle_401() → attempt 1
        response = requests.request(method, url, headers=headers, **kwargs)
        if response.status_code == 401:
            self.session.handle_401()  # v0.1 直接抛 RuntimeError，不会到达下一行
            response = requests.request(method, url, headers=headers, **kwargs)

        response.raise_for_status()
        return response

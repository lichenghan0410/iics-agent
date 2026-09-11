"""
auth/login.py — IICS V2 Login（认证层唯一允许的 POST）

Amendment 10 前提（Postman 实测已确认，本轮不再重新研究）：
  POST /ma/api/v2/user/login (username/password)
      → serverUrl + icSessionId
      → 同一 icSessionId：V2 用 icSessionId header，V3 用 INFA-SESSION-ID header

安全边界：
  - 本模块的 POST 只用于认证，绝不等于给业务 IICSClient 增加写能力
  - Login 原始响应体不得整体保留 / 打印 / 落盘；只提取允许字段（§13.1）
  - 异常 message 只描述结构性问题，不含任何字段取值与原始 body
"""

import sys
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import requests

from .credentials import EnvironmentCredentials
from .errors import InvalidCredentialsError, InvalidLoginResponseError

# Login 自身的 transient retry（与业务 GET 的 transient counter 相互独立）
LOGIN_TIMEOUT = (10, 60)
LOGIN_TRANSIENT_MAX_ATTEMPTS = 3
LOGIN_TRANSIENT_BACKOFF = (1, 2)
LOGIN_TRANSIENT_STATUS = frozenset({429, 502, 503, 504})

# §13.1 允许保留的 Login 响应字段（其余读取后即丢弃）
ALLOWED_LOGIN_FIELDS = ("icSessionId", "serverUrl", "orgId", "orgUuid", "id")


@dataclass(frozen=True)
class LoginIdentity:
    """Login 成功后的最小内部对象。绝不持有原始 response / 完整 dict。"""

    session_id: str = field(repr=False)
    server_url: str = ""
    org_id: str = ""
    org_uuid: str = ""
    user_id: str = field(default="", repr=False)

    def __repr__(self) -> str:
        return (f"LoginIdentity(server_url={self.server_url!r}, "
                f"org_id={self.org_id!r}, org_uuid={self.org_uuid!r}, "
                f"session_id=<redacted>)")

    __str__ = __repr__

    @property
    def server_host(self) -> str:
        return urlparse(self.server_url).netloc or ""

    def public_identity(self) -> dict:
        """auth-check 允许展示的非敏感身份信息（不含 id / session / credential）。"""
        return {
            "org_id": self.org_id,
            "org_uuid": self.org_uuid,
            "server_host": self.server_host,
        }


def _log(msg: str) -> None:
    print(f"[IICS auth] {msg}", file=sys.stderr)


def _extract_identity(response: requests.Response, environment: str) -> LoginIdentity:
    """从 Login 响应中只提取允许字段，构造最小对象；其余字段读取后立即丢弃。"""
    content_type = (response.headers.get("Content-Type") or "").lower()
    if content_type and "json" not in content_type:
        raise InvalidLoginResponseError(
            f"环境 {environment} 的 Login 响应 Content-Type 非 application/json "
            f"（收到 {content_type.split(';')[0]!r}），可能被重定向到登录页。")

    try:
        payload = response.json()
    except ValueError:
        raise InvalidLoginResponseError(
            f"环境 {environment} 的 Login 响应不是合法 JSON。") from None

    if not isinstance(payload, dict):
        raise InvalidLoginResponseError(
            f"环境 {environment} 的 Login 响应顶层不是 JSON 对象。")

    # 只取允许字段，随后丢弃 payload（不挂载到任何长期对象上）
    picked = {k: payload.get(k) for k in ALLOWED_LOGIN_FIELDS}
    del payload

    def _as_str(key: str) -> str:
        v = picked.get(key)
        return v.strip() if isinstance(v, str) else ""

    session_id = _as_str("icSessionId")
    server_url = _as_str("serverUrl")
    org_id = _as_str("orgId")
    org_uuid = _as_str("orgUuid")
    user_id = _as_str("id")
    del picked

    for name, value in (("icSessionId", session_id), ("serverUrl", server_url),
                        ("orgUuid", org_uuid), ("orgId", org_id)):
        if not value:
            raise InvalidLoginResponseError(
                f"环境 {environment} 的 Login 响应缺少字段 {name}。")

    return LoginIdentity(
        session_id=session_id,
        server_url=server_url,
        org_id=org_id,
        org_uuid=org_uuid,
        user_id=user_id,
    )


def perform_login(
    credentials: EnvironmentCredentials,
    timeout=LOGIN_TIMEOUT,
) -> LoginIdentity:
    """执行 Login POST，返回 LoginIdentity。

    - 401 / 403 → InvalidCredentialsError（不重复 retry，避免账号锁定）
    - 其它 4xx → InvalidLoginResponseError（不 retry）
    - Timeout / ConnectionError / 429 / 502 / 503 / 504 → 有限 transient retry
    - 200 但非 JSON / HTML / 缺字段 → InvalidLoginResponseError
    """
    env = credentials.environment
    body = {"username": credentials.username, "password": credentials.password}
    headers = {"Content-Type": "application/json", "Accept": "application/json"}

    attempt = 0
    while True:
        attempt += 1
        try:
            response = requests.post(
                credentials.login_url, json=body, headers=headers, timeout=timeout
            )
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError) as e:
            if attempt < LOGIN_TRANSIENT_MAX_ATTEMPTS:
                _sleep_backoff(attempt, type(e).__name__)
                continue
            raise

        status = response.status_code

        if status in (401, 403):
            # 凭据本身的问题：绝不自动重复尝试
            raise InvalidCredentialsError(
                f"环境 {env} 的 Login 被拒绝（HTTP {status}）。"
                f"请检查该环境在 credential 配置中的账号是否正确 / 是否被锁定。")

        if status in LOGIN_TRANSIENT_STATUS and attempt < LOGIN_TRANSIENT_MAX_ATTEMPTS:
            _sleep_backoff(attempt, f"HTTP {status}")
            continue

        if status >= 400:
            raise InvalidLoginResponseError(
                f"环境 {env} 的 Login 失败（HTTP {status}）。")

        identity = _extract_identity(response, env)
        # 显式切断对 response 的引用，避免原始 body 被长期持有
        del response
        return identity


def _sleep_backoff(attempt: int, reason: str) -> None:
    idx = min(attempt - 1, len(LOGIN_TRANSIENT_BACKOFF) - 1)
    delay = LOGIN_TRANSIENT_BACKOFF[idx]
    _log(f"login attempt {attempt}/{LOGIN_TRANSIENT_MAX_ATTEMPTS} "
         f"failed ({reason}); retrying in {delay}s")
    time.sleep(delay)

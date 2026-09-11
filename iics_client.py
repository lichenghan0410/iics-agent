"""
iics_client.py — 两种模式共享层：SessionManager + URL factory + IICSClient

v0.1 Read-only MVP（Amendment 10：多环境 username/password 自动 Login）。

安全边界（v0.1，未变）:
  - 业务只读：IICSClient._request 仍然严格 GET-only
  - Login POST 只存在于认证层（auth/login.py），不给业务 client 增加写能力
  - 不得输出、保存或泄露 username / password / icSessionId
  - session 只存在于进程内存，不落盘（无 session cache 文件）

认证链（Amendment 10）:
  CLI --env / IICS_ENV
      → auth.credentials.CredentialConfig
      → SessionManager.from_credentials()
      → SessionManager.login()  (auth.login.perform_login)
      → environment pin guard (auth.pins.EnvironmentPinStore)
      → IICSClient（业务 GET）

Legacy（可选，仅调试）:
  IICS_AUTH_MODE=legacy_token + IICS_SESSION_TOKEN → SessionManager.from_sso_token()
  legacy 模式不具备 refresh 能力，也不参与环境 pin 保护。
"""

import os
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

from auth.credentials import EnvironmentCredentials
from auth.errors import AuthenticationError, PermissionDeniedError
from auth.login import LoginIdentity, perform_login
from auth.pins import EnvironmentPinStore
from auth.errors import EnvironmentPinRequiredError
from cache.connection_cache import ConnectionCache, ObjectCache

# ── 模块级常量 ──────────────────────────────────────────────────────────────
DEFAULT_SERVER_URL = "https://use4.dm-us.informaticacloud.com"

# 网络容错（v0.1.1）：(connect, read) 秒
DEFAULT_TIMEOUT = (10, 60)
# 瞬时错误重试：最多 3 次总尝试（即 2 次重试）；backoff 1s / 2s
TRANSIENT_MAX_ATTEMPTS = 3
TRANSIENT_BACKOFF = (1, 2)
TRANSIENT_STATUS = frozenset({429, 502, 503, 504})

# 认证 refresh 与 transient retry 使用独立计数器（Amendment 10 §19）
AUTH_REFRESH_MAX = 1
# idle 主动 refresh 阈值（optimization；401 才是权威判定）
IDLE_REFRESH_SECONDS = 25 * 60


# ── URL 工厂（模块级函数）────────────────────────────────────────────────────
# 调用约定:
#   - V2 path 传业务资源路径，如 "dsstask/name/X" / "connection/123"（不含 saas/api/v2 前缀）
#   - V3 path 传 "public/core/v3/objects" 等（V3 public API 同样位于 /saas 下）
#   - 查询参数（?q=...）不在此处拼接，由 _request(..., params={...}) 传入
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
    """构建 V3 URL。实测：IICS V3 public API 与 V2 一样位于 /saas 下
    （正确端点 /saas/public/core/v3/objects），故确保 /saas 前缀、不重复。
    """
    if not server_url:
        raise ValueError("server_url is required")
    if not path:
        raise ValueError("path is required")

    base = server_url.rstrip("/")
    clean_path = path.lstrip("/")

    if base.endswith("/saas"):
        return f"{base}/{clean_path}"

    return f"{base}/saas/{clean_path}"


# ── SessionManager ───────────────────────────────────────────────────────────
@dataclass
class SessionManager:
    """认证会话管理（进程内，不落盘）。

    主路径：environment + credentials → login() → session_id / server_url / org 身份
    Legacy：from_sso_token()，仅调试，不具备 refresh 能力。
    """

    environment: Optional[str] = None
    credentials: Optional[EnvironmentCredentials] = field(default=None, repr=False)
    pin_store: Optional[EnvironmentPinStore] = field(default=None, repr=False)
    require_pin: bool = True
    legacy_token_mode: bool = False

    session_id: Optional[str] = field(default=None, repr=False)
    _server_url: str = DEFAULT_SERVER_URL
    org_id: Optional[str] = None
    org_uuid: Optional[str] = None
    authenticated_at: Optional[float] = None
    last_activity_at: Optional[float] = None

    # ── 只读属性 ──────────────────────────────────────────────────────────
    @property
    def server_url(self) -> str:
        return self._server_url

    @property
    def sso_token(self) -> Optional[str]:
        """向后兼容别名：内部语义已改为 session_id。"""
        return self.session_id

    @sso_token.setter
    def sso_token(self, value: Optional[str]) -> None:
        self.session_id = value

    def __repr__(self) -> str:  # 防止意外泄露 session_id
        return (f"SessionManager(environment={self.environment!r}, "
                f"server_url={self._server_url!r}, org_id={self.org_id!r}, "
                f"org_uuid={self.org_uuid!r}, "
                f"authenticated={'yes' if self.session_id else 'no'}, "
                f"legacy={self.legacy_token_mode})")

    __str__ = __repr__

    # ── 构造器 ───────────────────────────────────────────────────────────
    @classmethod
    def from_credentials(
        cls,
        environment: str,
        credentials: EnvironmentCredentials,
        pin_store: Optional[EnvironmentPinStore] = None,
        require_pin: bool = True,
    ) -> "SessionManager":
        """主路径：环境感知的 username/password 会话。不在此处发起 Login。"""
        if not environment:
            raise ValueError("environment is required")
        if credentials is None:
            raise ValueError("credentials are required")
        return cls(
            environment=environment,
            credentials=credentials,
            pin_store=pin_store or EnvironmentPinStore.default(),
            require_pin=require_pin,
        )

    @classmethod
    def from_sso_token(cls, token: str) -> "SessionManager":
        """Legacy：从浏览器手动提取的 USER_SESSION 构造会话（无 refresh 能力）。

        token 为空或全空白时抛 ValueError；不打印 / 不记录 token 值。
        """
        if token is None or not token.strip():
            raise ValueError("SSO token is required")
        return cls(session_id=token.strip(), legacy_token_mode=True,
                   require_pin=False)

    @classmethod
    def from_env(cls) -> "SessionManager":
        """Legacy：从环境变量 IICS_SESSION_TOKEN 读取 session 值。"""
        token = os.environ.get("IICS_SESSION_TOKEN")
        if not token:
            raise ValueError("IICS_SESSION_TOKEN is required for legacy token mode")
        return cls.from_sso_token(token)

    # ── 认证生命周期 ─────────────────────────────────────────────────────
    def can_refresh(self) -> bool:
        """legacy token 模式无法自动重新登录。"""
        return not self.legacy_token_mode and self.credentials is not None

    def login(self) -> None:
        """执行 Login 并应用身份；环境 pin 校验不通过时不保存任何 session。"""
        if not self.can_refresh():
            raise AuthenticationError(
                "当前会话无法自动登录（legacy token 模式或缺少 credential 配置）。")
        identity = perform_login(self.credentials)
        self._apply_identity(identity)

    def _apply_identity(self, identity: LoginIdentity) -> None:
        self._guard_environment(identity)
        self.session_id = identity.session_id
        self._server_url = identity.server_url or DEFAULT_SERVER_URL
        self.org_id = identity.org_id
        self.org_uuid = identity.org_uuid
        now = time.time()
        self.authenticated_at = now
        self.last_activity_at = now

    def _guard_environment(self, identity: LoginIdentity) -> None:
        """TOFU 保护：未 pin → 阻断；orgUuid 不一致 → 阻断。均不写 pin。"""
        if not self.require_pin:
            return
        store = self.pin_store or EnvironmentPinStore.default()
        pin = store.get(self.environment)
        if pin is None:
            raise EnvironmentPinRequiredError(
                f"环境 {self.environment} 尚未绑定 org 身份（未 pin），已阻断业务调用。"
                f"请先执行 `python iics_cli.py auth-check --env {self.environment}`，"
                f"人工核对 org_id / org_uuid 正确后，再执行 "
                f"`python iics_cli.py auth-pin --env {self.environment}`。")
        store.verify(self.environment, identity.org_uuid)

    def invalidate(self) -> None:
        """丢弃当前进程内 session（不影响 server_url 以外的环境绑定）。"""
        self.session_id = None
        self.authenticated_at = None
        self.last_activity_at = None

    def refresh(self) -> None:
        """失效 + 重新 Login。"""
        self.invalidate()
        self.login()

    def ensure_valid(self) -> None:
        """业务请求前的最小保障：无 session 则登录；idle 过久则主动 refresh。"""
        if self.legacy_token_mode:
            if not self.session_id:
                raise AuthenticationError("legacy token 模式下缺少 session 值。")
            return
        if not self.session_id:
            self.login()
            return
        if (self.last_activity_at is not None
                and (time.time() - self.last_activity_at) >= IDLE_REFRESH_SECONDS):
            _log(f"session idle >= {IDLE_REFRESH_SECONDS}s; proactive refresh")
            self.refresh()

    def touch(self) -> None:
        """成功业务请求后更新活跃时间。"""
        self.last_activity_at = time.time()

    # ── Header 注入 ──────────────────────────────────────────────────────
    def headers_v2(self) -> dict:
        if not self.session_id:
            raise AuthenticationError("no active IICS session")
        return {
            "icSessionId": self.session_id,
            "Content-Type": "application/json",
        }

    def headers_v3(self) -> dict:
        if not self.session_id:
            raise AuthenticationError("no active IICS session")
        return {
            "INFA-SESSION-ID": self.session_id,
            "Content-Type": "application/json",
        }

    # ── 401 处理（向后兼容入口）──────────────────────────────────────────
    def handle_401(self) -> None:
        """可 refresh 时重新登录；legacy 模式保持原有语义直接抛错。"""
        if self.can_refresh():
            self.refresh()
            return
        raise RuntimeError(
            "IICS session expired or unauthorized. Legacy token mode cannot "
            "re-authenticate; set IICS_ENV/--env with credential config, or "
            "refresh IICS_SESSION_TOKEN manually."
        )


def _log(msg: str) -> None:
    print(f"[IICS auth] {msg}", file=sys.stderr)


# ── IICSClient ───────────────────────────────────────────────────────────────
class IICSClient:
    """共享 HTTP 客户端。v0.1 只读：_request 仅支持 GET。"""

    session: SessionManager

    def __init__(self, session: SessionManager):
        self.session = session
        self.connection_cache = ConnectionCache()
        # v0.1.x：MTT enrichment 的 process-local 缓存（同一 id 只 fetch 一次）
        self.mapping_cache = ObjectCache()
        self.runtime_cache = ObjectCache()

    def _build_url_and_headers(self, path, use_v3, caller_headers):
        """每次实际 HTTP attempt 前重建：refresh 后 serverUrl / session 均可能变化。"""
        if use_v3:
            url = build_v3_url(self.session.server_url, path)
            headers = self.session.headers_v3()
        else:
            url = build_v2_url(self.session.server_url, path)
            headers = self.session.headers_v2()
        if caller_headers:
            headers.update(caller_headers)
        return url, headers

    def _request(
        self,
        method: str,
        path: str,
        use_v3: bool = False,
        **kwargs,
    ) -> requests.Response:
        """发出 IICS GET 请求。

        - v0.1 安全边界：非 GET 一律 ValueError（业务写能力不存在）
        - 按 use_v3 选择 URL 工厂与 Header（V3: INFA-SESSION-ID / V2: icSessionId）
        - 每个 attempt 都基于当前 session.server_url / session_id 重建 URL 与 Header
        - 401：invalidate → 重新 Login → 重建 URL/Header → 重试一次；再 401 → AuthenticationError
        - 403：不 refresh，直接 PermissionDeniedError
        - 瞬时错误（Timeout / ConnectionError / 429/502/503/504）：最多 3 次总尝试
        - 认证 refresh 计数与瞬时重试计数相互独立
        """
        method = method.upper()
        if method != "GET":
            raise ValueError("v0.1 only supports GET requests")
        if not path:
            raise ValueError("path is required")

        caller_headers = kwargs.pop("headers", None)
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)

        self.session.ensure_valid()

        auth_refresh_used = 0
        attempt = 0
        while True:
            attempt += 1
            url, headers = self._build_url_and_headers(path, use_v3, caller_headers)
            try:
                response = requests.request(
                    method, url, headers=headers, **kwargs
                )
            except (requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError) as e:
                if attempt < TRANSIENT_MAX_ATTEMPTS:
                    self._sleep_backoff(attempt, type(e).__name__)
                    continue
                raise

            # 401：会话失效 → 最多重新登录一次并重试（URL/Header 均重建）
            if response.status_code == 401:
                if (auth_refresh_used < AUTH_REFRESH_MAX
                        and self.session.can_refresh()):
                    auth_refresh_used += 1
                    _log("business GET returned 401; re-authenticating once")
                    self.session.refresh()
                    attempt = 0  # 认证 retry 不消耗业务瞬时重试预算
                    continue
                if not self.session.can_refresh():
                    self.session.handle_401()
                raise AuthenticationError(
                    "IICS 业务请求在重新登录后仍返回 401，已停止重试。"
                    "请确认该账号在目标 org 是否仍然有效。")

            # 403：session 有效但权限不足 —— 不 refresh
            if response.status_code == 403:
                raise PermissionDeniedError(
                    f"IICS 拒绝访问（HTTP 403）：{path}。"
                    f"session 有效但权限不足，不会重新登录。")

            # 瞬时 HTTP 错误：重试
            if (response.status_code in TRANSIENT_STATUS
                    and attempt < TRANSIENT_MAX_ATTEMPTS):
                self._sleep_backoff(attempt, f"HTTP {response.status_code}")
                continue

            response.raise_for_status()
            self.session.touch()
            return response

    @staticmethod
    def _sleep_backoff(attempt: int, reason: str) -> None:
        """瞬时错误 backoff + stderr 日志（不含 token/cookie/header）。"""
        idx = min(attempt - 1, len(TRANSIENT_BACKOFF) - 1)
        delay = TRANSIENT_BACKOFF[idx]
        print(
            f"[IICS retry] attempt {attempt}/{TRANSIENT_MAX_ATTEMPTS} "
            f"failed ({reason}); retrying in {delay}s",
            file=sys.stderr,
        )
        time.sleep(delay)

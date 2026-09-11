"""
auth/errors.py — 认证 / 环境层异常语义（v0.1 Amendment 10）

设计原则：
  - 语义可区分：Login 自身 401/403（凭据问题）≠ 业务 GET refresh 后仍 401（会话问题）
  - fail closed：环境未指定 / 未 pin / org 不匹配一律阻断业务调用
  - 不泄密：任何异常 message 都不得包含 username / password / icSessionId /
    原始 Login 响应体
"""


class IICSAuthError(Exception):
    """认证 / 环境层错误基类。"""


# ── 环境选择 ────────────────────────────────────────────────────────────────
class MissingEnvironmentError(IICSAuthError):
    """未提供 --env，且未设置 IICS_ENV。不默认 PROD，直接 fail closed。"""


class UnknownEnvironmentError(IICSAuthError):
    """提供的环境名/alias 无法解析为 credential config 中的规范环境键。"""


class DuplicateEnvironmentArgumentError(IICSAuthError):
    """同一条命令中出现多个 --env。fail closed，不采用 first/last wins。"""


# ── Credential 配置 ─────────────────────────────────────────────────────────
class CredentialConfigError(IICSAuthError):
    """credential 配置文件缺失 / 不可读 / JSON 非法 / schema 不合法。"""


class MissingCredentialsError(CredentialConfigError):
    """目标环境存在，但 username / password 为空字符串（视为未配置）。"""


# ── Login ───────────────────────────────────────────────────────────────────
class InvalidCredentialsError(IICSAuthError):
    """Login API 返回 401 / 403：账号本身无法完成 Login。不做重复 credential retry。"""


class InvalidLoginResponseError(IICSAuthError):
    """Login 返回非 JSON / HTML / 缺少 icSessionId / serverUrl / orgUuid / orgId。"""


# ── Environment Pin ─────────────────────────────────────────────────────────
class EnvironmentPinRequiredError(IICSAuthError):
    """该环境尚未 pin。必须先 auth-check → 人工确认 → auth-pin。"""


class EnvironmentMismatchError(IICSAuthError):
    """Login 得到的 orgUuid 与已 pin 的 orgUuid 不一致。禁止自动覆盖。"""


# ── 业务请求期认证语义 ──────────────────────────────────────────────────────
class PermissionDeniedError(IICSAuthError):
    """业务 GET 返回 403：session 有效但权限不足。不 refresh。"""


class AuthenticationError(IICSAuthError):
    """业务 GET 401 → refresh 一次 → 仍 401；或无可用 session 且无法 refresh。"""

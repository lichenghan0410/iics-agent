"""auth package — IICS Agent 认证 / 环境层（Amendment 10）。

职责边界：
  - 只负责：环境解析、credential 读取、Login POST、environment pin、认证异常语义
  - 不负责：任何 IICS 业务 API 调用（业务一律经 IICSClient，且仍严格 GET-only）
"""

from .credentials import (  # noqa: F401
    DEFAULT_LOGIN_URL,
    CREDENTIALS_FILENAME,
    TEMPLATE_FILENAME,
    CredentialConfig,
    EnvironmentCredentials,
    resolve_config_path,
    resolve_environment_name,
)
from .errors import (  # noqa: F401
    AuthenticationError,
    CredentialConfigError,
    DuplicateEnvironmentArgumentError,
    EnvironmentMismatchError,
    EnvironmentPinRequiredError,
    IICSAuthError,
    InvalidCredentialsError,
    InvalidLoginResponseError,
    MissingCredentialsError,
    MissingEnvironmentError,
    PermissionDeniedError,
    UnknownEnvironmentError,
)
from .login import LoginIdentity, perform_login  # noqa: F401
from .pins import EnvironmentPinStore, PIN_FILENAME  # noqa: F401

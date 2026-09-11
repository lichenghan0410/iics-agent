"""
auth/credentials.py — 多环境 credential 配置 loader + 环境名解析

所有权边界（Amendment 10 §3）：
  - 真实值文件 <repo_root>/iics_credentials.json 由使用者本人手工维护
  - 模板 <repo_root>/iics_credentials.template.json 进 git，值恒为空字符串
  - 本模块只读取，不写入、不打印、不记录 username / password
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .errors import (
    CredentialConfigError,
    MissingCredentialsError,
    MissingEnvironmentError,
    UnknownEnvironmentError,
)

# 代码内默认 Login URL（解析优先级最低）
DEFAULT_LOGIN_URL = "https://dm-us.informaticacloud.com/ma/api/v2/user/login"

CREDENTIALS_FILENAME = "iics_credentials.json"
TEMPLATE_FILENAME = "iics_credentials.template.json"

REPO_ROOT = Path(__file__).resolve().parent.parent


def resolve_config_path(explicit: Optional[str] = None) -> Path:
    """配置文件路径解析优先级：显式参数 → IICS_CONFIG_FILE → <repo_root>/iics_credentials.json"""
    if explicit:
        return Path(explicit)
    override = os.environ.get("IICS_CONFIG_FILE", "").strip()
    if override:
        return Path(override)
    return REPO_ROOT / CREDENTIALS_FILENAME


@dataclass(frozen=True)
class EnvironmentCredentials:
    """单个环境的认证材料。repr 永远脱敏。"""

    environment: str
    username: str = field(repr=False)
    password: str = field(repr=False)
    login_url: str = DEFAULT_LOGIN_URL

    def __repr__(self) -> str:  # 防止 dataclass repr / f-string 泄密
        return (f"EnvironmentCredentials(environment={self.environment!r}, "
                f"login_url={self.login_url!r}, username=<redacted>, "
                f"password=<redacted>)")

    __str__ = __repr__


class CredentialConfig:
    """iics_credentials.json 的只读视图。"""

    def __init__(self, raw: dict, source_path: Path):
        self.source_path = source_path
        self._raw = raw
        envs = raw.get("environments")
        if not isinstance(envs, dict) or not envs:
            raise CredentialConfigError(
                f"credential config 缺少非空 'environments' 对象: {source_path}")
        self._environments = envs
        self._top_login_url = raw.get("login_url") or ""
        self._alias_map = self._build_alias_map(envs)

    # ── 加载 ────────────────────────────────────────────────────────────
    @classmethod
    def load(cls, path: Optional[str] = None) -> "CredentialConfig":
        p = resolve_config_path(path)
        if not p.exists():
            raise CredentialConfigError(
                f"credential 配置文件不存在: {p}。请复制 {TEMPLATE_FILENAME} 为 "
                f"{CREDENTIALS_FILENAME} 并手工填入各环境 username/password。")
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as e:
            raise CredentialConfigError(f"无法读取 credential 配置文件 {p}: {e}") from None
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as e:
            # 只报告结构性位置，不回显文件内容
            raise CredentialConfigError(
                f"credential 配置文件不是合法 JSON: {p} (line {e.lineno} col {e.colno})"
            ) from None
        if not isinstance(raw, dict):
            raise CredentialConfigError(f"credential 配置文件顶层必须是 JSON 对象: {p}")
        return cls(raw, p)

    # ── 环境名解析 ──────────────────────────────────────────────────────
    @staticmethod
    def _build_alias_map(envs: dict) -> dict:
        mapping: dict = {}
        for canonical, cfg in envs.items():
            keys = [canonical]
            if isinstance(cfg, dict):
                aliases = cfg.get("aliases") or []
                if isinstance(aliases, list):
                    keys.extend(a for a in aliases if isinstance(a, str))
            for k in keys:
                norm = k.strip().casefold()
                if norm:
                    mapping[norm] = canonical
        return mapping

    @property
    def environments(self) -> list:
        return list(self._environments.keys())

    def resolve_environment(self, name: str) -> str:
        """把 --env / IICS_ENV 的输入 normalize 为规范环境键（DEV/TEST/PROD）。"""
        norm = (name or "").strip().casefold()
        if not norm:
            raise MissingEnvironmentError(
                "未指定 IICS 环境。请使用 --env DEV|TEST|PROD 或设置 IICS_ENV。")
        canonical = self._alias_map.get(norm)
        if not canonical:
            raise UnknownEnvironmentError(
                f"未知环境 {name!r}。可用环境: {', '.join(self.environments)}"
                f"（alias 见 {CREDENTIALS_FILENAME} 各环境 aliases 字段）。")
        return canonical

    # ── 取凭据 ──────────────────────────────────────────────────────────
    def get(self, environment: str) -> EnvironmentCredentials:
        """environment 必须已经是规范键；返回该环境凭据。空值 → MissingCredentialsError。"""
        cfg = self._environments.get(environment)
        if not isinstance(cfg, dict):
            raise UnknownEnvironmentError(
                f"credential config 中不存在环境 {environment!r}: {self.source_path}")

        username = cfg.get("username")
        password = cfg.get("password")
        if not isinstance(username, str) or not username.strip():
            raise MissingCredentialsError(
                f"环境 {environment} 的 username 未配置（空字符串）。"
                f"请手工编辑 {self.source_path} 填入该环境账号。")
        if not isinstance(password, str) or not password:
            raise MissingCredentialsError(
                f"环境 {environment} 的 password 未配置（空字符串）。"
                f"请手工编辑 {self.source_path} 填入该环境密码。")

        login_url = (cfg.get("login_url") or "").strip() \
            or (self._top_login_url or "").strip() \
            or DEFAULT_LOGIN_URL

        return EnvironmentCredentials(
            environment=environment,
            username=username.strip(),
            password=password,
            login_url=login_url,
        )


def resolve_environment_name(
    config: CredentialConfig,
    cli_env: Optional[str] = None,
    env_var: Optional[str] = None,
) -> str:
    """环境选择优先级：CLI --env → IICS_ENV → MissingEnvironmentError（不默认 PROD）。"""
    candidate = (cli_env or "").strip() or (env_var or "").strip()
    if not candidate:
        raise MissingEnvironmentError(
            "未指定 IICS 环境。请使用 --env DEV|TEST|PROD，或设置环境变量 IICS_ENV。"
            "本工具不会默认使用 PROD。")
    return config.resolve_environment(candidate)

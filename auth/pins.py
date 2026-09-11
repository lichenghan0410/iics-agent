"""
auth/pins.py — Environment Pin（TOFU orgUuid 绑定）

- 文件：<repo_root>/.env_pins.json（由 Python 维护）
- 只允许保存：org_uuid / org_id / pinned_at
- 绝不保存：username / password / icSessionId / serverUrl
  （serverUrl 每次以 Login 当前 response 为准，不作为环境身份）
- 写入必须原子：temp file → flush/fsync → os.replace()
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .errors import CredentialConfigError, EnvironmentMismatchError

PIN_FILENAME = ".env_pins.json"
PIN_SCHEMA_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parent.parent

# pin 记录只允许出现这些键；任何其它键（尤其 secret/session）一律不写
ALLOWED_PIN_FIELDS = ("org_uuid", "org_id", "pinned_at")


class EnvironmentPinStore:
    """.env_pins.json 的读写封装。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else (REPO_ROOT / PIN_FILENAME)

    @classmethod
    def default(cls) -> "EnvironmentPinStore":
        override = os.environ.get("IICS_PIN_FILE", "").strip()
        return cls(Path(override)) if override else cls()

    # ── 读 ──────────────────────────────────────────────────────────────
    def _load(self) -> dict:
        if not self.path.exists():
            return {"schema_version": PIN_SCHEMA_VERSION, "pins": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise CredentialConfigError(
                f"environment pin 文件不可读或非法 JSON: {self.path} ({type(e).__name__})"
            ) from None
        if not isinstance(raw, dict) or not isinstance(raw.get("pins"), dict):
            raise CredentialConfigError(
                f"environment pin 文件结构非法（缺少 pins 对象）: {self.path}")
        return raw

    def get(self, environment: str) -> Optional[dict]:
        """返回该环境的 pin 记录；未 pin 返回 None。"""
        pin = self._load()["pins"].get(environment)
        return pin if isinstance(pin, dict) else None

    def all_pins(self) -> dict:
        return dict(self._load()["pins"])

    # ── 校验 ────────────────────────────────────────────────────────────
    def verify(self, environment: str, org_uuid: str) -> None:
        """org_uuid 与已有 pin 不一致 → EnvironmentMismatchError；未 pin 时不判定。"""
        pin = self.get(environment)
        if pin is None:
            return
        pinned = pin.get("org_uuid") or ""
        if pinned != org_uuid:
            raise EnvironmentMismatchError(
                f"环境 {environment} 的 org 身份与已绑定的 pin 不一致："
                f"pinned org_uuid={pinned}，本次 Login org_uuid={org_uuid}。"
                f"已阻断所有业务调用。若确实需要重新绑定，请人工确认后执行 "
                f"auth-pin --env {environment} --force。")

    # ── 写 ──────────────────────────────────────────────────────────────
    def set(self, environment: str, org_uuid: str, org_id: str,
            force: bool = False) -> dict:
        """写入 pin。已有不同 pin 且未 force → EnvironmentMismatchError（禁止静默覆盖）。"""
        if not org_uuid:
            raise CredentialConfigError("org_uuid 为空，拒绝写入 environment pin。")

        data = self._load()
        existing = data["pins"].get(environment)
        if isinstance(existing, dict):
            if existing.get("org_uuid") == org_uuid:
                return dict(existing)  # 幂等：同一 org，不重写
            if not force:
                raise EnvironmentMismatchError(
                    f"环境 {environment} 已绑定 org_uuid={existing.get('org_uuid')}，"
                    f"与本次 Login 的 org_uuid={org_uuid} 不同。"
                    f"默认不覆盖；重新绑定必须由使用者显式执行 --force。")

        record = {
            "org_uuid": org_uuid,
            "org_id": org_id or "",
            "pinned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        # 防御：只保留允许字段
        record = {k: v for k, v in record.items() if k in ALLOWED_PIN_FIELDS}
        data["schema_version"] = PIN_SCHEMA_VERSION
        data["pins"][environment] = record
        self._atomic_write(data)
        return dict(record)

    def _atomic_write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".env_pins.", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

#!/usr/bin/env python3
"""
tests/test_cli_contract.py — auth-check / auth-pin 的 stdout contract 与
CLI 环境选择歧义防护（Amendment 10 第二轮修复）

全部使用 mock / fake / 临时配置，不触网、不读取真实 credential、
不读写真实 .env_pins.json。测试中的 "fake-*" 字符串均为虚构占位值。
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import iics_cli  # noqa: E402
from auth.errors import DuplicateEnvironmentArgumentError  # noqa: E402

FAKE_USER = "fake-user-value"
FAKE_PASS = "fake-pass-value"
FAKE_SESSION = "FAKE-ICSESSION-VALUE"
FAKE_USER_ID = "FAKE-USER-ID-0001"
ORG_UUID_TEST = "fake-uuid-test-0000"
ORG_UUID_OTHER = "fake-uuid-other-9999"

# 故意包含 §13.1 要求丢弃的字段，用于验证它们不会透传到 stdout
FULL_LOGIN_BODY = {
    "icSessionId": FAKE_SESSION,
    "serverUrl": "https://use4.dm-us.informaticacloud.com/saas",
    "orgId": "FAKE-ORGID",
    "orgUuid": ORG_UUID_TEST,
    "id": FAKE_USER_ID,
    "roles": [{"name": "fake-role"}],
    "usergroups": [{"createdBy": "fake-leak@example.invalid"}],
    "emails": "fake-leak@example.invalid",
    "securityQuestion": "fake security question text",
    "securityAnswer": "********",
    "password": "********",
}

FORBIDDEN_VALUES = (FAKE_USER, FAKE_PASS, FAKE_SESSION, FAKE_USER_ID,
                    "fake-role", "fake-leak@example.invalid",
                    "fake security question text")

FORBIDDEN_KEYS = ("username", "password", "icSessionId", "session_id", "id",
                  "roles", "usergroups", "emails", "securityQuestion",
                  "securityAnswer", "Authorization", "headers")


class FakeResponse:
    def __init__(self, body):
        self.status_code = 200
        self._b = body
        self.headers = {"Content-Type": "application/json"}
        self.text = ""

    def json(self):
        return dict(self._b)

    def raise_for_status(self):
        return None


class AuthCliTestBase(unittest.TestCase):
    """每个用例都在独立临时目录里跑，绝不碰仓库里的真实文件。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.cfg_path = d / "fake_credentials.json"
        self.pin_path = d / "fake_pins.json"
        self.cfg_path.write_text(json.dumps({
            "schema_version": 1,
            "login_url": "https://login.invalid/ma/api/v2/user/login",
            "environments": {
                "DEV": {"aliases": ["dev"], "username": FAKE_USER,
                        "password": FAKE_PASS},
                "TEST": {"aliases": ["test", "テスト"], "username": FAKE_USER,
                         "password": FAKE_PASS},
                "PROD": {"aliases": ["prod"], "username": FAKE_USER,
                         "password": FAKE_PASS},
            },
        }), encoding="utf-8")
        self.env_patch = mock.patch.dict(os.environ, {
            "IICS_CONFIG_FILE": str(self.cfg_path),
            "IICS_PIN_FILE": str(self.pin_path),
        }, clear=False)
        self.env_patch.start()
        os.environ.pop("IICS_ENV", None)
        os.environ.pop("IICS_AUTH_MODE", None)

    def tearDown(self):
        self.env_patch.stop()
        self.tmp.cleanup()

    def write_pin(self, environment, org_uuid, org_id="FAKE-ORGID"):
        self.pin_path.write_text(json.dumps({
            "schema_version": 1,
            "pins": {environment: {"org_uuid": org_uuid, "org_id": org_id,
                                   "pinned_at": "2026-01-01T00:00:00+00:00"}},
        }), encoding="utf-8")

    def run_cli(self, argv, login_body=None):
        """运行 CLI，返回 (exit_code, stdout_text, stderr_text)。Login 全 mock。"""
        body = dict(FULL_LOGIN_BODY if login_body is None else login_body)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("auth.login.requests.post",
                        return_value=FakeResponse(body)), \
                redirect_stdout(out), redirect_stderr(err):
            code = iics_cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def assert_no_secret_leak(self, *texts):
        blob = "".join(texts)
        for value in FORBIDDEN_VALUES:
            self.assertNotIn(value, blob)
        for key in FORBIDDEN_KEYS:
            self.assertNotIn(f'"{key}"', blob)


# ── Test A / B / C：auth-check stdout contract ──────────────────────────────
class TestAuthCheckStdoutContract(AuthCliTestBase):

    def test_A_pin_required_exact_keys(self):
        """未 pin 时 stdout 顶层 key 必须恰好是 5 个，且不含 next_step。"""
        code, out, err = self.run_cli(["auth-check", "--env", "TEST"])
        result = json.loads(out)
        self.assertEqual(set(result.keys()), {
            "environment", "status", "org_id", "org_uuid", "server_host",
        })
        self.assertEqual(result["status"], "pin_required")
        self.assertNotIn("next_step", out)
        self.assertNotIn("next_step", result)
        for key in ("message", "hint", "instruction"):
            self.assertNotIn(key, result)
        self.assertEqual(code, 0)
        # 未 pin 的 auth-check 绝不写 pin 文件
        self.assertFalse(self.pin_path.exists())

    def test_B_guidance_goes_to_stderr_only(self):
        """人工操作提示必须在 stderr，且不含任何敏感值。"""
        code, out, err = self.run_cli(["auth-check", "--env", "TEST"])
        self.assertIn("auth-pin", err)
        self.assertIn("TEST", err)
        self.assertNotIn("auth-pin --env TEST", out)
        self.assert_no_secret_leak(out, err)
        # stdout 必须是可解析的纯 JSON
        self.assertTrue(out.lstrip().startswith("{"))
        json.loads(out)

    def test_C_forbidden_login_fields_not_in_stdout(self):
        """Login 响应里的 roles/usergroups/emails/securityQuestion/... 不得透传。"""
        for argv in (["auth-check", "--env", "TEST"],):
            code, out, err = self.run_cli(argv)
            self.assert_no_secret_leak(out, err)
            result = json.loads(out)
            for key in FORBIDDEN_KEYS:
                self.assertNotIn(key, result)

    def test_pinned_exact_keys(self):
        self.write_pin("TEST", ORG_UUID_TEST)
        code, out, err = self.run_cli(["auth-check", "--env", "TEST"])
        result = json.loads(out)
        self.assertEqual(set(result.keys()), {
            "environment", "status", "org_id", "org_uuid", "server_host",
            "pinned_at",
        })
        self.assertEqual(result["status"], "pinned")
        self.assertEqual(code, 0)
        self.assert_no_secret_leak(out, err)

    def test_pin_mismatch_exact_keys(self):
        self.write_pin("TEST", ORG_UUID_OTHER)
        code, out, err = self.run_cli(["auth-check", "--env", "TEST"])
        result = json.loads(out)
        self.assertEqual(set(result.keys()), {
            "environment", "status", "org_id", "org_uuid", "server_host",
            "pinned_org_uuid", "error_type", "error",
        })
        self.assertEqual(result["status"], "pin_mismatch")
        self.assertEqual(result["error_type"], "EnvironmentMismatchError")
        self.assertEqual(code, 1)
        self.assert_no_secret_leak(out, err)

    def test_unknown_status_fails_closed(self):
        """未在 allowlist 中登记的 status 必须拒绝输出，而不是原样透传。"""
        with self.assertRaises(ValueError):
            iics_cli._project_auth_stdout(
                {"status": "brand_new_status", "environment": "TEST"},
                iics_cli.AUTH_CHECK_STDOUT_KEYS, "auth-check")

    def test_forbidden_key_in_result_blocks_output(self):
        with self.assertRaises(ValueError) as ctx:
            iics_cli._project_auth_stdout(
                {"status": "pin_required", "environment": "TEST",
                 "org_id": "x", "org_uuid": "y", "server_host": "z",
                 "icSessionId": FAKE_SESSION},
                iics_cli.AUTH_CHECK_STDOUT_KEYS, "auth-check")
        self.assertNotIn(FAKE_SESSION, str(ctx.exception))


# ── auth-pin stdout contract（§8.1）─────────────────────────────────────────
class TestAuthPinStdoutContract(AuthCliTestBase):

    PIN_KEYS = {"environment", "status", "org_id", "org_uuid", "server_host",
                "pinned_at", "pin_file"}

    def test_pinned_exact_keys(self):
        code, out, err = self.run_cli(["auth-pin", "--env", "TEST"])
        result = json.loads(out)
        self.assertEqual(set(result.keys()), self.PIN_KEYS)
        self.assertEqual(result["status"], "pinned")
        self.assertEqual(code, 0)
        self.assert_no_secret_leak(out, err)

    def test_already_pinned_exact_keys(self):
        self.write_pin("TEST", ORG_UUID_TEST)
        code, out, err = self.run_cli(["auth-pin", "--env", "TEST"])
        result = json.loads(out)
        self.assertEqual(set(result.keys()), self.PIN_KEYS)
        self.assertEqual(result["status"], "already_pinned")
        self.assertEqual(code, 0)
        self.assert_no_secret_leak(out, err)

    def test_mismatch_refuses_and_emits_only_error_shape(self):
        self.write_pin("TEST", ORG_UUID_OTHER)
        code, out, err = self.run_cli(["auth-pin", "--env", "TEST"])
        result = json.loads(out)
        self.assertEqual(set(result.keys()), {"error", "error_type"})
        self.assertEqual(result["error_type"], "EnvironmentMismatchError")
        self.assertEqual(code, 1)
        # pin 未被覆盖
        pins = json.loads(self.pin_path.read_text(encoding="utf-8"))["pins"]
        self.assertEqual(pins["TEST"]["org_uuid"], ORG_UUID_OTHER)
        self.assert_no_secret_leak(out, err)

    def test_written_pin_file_has_no_secret(self):
        self.run_cli(["auth-pin", "--env", "TEST"])
        text = self.pin_path.read_text(encoding="utf-8")
        for value in FORBIDDEN_VALUES:
            self.assertNotIn(value, text)
        for key in ("icSessionId", "serverUrl", "username", "password"):
            self.assertNotIn(key, text)

    def test_forbidden_key_blocked_for_auth_pin_too(self):
        with self.assertRaises(ValueError):
            iics_cli._project_auth_stdout(
                {"status": "pinned", "roles": ["x"]},
                iics_cli.AUTH_PIN_STDOUT_KEYS, "auth-pin")


# ── 重复 --env 防护 ────────────────────────────────────────────────────────
class TestDuplicateEnvFlag(AuthCliTestBase):

    def test_case1_single_env_before_subcommand(self):
        code, out, err = self.run_cli(["--env", "TEST", "auth-check"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["environment"], "TEST")

    def test_case2_single_env_after_subcommand(self):
        code, out, err = self.run_cli(["auth-check", "--env", "TEST"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["environment"], "TEST")

    def test_case3_duplicate_different_values_rejected(self):
        code, out, err = self.run_cli(["--env", "DEV", "auth-check",
                                       "--env", "TEST"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["error_type"],
                         "DuplicateEnvironmentArgumentError")

    def test_case4_duplicate_same_value_rejected(self):
        code, out, err = self.run_cli(["--env", "TEST", "auth-check",
                                       "--env", "TEST"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["error_type"],
                         "DuplicateEnvironmentArgumentError")

    def test_case5_cli_env_overrides_iics_env_not_duplicate(self):
        with mock.patch.dict(os.environ, {"IICS_ENV": "DEV"}, clear=False):
            code, out, err = self.run_cli(["--env", "TEST", "auth-check"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["environment"], "TEST")

    def test_iics_env_alone_still_works(self):
        with mock.patch.dict(os.environ, {"IICS_ENV": "DEV"}, clear=False):
            code, out, err = self.run_cli(["auth-check"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["environment"], "DEV")

    def test_equals_form_duplicate_rejected(self):
        code, out, err = self.run_cli(["--env=DEV", "auth-check", "--env=TEST"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["error_type"],
                         "DuplicateEnvironmentArgumentError")

    def test_duplicate_rejected_before_any_login(self):
        """重复 --env 必须在读取 credential / 发起 Login 之前就失败。"""
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("auth.login.requests.post") as post, \
                mock.patch("auth.credentials.CredentialConfig.load") as load, \
                redirect_stdout(out), redirect_stderr(err):
            code = iics_cli.main(["--env", "DEV", "auth-check", "--env", "TEST"])
        self.assertEqual(code, 1)
        post.assert_not_called()
        load.assert_not_called()

    def test_duplicate_rejected_for_business_subcommands(self):
        code, out, err = self.run_cli(["--env", "DEV", "list-tasks", "F",
                                       "--env", "TEST"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["error_type"],
                         "DuplicateEnvironmentArgumentError")

    def test_counter_helper(self):
        self.assertEqual(iics_cli.count_env_flags([]), 0)
        self.assertEqual(iics_cli.count_env_flags(["--env", "TEST"]), 1)
        self.assertEqual(iics_cli.count_env_flags(["--env=TEST"]), 1)
        self.assertEqual(
            iics_cli.count_env_flags(["--env", "A", "x", "--env=B"]), 2)
        # 不应把 --environment 之类误判
        self.assertEqual(iics_cli.count_env_flags(["--envelope", "x"]), 0)
        iics_cli.assert_single_env_flag(["--env", "TEST"])
        with self.assertRaises(DuplicateEnvironmentArgumentError):
            iics_cli.assert_single_env_flag(["--env", "A", "--env", "B"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

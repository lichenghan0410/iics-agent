#!/usr/bin/env python3
"""
tests/test_auth.py — Amendment 10 认证架构单元测试（全部使用 mock，不触网）

覆盖：environment 解析 / credential config / Login / environment pin /
业务请求 header 与 401 refresh / 403 不 refresh / GET-only 安全回归 /
Login 响应体最小化持有与不泄密。

测试中出现的 "u-dev"/"pw-dev" 等字符串均为虚构占位值，不是任何真实账号。
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

import iics_client  # noqa: E402
from auth.credentials import (CredentialConfig, DEFAULT_LOGIN_URL,  # noqa: E402
                              resolve_environment_name)
from auth.errors import (AuthenticationError, CredentialConfigError,  # noqa: E402
                         EnvironmentMismatchError, EnvironmentPinRequiredError,
                         InvalidCredentialsError, InvalidLoginResponseError,
                         MissingCredentialsError, MissingEnvironmentError,
                         PermissionDeniedError, UnknownEnvironmentError)
from auth.login import LoginIdentity, perform_login  # noqa: E402
from auth.pins import EnvironmentPinStore  # noqa: E402
from iics_client import IICSClient, SessionManager  # noqa: E402

FAKE_SESSION = "FAKE-SESSION-ID-0001"
FAKE_SESSION_2 = "FAKE-SESSION-ID-0002"
ORG_UUID_TEST = "11111111-2222-3333-4444-555555555555"
ORG_UUID_PROD = "99999999-8888-7777-6666-555555555555"

# 完整 Login 响应体（含 §13.1 要求丢弃的敏感/冗余字段）
FULL_LOGIN_BODY = {
    "icSessionId": FAKE_SESSION,
    "serverUrl": "https://use4.dm-us.informaticacloud.com/saas",
    "orgId": "000LVT",
    "orgUuid": ORG_UUID_TEST,
    "id": "00ABCD",
    "roles": [{"name": "Admin"}],
    "usergroups": [{"createdBy": "someone@example.com"}],
    "emails": "someone@example.com",
    "securityQuestion": "What is your favourite colour?",
    "securityAnswer": "********",
    "password": "********",
    "name": "svc-account",
}


def make_config(dev=("", ""), test=("u-test", "pw-test"), prod=("u-prod", "pw-prod"),
                top_login_url=DEFAULT_LOGIN_URL, env_login_url=None):
    envs = {
        "DEV": {"aliases": ["dev", "development", "開発", "開発環境"],
                "username": dev[0], "password": dev[1]},
        "TEST": {"aliases": ["test", "testing", "テスト", "テスト環境"],
                 "username": test[0], "password": test[1]},
        "PROD": {"aliases": ["prod", "production", "本番", "本番環境"],
                 "username": prod[0], "password": prod[1]},
    }
    if env_login_url:
        envs["TEST"]["login_url"] = env_login_url
    raw = {"schema_version": 1, "environments": envs}
    if top_login_url is not None:
        raw["login_url"] = top_login_url
    return raw


def write_config(tmpdir, raw=None, text=None):
    p = Path(tmpdir) / "iics_credentials.json"
    p.write_text(text if text is not None
                 else json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return str(p)


class FakeResponse:
    """最小 requests.Response 替身。"""

    def __init__(self, status_code=200, json_body=None, text="",
                 content_type="application/json"):
        self.status_code = status_code
        self._json = json_body
        self.text = text
        self.headers = {"Content-Type": content_type} if content_type else {}

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")


# ── 1. Environment 解析 ─────────────────────────────────────────────────────
class TestEnvironmentResolution(unittest.TestCase):
    def setUp(self):
        self.cfg = CredentialConfig(make_config(), Path("memory://cfg"))

    def test_cli_env_wins_over_env_var(self):
        self.assertEqual(resolve_environment_name(self.cfg, "TEST", "PROD"), "TEST")

    def test_env_var_used_when_no_cli(self):
        self.assertEqual(resolve_environment_name(self.cfg, "", "PROD"), "PROD")

    def test_alias_normalization(self):
        for raw, expected in [("test", "TEST"), ("testing", "TEST"),
                              ("テスト", "TEST"), ("テスト環境", "TEST"),
                              ("development", "DEV"), ("開発", "DEV"),
                              ("本番", "PROD"), ("PrOd", "PROD"), (" test ", "TEST")]:
            with self.subTest(raw=raw):
                self.assertEqual(resolve_environment_name(self.cfg, raw), expected)

    def test_missing_env_fails_closed_not_prod(self):
        with self.assertRaises(MissingEnvironmentError):
            resolve_environment_name(self.cfg, "", "")

    def test_unknown_env(self):
        with self.assertRaises(UnknownEnvironmentError):
            resolve_environment_name(self.cfg, "staging")


# ── 2. Credential config ────────────────────────────────────────────────────
class TestCredentialConfig(unittest.TestCase):
    def test_missing_config_file(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(CredentialConfigError):
                CredentialConfig.load(str(Path(d) / "nope.json"))

    def test_invalid_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_config(d, text="{not json")
            with self.assertRaises(CredentialConfigError):
                CredentialConfig.load(path)

    def test_missing_environments_key(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_config(d, raw={"schema_version": 1})
            with self.assertRaises(CredentialConfigError):
                CredentialConfig.load(path)

    def test_missing_environment_entry(self):
        cfg = CredentialConfig(make_config(), Path("memory://cfg"))
        with self.assertRaises(UnknownEnvironmentError):
            cfg.get("STAGING")

    def test_missing_username(self):
        cfg = CredentialConfig(make_config(test=("", "pw-test")), Path("memory://cfg"))
        with self.assertRaises(MissingCredentialsError):
            cfg.get("TEST")

    def test_missing_password(self):
        cfg = CredentialConfig(make_config(test=("u-test", "")), Path("memory://cfg"))
        with self.assertRaises(MissingCredentialsError):
            cfg.get("TEST")

    def test_empty_env_is_unconfigured_not_login_attempt(self):
        cfg = CredentialConfig(make_config(), Path("memory://cfg"))
        with self.assertRaises(MissingCredentialsError):
            cfg.get("DEV")   # DEV 默认空字符串

    def test_top_level_login_url(self):
        cfg = CredentialConfig(make_config(top_login_url="https://top/login"),
                               Path("memory://cfg"))
        self.assertEqual(cfg.get("TEST").login_url, "https://top/login")

    def test_env_login_url_override(self):
        cfg = CredentialConfig(
            make_config(top_login_url="https://top/login",
                        env_login_url="https://env/login"), Path("memory://cfg"))
        self.assertEqual(cfg.get("TEST").login_url, "https://env/login")
        self.assertEqual(cfg.get("PROD").login_url, "https://top/login")

    def test_default_login_url_when_absent(self):
        cfg = CredentialConfig(make_config(top_login_url=None), Path("memory://cfg"))
        self.assertEqual(cfg.get("TEST").login_url, DEFAULT_LOGIN_URL)

    def test_credentials_repr_is_redacted(self):
        cfg = CredentialConfig(make_config(), Path("memory://cfg"))
        creds = cfg.get("TEST")
        for rendered in (repr(creds), str(creds), f"{creds}"):
            self.assertNotIn("u-test", rendered)
            self.assertNotIn("pw-test", rendered)
            self.assertIn("redacted", rendered)


# ── 3. Login ────────────────────────────────────────────────────────────────
class TestLogin(unittest.TestCase):
    def setUp(self):
        self.creds = CredentialConfig(make_config(), Path("memory://cfg")).get("TEST")

    def _login_with(self, response):
        with mock.patch("auth.login.requests.post", return_value=response) as post:
            return perform_login(self.creds), post

    def test_login_success_extracts_allowed_fields(self):
        identity, post = self._login_with(FakeResponse(json_body=dict(FULL_LOGIN_BODY)))
        self.assertEqual(identity.session_id, FAKE_SESSION)
        self.assertEqual(identity.server_url,
                         "https://use4.dm-us.informaticacloud.com/saas")
        self.assertEqual(identity.org_id, "000LVT")
        self.assertEqual(identity.org_uuid, ORG_UUID_TEST)
        self.assertEqual(identity.server_host, "use4.dm-us.informaticacloud.com")
        self.assertEqual(post.call_args.args[0], DEFAULT_LOGIN_URL)

    def test_login_posts_credentials_as_json_body(self):
        with mock.patch("auth.login.requests.post",
                        return_value=FakeResponse(json_body=dict(FULL_LOGIN_BODY))) as post:
            perform_login(self.creds)
        self.assertEqual(post.call_args.kwargs["json"],
                         {"username": "u-test", "password": "pw-test"})

    def test_login_401_invalid_credentials_no_retry(self):
        with mock.patch("auth.login.requests.post",
                        return_value=FakeResponse(401)) as post:
            with self.assertRaises(InvalidCredentialsError):
                perform_login(self.creds)
        self.assertEqual(post.call_count, 1)

    def test_login_403_invalid_credentials(self):
        with mock.patch("auth.login.requests.post", return_value=FakeResponse(403)):
            with self.assertRaises(InvalidCredentialsError):
                perform_login(self.creds)

    def test_login_200_html(self):
        resp = FakeResponse(200, json_body=None, text="<html>login</html>",
                            content_type="text/html; charset=utf-8")
        with mock.patch("auth.login.requests.post", return_value=resp):
            with self.assertRaises(InvalidLoginResponseError):
                perform_login(self.creds)

    def test_login_missing_session_id(self):
        body = dict(FULL_LOGIN_BODY); body.pop("icSessionId")
        with mock.patch("auth.login.requests.post",
                        return_value=FakeResponse(json_body=body)):
            with self.assertRaises(InvalidLoginResponseError) as ctx:
                perform_login(self.creds)
        self.assertIn("icSessionId", str(ctx.exception))

    def test_login_missing_server_url(self):
        body = dict(FULL_LOGIN_BODY); body.pop("serverUrl")
        with mock.patch("auth.login.requests.post",
                        return_value=FakeResponse(json_body=body)):
            with self.assertRaises(InvalidLoginResponseError) as ctx:
                perform_login(self.creds)
        self.assertIn("serverUrl", str(ctx.exception))

    def test_login_missing_org_uuid(self):
        body = dict(FULL_LOGIN_BODY); body.pop("orgUuid")
        with mock.patch("auth.login.requests.post",
                        return_value=FakeResponse(json_body=body)):
            with self.assertRaises(InvalidLoginResponseError):
                perform_login(self.creds)

    def test_login_error_message_has_no_secrets(self):
        with mock.patch("auth.login.requests.post", return_value=FakeResponse(401)):
            with self.assertRaises(InvalidCredentialsError) as ctx:
                perform_login(self.creds)
        msg = str(ctx.exception)
        for secret in ("u-test", "pw-test"):
            self.assertNotIn(secret, msg)

    def test_identity_repr_redacts_session(self):
        identity, _ = self._login_with(FakeResponse(json_body=dict(FULL_LOGIN_BODY)))
        for rendered in (repr(identity), str(identity), f"{identity}"):
            self.assertNotIn(FAKE_SESSION, rendered)

    def test_login_transient_retry_then_success(self):
        responses = [FakeResponse(503), FakeResponse(json_body=dict(FULL_LOGIN_BODY))]
        with mock.patch("auth.login.requests.post", side_effect=responses) as post, \
                mock.patch("auth.login.time.sleep"), redirect_stderr(io.StringIO()):
            identity = perform_login(self.creds)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(identity.org_uuid, ORG_UUID_TEST)



# ── 3b. Login transient retry policy（补测，不改变已有 policy）──────────────
class TestLoginTransientRetry(unittest.TestCase):
    """覆盖 LOGIN_TRANSIENT_* policy：最多 3 次总尝试；400/401/403 绝不重试。

    全部 mock time.sleep，测试不真实等待。
    """

    def setUp(self):
        self.creds = CredentialConfig(make_config(), Path("memory://cfg")).get("TEST")
        self.stderr = redirect_stderr(io.StringIO())
        self.stderr.__enter__()

    def tearDown(self):
        self.stderr.__exit__(None, None, None)

    def _run(self, side_effect):
        with mock.patch("auth.login.requests.post",
                        side_effect=side_effect) as post, \
                mock.patch("auth.login.time.sleep") as sleep:
            try:
                identity = perform_login(self.creds)
                error = None
            except Exception as e:  # noqa: BLE001 — 用例自行断言异常类型
                identity, error = None, e
        return identity, error, post, sleep

    def _ok(self):
        return FakeResponse(json_body=dict(FULL_LOGIN_BODY))

    # ── Timeout ──────────────────────────────────────────────────────────
    def test_timeout_then_success(self):
        identity, error, post, sleep = self._run(
            [requests.exceptions.Timeout("t"), self._ok()])
        self.assertIsNone(error)
        self.assertEqual(identity.org_uuid, ORG_UUID_TEST)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

    def test_timeout_exhausted_raises_timeout(self):
        identity, error, post, _ = self._run(
            [requests.exceptions.Timeout("t")] * 3)
        self.assertIsInstance(error, requests.exceptions.Timeout)
        self.assertEqual(post.call_count, 3)   # 不无限 retry

    # ── ConnectionError ──────────────────────────────────────────────────
    def test_connection_error_then_success(self):
        identity, error, post, sleep = self._run(
            [requests.exceptions.ConnectionError("c"), self._ok()])
        self.assertIsNone(error)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

    def test_connection_error_exhausted(self):
        _, error, post, _ = self._run(
            [requests.exceptions.ConnectionError("c")] * 3)
        self.assertIsInstance(error, requests.exceptions.ConnectionError)
        self.assertEqual(post.call_count, 3)

    # ── 瞬时 HTTP 状态码 ─────────────────────────────────────────────────
    def test_502_then_success(self):
        identity, error, post, _ = self._run([FakeResponse(502), self._ok()])
        self.assertIsNone(error)
        self.assertEqual(post.call_count, 2)

    def test_504_then_success(self):
        identity, error, post, _ = self._run([FakeResponse(504), self._ok()])
        self.assertIsNone(error)
        self.assertEqual(post.call_count, 2)

    def test_429_then_success(self):
        identity, error, post, _ = self._run([FakeResponse(429), self._ok()])
        self.assertIsNone(error)
        self.assertEqual(post.call_count, 2)

    def test_503_retry_exhausted(self):
        _, error, post, sleep = self._run([FakeResponse(503)] * 3)
        self.assertIsInstance(error, InvalidLoginResponseError)
        self.assertIn("503", str(error))
        self.assertEqual(post.call_count, 3)          # 预定次数，不无限 retry
        self.assertEqual(sleep.call_count, 2)         # backoff 只发生在重试之间

    def test_transient_max_attempts_constant_unchanged(self):
        from auth import login as login_mod
        self.assertEqual(login_mod.LOGIN_TRANSIENT_MAX_ATTEMPTS, 3)
        self.assertEqual(set(login_mod.LOGIN_TRANSIENT_STATUS),
                         {429, 502, 503, 504})
        self.assertEqual(login_mod.LOGIN_TRANSIENT_BACKOFF, (1, 2))

    # ── 非瞬时：绝不重试（防账号锁定）──────────────────────────────────
    def test_400_no_retry(self):
        _, error, post, sleep = self._run([FakeResponse(400), self._ok()])
        self.assertIsInstance(error, InvalidLoginResponseError)
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()

    def test_401_no_retry(self):
        _, error, post, sleep = self._run([FakeResponse(401), self._ok()])
        self.assertIsInstance(error, InvalidCredentialsError)
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()

    def test_403_no_retry(self):
        _, error, post, sleep = self._run([FakeResponse(403), self._ok()])
        self.assertIsInstance(error, InvalidCredentialsError)
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()

    def test_404_no_retry(self):
        _, error, post, _ = self._run([FakeResponse(404), self._ok()])
        self.assertIsInstance(error, InvalidLoginResponseError)
        self.assertEqual(post.call_count, 1)

    def test_transient_errors_carry_no_secret(self):
        _, error, _, _ = self._run([FakeResponse(503)] * 3)
        msg = str(error)
        for secret in ("u-test", "pw-test"):
            self.assertNotIn(secret, msg)


# ── 4. §13.1 Login 响应体最小化持有 ─────────────────────────────────────────
FORBIDDEN_TOKENS = ("roles", "usergroups", "securityQuestion", "securityAnswer",
                    "someone@example.com", "favourite colour", "svc-account")


class TestLoginResponseMinimization(unittest.TestCase):
    def setUp(self):
        self.creds = CredentialConfig(make_config(), Path("memory://cfg")).get("TEST")

    def test_session_manager_does_not_hold_raw_response(self):
        with tempfile.TemporaryDirectory() as d:
            store = EnvironmentPinStore(Path(d) / ".env_pins.json")
            store.set("TEST", ORG_UUID_TEST, "000LVT")
            session = SessionManager.from_credentials("TEST", self.creds,
                                                      pin_store=store)
            with mock.patch("auth.login.requests.post",
                            return_value=FakeResponse(json_body=dict(FULL_LOGIN_BODY))):
                session.login()

            dumped = json.dumps(
                {k: repr(v) for k, v in vars(session).items()}, ensure_ascii=False)
            for token in FORBIDDEN_TOKENS:
                self.assertNotIn(token, dumped)
            self.assertNotIn(FAKE_SESSION, repr(session))
            self.assertFalse(hasattr(session, "raw"))
            self.assertFalse(hasattr(session, "login_response"))

    def test_pin_file_contains_no_secret_or_session(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".env_pins.json"
            store = EnvironmentPinStore(path)
            store.set("TEST", ORG_UUID_TEST, "000LVT")
            text = path.read_text(encoding="utf-8")
            for token in ("u-test", "pw-test", FAKE_SESSION, "password",
                          "icSessionId", "serverUrl", *FORBIDDEN_TOKENS):
                self.assertNotIn(token, text)
            self.assertIn(ORG_UUID_TEST, text)

    def test_public_identity_has_no_user_id_or_session(self):
        with mock.patch("auth.login.requests.post",
                        return_value=FakeResponse(json_body=dict(FULL_LOGIN_BODY))):
            identity = perform_login(self.creds)
        public = identity.public_identity()
        self.assertEqual(set(public), {"org_id", "org_uuid", "server_host"})
        self.assertNotIn("00ABCD", json.dumps(public))


# ── 5. Environment Pin ──────────────────────────────────────────────────────
class TestEnvironmentPin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / ".env_pins.json"
        self.store = EnvironmentPinStore(self.path)
        self.creds = CredentialConfig(make_config(), Path("memory://cfg")).get("TEST")

    def tearDown(self):
        self.tmp.cleanup()

    def _session(self):
        return SessionManager.from_credentials("TEST", self.creds,
                                               pin_store=self.store)

    def _login(self, session, org_uuid=ORG_UUID_TEST):
        body = dict(FULL_LOGIN_BODY); body["orgUuid"] = org_uuid
        with mock.patch("auth.login.requests.post",
                        return_value=FakeResponse(json_body=body)):
            session.login()

    def test_no_pin_blocks_business(self):
        session = self._session()
        with self.assertRaises(EnvironmentPinRequiredError):
            self._login(session)
        self.assertIsNone(session.session_id)   # 未 pin 时不保存 session

    def test_correct_pin_allows(self):
        self.store.set("TEST", ORG_UUID_TEST, "000LVT")
        session = self._session()
        self._login(session)
        self.assertEqual(session.session_id, FAKE_SESSION)
        self.assertEqual(session.org_uuid, ORG_UUID_TEST)

    def test_wrong_org_uuid_mismatch(self):
        self.store.set("TEST", ORG_UUID_TEST, "000LVT")
        session = self._session()
        with self.assertRaises(EnvironmentMismatchError):
            self._login(session, org_uuid=ORG_UUID_PROD)
        self.assertIsNone(session.session_id)

    def test_existing_pin_not_silently_overwritten(self):
        self.store.set("TEST", ORG_UUID_TEST, "000LVT")
        with self.assertRaises(EnvironmentMismatchError):
            self.store.set("TEST", ORG_UUID_PROD, "000LVU")
        self.assertEqual(self.store.get("TEST")["org_uuid"], ORG_UUID_TEST)

    def test_same_pin_is_idempotent(self):
        first = self.store.set("TEST", ORG_UUID_TEST, "000LVT")
        again = self.store.set("TEST", ORG_UUID_TEST, "000LVT")
        self.assertEqual(first["pinned_at"], again["pinned_at"])

    def test_force_rebind(self):
        self.store.set("TEST", ORG_UUID_TEST, "000LVT")
        self.store.set("TEST", ORG_UUID_PROD, "000LVU", force=True)
        self.assertEqual(self.store.get("TEST")["org_uuid"], ORG_UUID_PROD)

    def test_pin_record_fields_only(self):
        self.store.set("TEST", ORG_UUID_TEST, "000LVT")
        self.assertEqual(set(self.store.get("TEST")),
                         {"org_uuid", "org_id", "pinned_at"})

    def test_atomic_write_leaves_no_temp_file(self):
        self.store.set("TEST", ORG_UUID_TEST, "000LVT")
        leftovers = [p.name for p in self.path.parent.iterdir()
                     if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))
                         ["schema_version"], 1)


# ── 6. 业务请求：Header / 401 refresh / 403 / GET-only ──────────────────────
class TestBusinessRequest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EnvironmentPinStore(Path(self.tmp.name) / ".env_pins.json")
        self.store.set("TEST", ORG_UUID_TEST, "000LVT")
        self.creds = CredentialConfig(make_config(), Path("memory://cfg")).get("TEST")
        self.stderr = redirect_stderr(io.StringIO())
        self.stderr.__enter__()

    def tearDown(self):
        self.stderr.__exit__(None, None, None)
        self.tmp.cleanup()

    def _client(self, session_id=FAKE_SESSION,
                server_url="https://use4.dm-us.informaticacloud.com"):
        session = SessionManager.from_credentials("TEST", self.creds,
                                                  pin_store=self.store)
        session.session_id = session_id
        session._server_url = server_url
        session.org_uuid = ORG_UUID_TEST
        session.last_activity_at = iics_client.time.time()
        return IICSClient(session)

    @staticmethod
    def _login_response(session_id=FAKE_SESSION_2,
                        server_url="https://use4.dm-us.informaticacloud.com/saas"):
        body = dict(FULL_LOGIN_BODY)
        body["icSessionId"] = session_id
        body["serverUrl"] = server_url
        return FakeResponse(json_body=body)

    def test_v2_header(self):
        client = self._client()
        with mock.patch("iics_client.requests.request",
                        return_value=FakeResponse(json_body={})) as req:
            client._request("GET", "connection/1")
        headers = req.call_args.kwargs["headers"]
        self.assertEqual(headers["icSessionId"], FAKE_SESSION)
        self.assertNotIn("INFA-SESSION-ID", headers)

    def test_v3_header(self):
        client = self._client()
        with mock.patch("iics_client.requests.request",
                        return_value=FakeResponse(json_body={})) as req:
            client._request("GET", "public/core/v3/objects", use_v3=True)
        headers = req.call_args.kwargs["headers"]
        self.assertEqual(headers["INFA-SESSION-ID"], FAKE_SESSION)
        self.assertNotIn("icSessionId", headers)

    def test_normal_get_does_not_login_again(self):
        client = self._client()
        with mock.patch("auth.login.requests.post") as post, \
                mock.patch("iics_client.requests.request",
                           return_value=FakeResponse(json_body={})):
            client._request("GET", "connection/1")
        post.assert_not_called()

    def test_401_refresh_once_and_retry(self):
        client = self._client()
        responses = [FakeResponse(401), FakeResponse(json_body={"ok": True})]
        with mock.patch("auth.login.requests.post",
                        return_value=self._login_response()) as post, \
                mock.patch("iics_client.requests.request",
                           side_effect=responses) as req:
            resp = client._request("GET", "connection/1")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(req.call_count, 2)
        self.assertEqual(resp.json(), {"ok": True})
        self.assertEqual(req.call_args_list[1].kwargs["headers"]["icSessionId"],
                         FAKE_SESSION_2)

    def test_401_twice_raises_authentication_error(self):
        client = self._client()
        with mock.patch("auth.login.requests.post",
                        return_value=self._login_response()) as post, \
                mock.patch("iics_client.requests.request",
                           side_effect=[FakeResponse(401), FakeResponse(401)]) as req:
            with self.assertRaises(AuthenticationError):
                client._request("GET", "connection/1")
        self.assertEqual(post.call_count, 1)   # 认证 refresh 最多一次
        self.assertEqual(req.call_count, 2)

    def test_refresh_uses_new_server_url(self):
        client = self._client(server_url="https://old-pod.example.com")
        new_server = "https://use4.dm-us.informaticacloud.com"
        with mock.patch("auth.login.requests.post",
                        return_value=self._login_response(server_url=new_server)), \
                mock.patch("iics_client.requests.request",
                           side_effect=[FakeResponse(401),
                                        FakeResponse(json_body={})]) as req:
            client._request("GET", "connection/1")
        first_url = req.call_args_list[0].args[1]
        second_url = req.call_args_list[1].args[1]
        self.assertTrue(first_url.startswith("https://old-pod.example.com"))
        self.assertEqual(second_url,
                         f"{new_server}/saas/api/v2/connection/1")

    def test_403_does_not_refresh(self):
        client = self._client()
        with mock.patch("auth.login.requests.post") as post, \
                mock.patch("iics_client.requests.request",
                           return_value=FakeResponse(403)) as req:
            with self.assertRaises(PermissionDeniedError):
                client._request("GET", "connection/1")
        post.assert_not_called()
        self.assertEqual(req.call_count, 1)

    def test_legacy_token_mode_401_cannot_refresh(self):
        session = SessionManager.from_sso_token("legacy-token-value")
        client = IICSClient(session)
        with mock.patch("auth.login.requests.post") as post, \
                mock.patch("iics_client.requests.request",
                           return_value=FakeResponse(401)):
            with self.assertRaises(RuntimeError):
                client._request("GET", "connection/1")
        post.assert_not_called()

    def test_transient_retry_budget_independent_of_auth_refresh(self):
        client = self._client()
        responses = [FakeResponse(401), FakeResponse(503), FakeResponse(503),
                     FakeResponse(json_body={"ok": True})]
        with mock.patch("auth.login.requests.post",
                        return_value=self._login_response()), \
                mock.patch("iics_client.time.sleep"), \
                mock.patch("iics_client.requests.request",
                           side_effect=responses) as req:
            resp = client._request("GET", "connection/1")
        self.assertEqual(req.call_count, 4)
        self.assertEqual(resp.json(), {"ok": True})

    def test_idle_triggers_proactive_refresh(self):
        client = self._client()
        client.session.last_activity_at = (iics_client.time.time()
                                           - iics_client.IDLE_REFRESH_SECONDS - 1)
        with mock.patch("auth.login.requests.post",
                        return_value=self._login_response()) as post, \
                mock.patch("iics_client.requests.request",
                           return_value=FakeResponse(json_body={})) as req:
            client._request("GET", "connection/1")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(req.call_args.kwargs["headers"]["icSessionId"],
                         FAKE_SESSION_2)


# ── 7. 安全回归：GET-only ───────────────────────────────────────────────────
class TestSafetyRegression(unittest.TestCase):
    def setUp(self):
        self.session = SessionManager.from_sso_token("legacy-token-value")
        self.client = IICSClient(self.session)

    def test_write_methods_rejected(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "post"):
            with self.subTest(method=method):
                with mock.patch("iics_client.requests.request") as req:
                    with self.assertRaises(ValueError) as ctx:
                        self.client._request(method, "connection/1")
                    self.assertIn("only supports GET", str(ctx.exception))
                    req.assert_not_called()

    def test_no_session_cache_file_written(self):
        """v0.1 不允许跨进程 session 持久化。"""
        repo = Path(__file__).resolve().parent.parent
        for name in ("session_cache.json", "cached_session.json", ".session_cache"):
            self.assertFalse((repo / name).exists(), name)


# ── 8. 无 session 时的 header 行为 ──────────────────────────────────────────
class TestHeaderGuards(unittest.TestCase):
    def test_headers_require_session(self):
        session = SessionManager(environment="TEST")
        for fn in (session.headers_v2, session.headers_v3):
            with self.assertRaises(AuthenticationError):
                fn()


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Offline contracts for new CLI sessions; only synthetic credentials are used."""
from contextlib import contextmanager
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from hosted_auth import AuthConfig, HostedAuth, HostedAuthError, WindowsSessionStore, _NoRedirect, _dpapi


WEB, API, AUTH = "https://web.example", "https://api.example", "https://auth.example"
ACCESS, REFRESH, PASSWORD = "synthetic.access.jwt", "synthetic-refresh-token", "synthetic-password-never-store"
IDENTITY = {"authenticated": True, "user": {"userId": "user-1", "tenantId": "tenant-1", "role": "analyst", "displayName": "검증 계정"}}


def config():
    return AuthConfig(WEB, API, AUTH, "sb_publishable_syntheticfixture", AUTH + "/auth/v1")


class MemoryStore:
    def __init__(self):
        self.value, self.deleted = None, 0
        self.lock = threading.RLock()

    @contextmanager
    def locked(self):
        with self.lock:
            yield

    def load(self):
        return copy.deepcopy(self.value)

    def save(self, value):
        self.value = copy.deepcopy(value)

    def delete(self):
        self.value = None
        self.deleted += 1


class Reply:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, limit):
        return self.value if isinstance(self.value, bytes) else json.dumps(self.value).encode()[:limit]


def session(access=ACCESS, refresh=REFRESH, subject="subject-1"):
    return {"access_token": access, "refresh_token": refresh, "expires_in": 3600,
            "token_type": "bearer", "user": {"id": subject}}


class AuthFlowTest(unittest.TestCase):
    def setUp(self):
        self.store, self.opener = MemoryStore(), MagicMock()
        self.auth = HostedAuth(config(), store=self.store, opener=self.opener, clock=lambda: 1000)

    def replies(self, *values):
        self.opener.open.side_effect = [value if isinstance(value, BaseException) else Reply(value) for value in values]

    def login(self):
        self.replies(session(), IDENTITY)
        return self.auth.login_password("reviewer@example.com", PASSWORD)

    def test_fresh_login_validates_operating_identity_and_never_persists_password(self):
        public = self.login()
        requests = [call.args[0] for call in self.opener.open.call_args_list]
        self.assertEqual(requests[0].full_url, AUTH + "/auth/v1/token?grant_type=password")
        self.assertEqual(json.loads(requests[0].data), {"email": "reviewer@example.com", "password": PASSWORD})
        self.assertEqual(requests[0].get_header("Apikey"), config().publishable_key)
        self.assertEqual(requests[1].full_url, API + "/auth/me")
        self.assertEqual(requests[1].get_header("Authorization"), "Bearer " + ACCESS)
        self.assertTrue(public["authenticated"])
        self.assertEqual(public["identity"]["tenantId"], "tenant-1")
        self.assertNotIn(PASSWORD, json.dumps(self.store.value))
        self.assertNotIn(ACCESS, json.dumps(public))
        self.assertNotIn(REFRESH, json.dumps(public))
        self.assertEqual(self.auth.access_token(), ACCESS)
        self.assertEqual(len(self.opener.open.call_args_list), 2)

    def test_expiry_rotates_both_tokens_then_reuses_new_session(self):
        self.login()
        self.store.value["expiresAt"] = 1020
        self.replies(session("rotated.access.jwt", "rotated-refresh-token"), IDENTITY)
        self.assertEqual(self.auth.access_token(), "rotated.access.jwt")
        request = self.opener.open.call_args_list[-2].args[0]
        self.assertEqual(request.full_url, AUTH + "/auth/v1/token?grant_type=refresh_token")
        self.assertEqual(json.loads(request.data), {"refresh_token": REFRESH})
        self.assertEqual(self.store.value["refreshToken"], "rotated-refresh-token")
        self.assertEqual(self.auth.access_token(), "rotated.access.jwt")

    def test_force_refresh_is_supported_for_one_read_retry(self):
        self.login()
        self.replies(session("forced.access.jwt", "forced-refresh-token"), IDENTITY)
        self.assertEqual(self.auth.access_token(force_refresh=True), "forced.access.jwt")

    def test_missing_login_and_external_logout_are_observed_per_request(self):
        with self.assertRaisesRegex(HostedAuthError, "로그인이 필요"):
            self.auth.access_token()
        self.login()
        self.store.delete()
        with self.assertRaisesRegex(HostedAuthError, "로그인이 필요"):
            self.auth.access_token()

    def test_refresh_revocation_clears_owned_cache_without_leaking_provider_body(self):
        self.login()
        self.store.value["expiresAt"] = 0
        self.replies(HTTPError(AUTH, 400, REFRESH, {}, io.BytesIO(REFRESH.encode())))
        with self.assertRaisesRegex(HostedAuthError, "다시 연결") as raised:
            self.auth.access_token()
        self.assertNotIn(REFRESH, str(raised.exception))
        self.assertIsNone(self.store.value)

    def test_transient_refresh_transport_failure_does_not_destroy_saved_session(self):
        self.login()
        self.store.value["expiresAt"] = 0
        self.replies(URLError(REFRESH))
        with self.assertRaises(HostedAuthError) as raised:
            self.auth.access_token()
        self.assertNotIn(REFRESH, str(raised.exception))
        self.assertEqual(self.store.value["refreshToken"], REFRESH)

    def test_refresh_never_switches_user_or_tenant(self):
        for field in ("subject", "user", "tenant"):
            with self.subTest(field=field):
                self.login()
                changed = copy.deepcopy(IDENTITY)
                if field == "user":
                    changed["user"]["userId"] = "user-2"
                elif field == "tenant":
                    changed["user"]["tenantId"] = "tenant-2"
                self.store.value["expiresAt"] = 0
                self.replies(session(subject="subject-2" if field == "subject" else "subject-1"), changed)
                with self.assertRaisesRegex(HostedAuthError, "달라졌습니다"):
                    self.auth.access_token()
                self.assertIsNone(self.store.value)

    def test_same_process_concurrent_expiry_refreshes_only_once(self):
        self.login()
        self.store.value["expiresAt"] = 0
        self.replies(session("parallel.access.jwt", "parallel-refresh-token"), IDENTITY)
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.auth.access_token())) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results, ["parallel.access.jwt"] * 3)
        refreshes = [call.args[0] for call in self.opener.open.call_args_list if "grant_type=refresh_token" in call.args[0].full_url]
        self.assertEqual(len(refreshes), 1)

    def test_failed_membership_does_not_save_session_and_revokes_fresh_login(self):
        self.replies(session(), {"authenticated": False}, b"")
        with self.assertRaisesRegex(HostedAuthError, "사용 권한"):
            self.auth.login_password("reviewer@example.com", PASSWORD)
        self.assertIsNone(self.store.value)
        self.assertEqual(self.opener.open.call_args_list[-1].args[0].full_url, AUTH + "/auth/v1/logout?scope=local")

    def test_login_failure_is_public_and_never_contains_password_or_token(self):
        self.replies(HTTPError(AUTH, 400, PASSWORD, {}, io.BytesIO(PASSWORD.encode())))
        with self.assertRaisesRegex(HostedAuthError, "로그인 정보") as raised:
            self.auth.login_password("reviewer@example.com", PASSWORD)
        self.assertNotIn(PASSWORD, str(raised.exception))
        self.assertIsNone(self.store.value)

    def test_explicit_status_rechecks_server_identity_without_exposing_tokens(self):
        self.login()
        self.replies(IDENTITY)
        public = self.auth.status()
        self.assertTrue(public["authenticated"])
        self.assertNotIn(ACCESS, json.dumps(public))
        self.assertNotIn(REFRESH, json.dumps(public))
        self.store.delete()
        self.assertEqual(self.auth.status()["needsLogin"], True)

    def test_logout_only_revokes_cli_session_and_always_deletes_own_cache(self):
        self.login()
        self.replies(b"")
        result = self.auth.logout()
        self.assertTrue(result["serverSessionRevoked"])
        self.assertIsNone(self.store.value)
        self.assertEqual(self.opener.open.call_args_list[-1].args[0].full_url, AUTH + "/auth/v1/logout?scope=local")
        self.login()
        self.replies(URLError(ACCESS))
        result = self.auth.logout()
        self.assertTrue(result["localSessionCleared"])
        self.assertFalse(result["serverSessionRevoked"])
        self.assertNotIn(ACCESS, json.dumps(result))

    def test_no_credential_copy_or_unsupported_oauth_fallback(self):
        with self.assertRaisesRegex(HostedAuthError, "아직 검증되지"):
            self.auth.login_oauth("google")
        self.opener.open.assert_not_called()

    def test_rejects_malformed_session_and_origin_binding(self):
        self.login()
        self.store.value["binding"] = "other-origin"
        with self.assertRaisesRegex(HostedAuthError, "저장 정보"):
            self.auth.access_token()
        self.assertEqual(len(self.opener.open.call_args_list), 2)

    def test_malformed_numeric_expiry_and_malformed_provider_types_fail_safely(self):
        for value in (float("nan"), float("inf"), 10 ** 500, True):
            bad = session()
            bad["expires_in"] = value
            self.replies(bad, b"")
            with self.subTest(value=str(value)[:20]), self.assertRaises(HostedAuthError):
                self.auth.login_password("reviewer@example.com", PASSWORD)
            self.assertIsNone(self.store.value)
        bad = session()
        bad["token_type"] = 123
        self.replies(bad, b"")
        with self.assertRaises(HostedAuthError):
            self.auth.login_password("reviewer@example.com", PASSWORD)


class AuthConfigurationTest(unittest.TestCase):
    def public_config(self):
        return {"schemaVersion": "realytics-cli-config-v1", "apiOrigin": API, "supabaseUrl": AUTH,
                "issuer": AUTH + "/auth/v1", "publishableKey": config().publishable_key,
                "authMethods": ["password", "refresh_token"], "clientId": "realytics-cli"}

    def test_discovers_only_bounded_public_configuration_without_credentials(self):
        opener = MagicMock()
        opener.open.return_value = Reply(self.public_config())
        with patch.dict(os.environ, {"REALYTICS_WEB_ORIGIN": WEB, "REALYTICS_API_ORIGIN": API}):
            value = AuthConfig.from_environment(opener=opener)
        self.assertEqual(value.api_origin, API)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, WEB + "/api/cli/config")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertIsNone(request.data)

    def test_rejects_redirects_secret_keys_wrong_issuer_and_environment_confusion(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example"))
        for mutate in (lambda item: item.update(publishableKey="sb_secret_forbidden"),
                       lambda item: item.update(issuer="https://other.example/auth/v1"),
                       lambda item: item.update(apiOrigin="http://api.example"),
                       lambda item: item.update(authMethods=[{}])):
            public = self.public_config()
            mutate(public)
            opener = MagicMock()
            opener.open.return_value = Reply(public)
            with self.subTest(public=public), patch.dict(os.environ, {"REALYTICS_WEB_ORIGIN": WEB, "REALYTICS_API_ORIGIN": API}), self.assertRaises(HostedAuthError):
                AuthConfig.from_environment(opener=opener)
        opener.open.return_value = Reply(self.public_config())
        with patch.dict(os.environ, {"REALYTICS_WEB_ORIGIN": WEB, "REALYTICS_API_ORIGIN": "https://other.example"}), self.assertRaises(HostedAuthError):
            AuthConfig.from_environment(opener=opener)

    def test_origin_binding_changes_for_each_trusted_endpoint(self):
        values = [config().binding,
                  AuthConfig("https://other.example", API, AUTH, config().publishable_key, AUTH + "/auth/v1").binding,
                  AuthConfig(WEB, "https://other.example", AUTH, config().publishable_key, AUTH + "/auth/v1").binding]
        self.assertEqual(len(set(values)), 3)

    def test_malformed_public_origin_types_fail_with_sanitized_auth_error(self):
        for key in ("apiOrigin", "supabaseUrl"):
            for malformed in (123, [API], {"origin": API}, None, "", "a" * 2049):
                public = self.public_config()
                public[key] = malformed
                opener = MagicMock()
                opener.open.return_value = Reply(public)
                with self.subTest(key=key, type=type(malformed).__name__), patch.dict(os.environ, {"REALYTICS_WEB_ORIGIN": WEB, "REALYTICS_API_ORIGIN": API}), self.assertRaises(HostedAuthError):
                    AuthConfig.from_environment(opener=opener)


@unittest.skipUnless(os.name == "nt", "Native current-user DPAPI only executes on Windows")
class NativeEncryptedStoreTest(unittest.TestCase):
    def test_owned_session_is_encrypted_on_disk_bound_to_origin_and_deletable(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store = WindowsSessionStore(config().binding, directory=directory)
            record = {"schemaVersion": "realytics-cli-session-v1", "binding": config().binding,
                      "accessToken": ACCESS, "refreshToken": REFRESH}
            with store.locked():
                store.save(record)
                raw = store.path.read_bytes()
                self.assertNotIn(ACCESS.encode(), raw)
                self.assertNotIn(REFRESH.encode(), raw)
                self.assertEqual(store.load(), record)
            with self.assertRaises(HostedAuthError):
                _dpapi(raw, b"other-origin-binding", decrypt=True)
            with store.locked():
                store.delete()
                self.assertIsNone(store.load())
            self.assertFalse(list(directory.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()

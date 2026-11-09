"""Native loopback roundtrips with synthetic provider responses only."""
import base64
import hashlib
from http.client import HTTPConnection
import io
import json
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit

from hosted_auth import AuthConfig, HostedAuthError
import hosted_oauth as oauth


AUTH = "https://auth.example"
CODE = "synthetic-code-one-use"
ACCESS, REFRESH = "synthetic.access.jwt", "synthetic-refresh"


def config():
    return AuthConfig("https://web.example", "https://api.example", AUTH,
                      "sb_publishable_syntheticfixture", AUTH + "/auth/v1")


def google_url(**changes):
    query = {"client_id": "synthetic-client.apps.googleusercontent.com", "response_type": "code",
             "redirect_uri": AUTH + "/auth/v1/callback", "state": "synthetic-opaque-provider-state"}
    query.update(changes)
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(query)


def redirect(location=None):
    return HTTPError(AUTH, 302, "Found", {"Location": location or google_url()}, io.BytesIO(b""))


def provider_error(status=400):
    return HTTPError(AUTH, status, ACCESS, {}, io.BytesIO(REFRESH.encode()))


def session():
    return {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3600,
            "token_type": "bearer", "user": {"id": "synthetic-subject"}}


class Reply:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, limit):
        return json.dumps(self.value).encode()[:limit]


class GoogleLoopbackTest(unittest.TestCase):
    def setUp(self):
        self.servers, self.browser_urls, self.responses, self.threads = [], [], [], []
        self.real_server = oauth._LoopbackServer
        self.opener = MagicMock()

    def tearDown(self):
        for server in self.servers:
            server.server_close()
        for thread in self.threads:
            thread.join(2)

    def server(self, address, handler):
        self.assertEqual(address, ("127.0.0.1", 3000))
        server = self.real_server(("127.0.0.1", 0), handler)
        self.servers.append(server)
        return server

    def browser(self, callbacks):
        def opened(url, **_):
            self.browser_urls.append(url)

            def send():
                for item in callbacks:
                    path, extra_headers, method = item if isinstance(item, tuple) else (item, [], "GET")
                    connection = HTTPConnection("127.0.0.1", self.servers[-1].server_port, timeout=2)
                    try:
                        connection.putrequest(method, path, skip_host=True)
                        headers = [("Host", "127.0.0.1:3000"), *extra_headers]
                        for key, value in headers:
                            connection.putheader(key, value)
                        connection.endheaders()
                        response = connection.getresponse()
                        self.responses.append((response.status, dict(response.getheaders()), response.read().decode()))
                    finally:
                        connection.close()
            thread = threading.Thread(target=send, daemon=True)
            self.threads.append(thread)
            thread.start()
            return True
        return opened

    def run_login(self, callbacks, token_results=None, *, timeout=1):
        self.opener.open.side_effect = [redirect(), *(Reply(value) if isinstance(value, dict) else value
                                                    for value in (token_results if token_results is not None else [session()]))]
        with patch.object(oauth, "_LoopbackServer", side_effect=self.server), patch.object(
                oauth.webbrowser, "open", side_effect=self.browser(callbacks)):
            try:
                return oauth.authorize_google(config(), opener=self.opener, timeout_seconds=timeout)
            finally:
                for thread in self.threads:
                    thread.join(2)

    def test_native_callback_exchanges_fresh_s256_with_fixed_issuer_and_no_cookie_copy(self):
        result = self.run_login(["/auth/callback?code=" + CODE])
        self.assertEqual(result, session())
        self.assertEqual(len(self.opener.open.call_args_list), 2)
        start, exchange = [call.args[0] for call in self.opener.open.call_args_list]
        query = parse_qs(urlsplit(start.full_url).query)
        payload = json.loads(exchange.data)
        self.assertEqual(query["provider"], ["google"])
        self.assertEqual(query["redirect_to"], ["http://127.0.0.1:3000/auth/callback"])
        self.assertEqual(query["prompt"], ["select_account"])
        self.assertEqual(query["code_challenge_method"], ["s256"])
        self.assertEqual(payload["auth_code"], CODE)
        verifier = payload["code_verifier"]
        self.assertGreaterEqual(len(verifier), 43)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertNotIn(verifier, start.full_url)
        self.assertEqual(exchange.full_url, AUTH + "/auth/v1/token?grant_type=pkce")
        self.assertIsNone(exchange.get_header("Cookie"))
        self.assertIsNone(exchange.get_header("Authorization"))
        status, headers, body = self.responses[0]
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        for sensitive in (CODE, ACCESS, REFRESH, verifier):
            self.assertNotIn(sensitive, body)
        self.assertEqual(self.servers[0].socket.fileno(), -1)

    def test_wrong_path_host_origin_duplicates_unknown_parameters_and_issuer_never_exchange(self):
        invalid = [
            "/elsewhere?code=" + CODE,
            ("/auth/callback?code=" + CODE, [("Host", "attacker.example")], "GET"),
            ("/auth/callback?code=" + CODE, [("Origin", "https://attacker.example")], "GET"),
            ("/auth/callback?code=" + CODE, [], "POST"),
            "/auth/callback?code=" + CODE + "&code=other-code",
            "/auth/callback?code=" + CODE + "&access_token=" + ACCESS,
            "/auth/callback?code=" + CODE + "&iss=https%3A%2F%2Fattacker.example",
            "/auth/callback?code=" + "a" * 2049,
            "/auth/callback?code=" + CODE + "&error=access_denied",
            "/auth/callback?code=" + CODE + "&error=",
        ]
        result = self.run_login([*invalid, "/auth/callback?code=" + CODE])
        self.assertEqual(result, session())
        self.assertEqual(len(self.opener.open.call_args_list), 2)
        self.assertTrue(all(status in {400, 405} for status, _, _ in self.responses[:-1]))
        self.assertEqual(self.responses[-1][0], 200)

    def test_unrelated_code_is_rejected_by_pkce_without_consuming_the_pending_login(self):
        result = self.run_login(["/auth/callback?code=attacker-wrong-code", "/auth/callback?code=" + CODE],
                                [provider_error(), session()])
        self.assertEqual(result, session())
        self.assertEqual([status for status, _, _ in self.responses], [400, 200])
        payloads = [json.loads(call.args[0].data) for call in self.opener.open.call_args_list[1:]]
        self.assertEqual(payloads[0]["code_verifier"], payloads[1]["code_verifier"])

    def test_repeated_invalid_codes_stop_with_credential_free_failure(self):
        with self.assertRaises(HostedAuthError) as raised:
            self.run_login(["/auth/callback?code=invalid-code-one"] * 3, [provider_error()] * 3)
        self.assertNotIn(ACCESS, str(raised.exception))
        self.assertNotIn(REFRESH, str(raised.exception))
        self.assertEqual([status for status, _, _ in self.responses], [400] * 3)

    def test_provider_denial_never_exchanges_or_claims_success(self):
        with self.assertRaisesRegex(HostedAuthError, "취소되거나"):
            self.run_login(["/auth/callback?error=access_denied&error_description=" + ACCESS], [])
        self.assertEqual(len(self.opener.open.call_args_list), 1)
        self.assertNotIn(ACCESS, self.responses[0][2])

    def test_transient_exchange_failure_is_sanitized_and_closes_listener(self):
        with self.assertRaises(HostedAuthError) as raised:
            self.run_login(["/auth/callback?code=" + CODE], [provider_error(503)])
        self.assertNotIn(ACCESS, str(raised.exception))
        self.assertNotIn(REFRESH, str(raised.exception))
        self.assertEqual(self.servers[0].socket.fileno(), -1)

    def test_missing_or_web_fallback_callback_times_out_without_claiming_allowlist_verification(self):
        self.opener.open.side_effect = [redirect()]
        with patch.object(oauth, "_LoopbackServer", side_effect=self.server), patch.object(
                oauth.webbrowser, "open", return_value=True), self.assertRaisesRegex(HostedAuthError, "복귀 응답"):
            oauth.authorize_google(config(), opener=self.opener, timeout_seconds=0.02)
        self.assertEqual(len(self.opener.open.call_args_list), 1)
        self.assertEqual(self.servers[0].socket.fileno(), -1)

    def test_busy_port_fails_before_authorize_or_browser_and_never_chooses_another_interface(self):
        with patch.object(oauth, "_LoopbackServer", side_effect=OSError(ACCESS)), patch.object(
                oauth.webbrowser, "open") as browser, self.assertRaisesRegex(HostedAuthError, "복귀 포트") as raised:
            oauth.authorize_google(config(), opener=self.opener)
        self.opener.open.assert_not_called()
        browser.assert_not_called()
        self.assertNotIn(ACCESS, str(raised.exception))

    def test_browser_failure_closes_exclusive_listener(self):
        self.opener.open.side_effect = [redirect()]
        with patch.object(oauth, "_LoopbackServer", side_effect=self.server), patch.object(
                oauth.webbrowser, "open", return_value=False), self.assertRaisesRegex(HostedAuthError, "브라우저"):
            oauth.authorize_google(config(), opener=self.opener)
        self.assertEqual(self.servers[0].socket.fileno(), -1)

    def test_rejects_untrusted_provider_redirects_without_launching_a_browser(self):
        invalid = ["https://attacker.example/authorize?state=synthetic",
                   google_url(redirect_uri="https://attacker.example/callback"),
                   google_url(response_type="token"), google_url(state=""),
                   google_url() + "&redirect_uri=" + AUTH + "/auth/v1/callback",
                   google_url().replace("accounts.google.com", "accounts.google.com.attacker.example"),
                   google_url().replace("https://", "http://")]
        for location in invalid:
            with self.subTest(location=location):
                opener = MagicMock()
                opener.open.side_effect = redirect(location)
                with patch.object(oauth.webbrowser, "open") as browser, self.assertRaises(HostedAuthError):
                    oauth._google_redirect(opener, config(), "synthetic-challenge")
                browser.assert_not_called()

    def test_invalid_timeout_cannot_create_an_unbounded_listener(self):
        for value in (0, -1, 301, float("nan"), float("inf"), True, "300"):
            with self.subTest(value=value), patch.object(oauth, "_LoopbackServer") as server, self.assertRaises(HostedAuthError):
                oauth.authorize_google(config(), opener=self.opener, timeout_seconds=value)
            server.assert_not_called()


if __name__ == "__main__":
    unittest.main()

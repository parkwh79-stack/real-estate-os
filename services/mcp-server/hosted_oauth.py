"""Fresh Google PKCE login through the user's browser and an exclusive loopback.

No browser session is inspected. The verifier and authorization response exist
only in this process; the caller verifies REALYTICS membership before storing
the resulting session. RFC 9700 permits PKCE CSRF protection for an AS known to
support PKCE. There is deliberately no implicit or password fallback.
"""
from __future__ import annotations

import base64
import hashlib
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, HTTPServer
import re
import secrets
import socket
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, build_opener
import webbrowser

from hosted_auth import HostedAuthError, _AuthHttpError, _NoRedirect, _request_json


LOOPBACK_HOST = "127.0.0.1"
LOOPBACK_PORT = 3000
CALLBACK_PATH = "/auth/callback"
CALLBACK_ORIGIN = f"http://{LOOPBACK_HOST}:{LOOPBACK_PORT}"
CALLBACK_URL = CALLBACK_ORIGIN + CALLBACK_PATH
_CODE = re.compile(r"[A-Za-z0-9._~-]{8,2048}\Z")


class _LoopbackServer(HTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address

    def handle_error(self, request, client_address):
        # BaseServer's default traceback can include a request path/code.
        pass


def _google_redirect(opener, config, challenge: str) -> str:
    parameters = {"provider": "google", "redirect_to": CALLBACK_URL,
                  "code_challenge": challenge, "code_challenge_method": "s256",
                  "prompt": "select_account"}
    request = Request(config.supabase_url + "/auth/v1/authorize?" + urlencode(parameters),
                      headers={"apikey": config.publishable_key, "Accept": "text/html"})
    location = None
    try:
        with opener.open(request, timeout=30):
            pass
    except HTTPError as error:
        if error.code == 302:
            location = error.headers.get("Location")
        error.close()
    except (OSError, HTTPException, ValueError):
        raise HostedAuthError("Google 로그인 시작 서버에 연결하지 못했습니다. 다시 시도하세요.") from None
    try:
        if not isinstance(location, str) or not 1 <= len(location) <= 16_384:
            raise ValueError
        parsed = urlsplit(location)
        query = parse_qs(parsed.query, strict_parsing=True, max_num_fields=32)
        if (parsed.scheme != "https" or parsed.netloc != "accounts.google.com"
                or parsed.path not in {"/o/oauth2/auth", "/o/oauth2/v2/auth"}
                or parsed.fragment or any(character.isspace() or ord(character) < 32 for character in location)
                or query.get("redirect_uri") != [config.supabase_url + "/auth/v1/callback"]
                or query.get("response_type") != ["code"]
                or len(query.get("client_id", [])) != 1 or not query["client_id"][0]
                or len(query.get("state", [])) != 1 or not query["state"][0]):
            raise ValueError
    except (ValueError, TypeError):
        raise HostedAuthError("Google 로그인 시작 주소를 안전하게 확인하지 못했습니다. 설정을 확인하세요.") from None
    return location


def authorize_google(config, opener=None, timeout_seconds=300) -> dict:
    """Return a fresh PKCE session; membership/cache writes belong to the caller.

    A provider redirect alone does not prove the live redirect allowlist. Only
    arrival at this loopback and successful code exchange completes this step.
    """
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not 0 < timeout_seconds <= 300):
        raise HostedAuthError("Google 로그인 대기 시간 설정을 확인하세요.")
    transport = opener or build_opener(_NoRedirect())
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    deadline = time.monotonic() + timeout_seconds
    outcome = {"session": None, "error": None, "rejectedCodes": 0}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, message):
            body = ("<!doctype html><html lang=ko><meta charset=utf-8><title>REALYTICS 연결</title>"
                    "<body><p>" + message + "</p></body></html>").encode("utf-8")
            self.send_response(status)
            for key, value in {"Content-Type": "text/html; charset=utf-8", "Content-Length": str(len(body)),
                               "Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer",
                               "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
                               "X-Content-Type-Options": "nosniff", "Connection": "close"}.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def send_error(self, code, message=None, explain=None):
            self.reply(code, "허용되지 않은 요청입니다.")

        def do_POST(self):
            self.reply(405, "허용되지 않은 요청입니다.")

        def do_GET(self):
            try:
                if (self.client_address[0] != LOOPBACK_HOST
                        or self.headers.get_all("Host", []) != [f"{LOOPBACK_HOST}:{LOOPBACK_PORT}"]
                        or len(self.headers.get_all("Origin", [])) > 1
                        or self.headers.get("Origin") not in (None, config.supabase_url, CALLBACK_ORIGIN)
                        or len(self.path) > 8192):
                    raise ValueError
                parsed = urlsplit(self.path)
                if parsed.scheme or parsed.netloc or parsed.path != CALLBACK_PATH or parsed.fragment:
                    raise ValueError
                query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=8)
                if set(query) - {"code", "error", "error_code", "error_description", "iss"}:
                    raise ValueError
                if any(len(values) != 1 for values in query.values()):
                    raise ValueError
                if query.get("iss", [config.issuer]) != [config.issuer]:
                    raise ValueError
                if query.get("error") and "code" not in query:
                    outcome["error"] = "Google 로그인이 취소되거나 거절됐습니다. 다시 연결하세요."
                    self.reply(400, "로그인이 완료되지 않았습니다. 연결 창에서 다시 시도하세요.")
                    return
                code = query.get("code", [None])[0]
                if "error" in query or not isinstance(code, str) or not _CODE.fullmatch(code):
                    raise ValueError
            except (ValueError, TypeError):
                self.reply(400, "로그인 응답을 확인할 수 없습니다.")
                return
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise HostedAuthError("Google 로그인 대기 시간이 끝났습니다. 다시 연결하세요.")
                response = _request_json(transport, config.supabase_url + "/auth/v1/token?grant_type=pkce",
                                         payload={"auth_code": code, "code_verifier": verifier},
                                         headers={"apikey": config.publishable_key}, timeout=max(1, min(30, remaining)))
            except _AuthHttpError as error:
                outcome["rejectedCodes"] += 1
                if error.status not in {400, 401, 403, 422} or outcome["rejectedCodes"] >= 3:
                    outcome["error"] = "Google 인증코드 교환을 완료하지 못했습니다. 다시 연결하세요."
                self.reply(400, "인증코드를 확인할 수 없습니다. 연결 창의 결과를 확인하세요.")
                return
            except HostedAuthError:
                outcome["error"] = "Google 로그인 서버 연결을 완료하지 못했습니다. 다시 연결하세요."
                self.reply(503, "인증 서버 연결을 완료하지 못했습니다. 연결 창의 결과를 확인하세요.")
                return
            outcome["session"] = response
            self.reply(200, "인증 응답을 받았습니다. 연결 창에서 REALYTICS 권한 확인 결과를 확인하세요.")

    try:
        server = _LoopbackServer((LOOPBACK_HOST, LOOPBACK_PORT), Callback)
    except OSError:
        raise HostedAuthError("CLI 로그인 복귀 포트(127.0.0.1:3000)를 사용할 수 없습니다. 해당 포트를 쓰는 프로그램을 확인한 뒤 다시 연결하세요.") from None
    try:
        redirect = _google_redirect(transport, config, challenge)
        try:
            opened = webbrowser.open(redirect, new=1, autoraise=True)
        except Exception:
            opened = False
        if not opened:
            raise HostedAuthError("Google 로그인 브라우저를 열지 못했습니다. 기본 브라우저 설정을 확인하세요.")
        while outcome["session"] is None and outcome["error"] is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HostedAuthError("Google 로그인 복귀 응답을 받지 못했습니다. 브라우저 로그인과 운영 복귀 주소 설정을 확인하고 다시 연결하세요.")
            server.timeout = min(0.5, remaining)
            server.handle_request()
        if outcome["error"] is not None:
            raise HostedAuthError(outcome["error"])
        return outcome["session"]
    finally:
        verifier = ""
        server.server_close()

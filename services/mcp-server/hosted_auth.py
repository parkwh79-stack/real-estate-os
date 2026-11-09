"""Fresh CLI sessions for the existing hosted identity; no browser-cookie export.

The only persistent credentials are sessions created through this module. On
Windows they are encrypted with current-user DPAPI. No plaintext fallback is
provided, and LLM/API provider credentials are never used here.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


DEFAULT_WEB_ORIGIN = "https://realytics-web-git-codex-parser-v2-postgr-05604e-real-estate-os2.vercel.app"
MAX_AUTH_BYTES = 128 * 1024
_TOKEN = re.compile(r"[A-Za-z0-9._~+/-]+=*\Z")


class HostedAuthError(RuntimeError):
    """A public, credential-free failure safe for a CLI or MCP response."""


class _InvalidSession(HostedAuthError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _https_origin(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise HostedAuthError("운영 HTTPS 주소 설정을 확인하세요.")
    try:
        parsed = urlsplit(value)
        parsed.port
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment or "\\" in value
                or any(character.isspace() or ord(character) < 32 for character in value)):
            raise ValueError
    except (ValueError, TypeError):
        raise HostedAuthError("운영 HTTPS 주소 설정을 확인하세요.") from None
    return value.rstrip("/")


def _valid_token(value) -> bool:
    return isinstance(value, str) and 8 <= len(value) <= 16_384 and bool(_TOKEN.fullmatch(value))


def _request_json(opener, url: str, *, payload: dict | None = None, headers: dict | None = None,
                  timeout: int = 30, empty_ok: bool = False) -> dict:
    request_headers = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=None if payload is None else json.dumps(payload).encode(),
                      headers=request_headers, method="GET" if payload is None else "POST")
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_AUTH_BYTES + 1)
    except HTTPError as error:
        status = error.code
        error.close()
        raise _AuthHttpError(status) from None
    except (OSError, HTTPException, ValueError):
        raise HostedAuthError("운영 로그인 서버에 연결하지 못했습니다. 잠시 뒤 다시 시도하세요.") from None
    if len(raw) > MAX_AUTH_BYTES:
        raise HostedAuthError("로그인 서버의 응답 크기를 확인할 수 없습니다.")
    if not raw and empty_ok:
        return {}
    try:
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, UnicodeError):
        raise HostedAuthError("로그인 서버의 응답 형식을 확인할 수 없습니다.") from None


class _AuthHttpError(HostedAuthError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"로그인 서버가 요청을 처리하지 못했습니다. (HTTP {status})")


@dataclass(frozen=True)
class AuthConfig:
    web_origin: str
    api_origin: str
    supabase_url: str
    publishable_key: str
    issuer: str
    client_id: str = "realytics-cli"

    def __post_init__(self):
        for field in ("web_origin", "api_origin", "supabase_url"):
            object.__setattr__(self, field, _https_origin(getattr(self, field)))
        if self.issuer != self.supabase_url + "/auth/v1":
            raise HostedAuthError("운영 로그인 발급자 설정이 일치하지 않습니다.")
        if not isinstance(self.publishable_key, str) or not re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]{8,256}", self.publishable_key):
            raise HostedAuthError("CLI 로그인에는 공개 publishable 키만 사용할 수 있습니다.")
        if self.client_id != "realytics-cli":
            raise HostedAuthError("CLI 연결 식별자가 일치하지 않습니다.")

    @property
    def binding(self) -> str:
        return hashlib.sha256(json.dumps({"web": self.web_origin, "api": self.api_origin,
            "issuer": self.issuer, "client": self.client_id}, sort_keys=True).encode()).hexdigest()

    @classmethod
    def from_environment(cls, *, opener=None):
        web = _https_origin(os.environ.get("REALYTICS_WEB_ORIGIN") or DEFAULT_WEB_ORIGIN)
        try:
            config = _request_json(opener or build_opener(_NoRedirect()), web + "/api/cli/config")
        except _AuthHttpError:
            raise HostedAuthError("운영 CLI 연결 설정을 불러오지 못했습니다. 웹 주소와 배포 상태를 확인하세요.") from None
        if config.get("schemaVersion") != "realytics-cli-config-v1":
            raise HostedAuthError("운영 CLI 연결 설정 형식이 일치하지 않습니다.")
        methods = config.get("authMethods")
        if not isinstance(methods, list) or not all(isinstance(method, str) for method in methods) or not {"password", "refresh_token"}.issubset(set(methods)):
            raise HostedAuthError("이 운영 환경에서는 CLI 로그인이 준비되지 않았습니다.")
        instance = cls(web_origin=web, api_origin=config.get("apiOrigin"), supabase_url=config.get("supabaseUrl"),
                       publishable_key=config.get("publishableKey"), issuer=config.get("issuer"),
                       client_id=config.get("clientId", "realytics-cli"))
        explicit_api = os.environ.get("REALYTICS_API_ORIGIN")
        if explicit_api and _https_origin(explicit_api) != instance.api_origin:
            raise HostedAuthError("CLI의 API 주소와 운영 로그인 설정이 다릅니다. 같은 운영 환경을 선택하세요.")
        return instance


def _dpapi(value: bytes, entropy: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise HostedAuthError("현재 CLI의 영구 로그인 저장은 Windows 사용자 보안 저장소에서 지원합니다.")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    data_buffer, entropy_buffer = ctypes.create_string_buffer(value), ctypes.create_string_buffer(entropy)
    source = Blob(len(value), ctypes.cast(data_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    extra = Blob(len(entropy), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    destination = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                          ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
    if not operation(ctypes.byref(source), None, ctypes.byref(extra), None, None, 1, ctypes.byref(destination)):
        raise HostedAuthError("Windows 사용자 보안 저장소를 열지 못했습니다. 현재 사용자로 다시 로그인하세요.")
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        ctypes.memset(destination.pbData, 0, destination.cbData)
        kernel.LocalFree(destination.pbData)
        ctypes.memset(data_buffer, 0, len(value))


class WindowsSessionStore:
    """A single app-owned, current-user encrypted record per operating origin."""
    def __init__(self, binding: str, *, directory: Path | None = None):
        self.binding = binding
        self.directory = directory or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "REALYTICS" / "cli"
        self.path = self.directory / (binding + ".session")
        self._thread_lock = threading.RLock()

    def _directory(self):
        paths = (self.path, self.directory, *self.directory.parents)
        if any(path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()) for path in paths):
            raise HostedAuthError("CLI 로그인 저장소의 경로를 안전하게 확인할 수 없습니다.")
        self.directory.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def locked(self):
        self._directory()
        with self._thread_lock:
            lock_path = self.directory / (self.binding + ".lock")
            if lock_path.is_symlink():
                raise HostedAuthError("CLI 로그인 저장소의 잠금 경로를 확인할 수 없습니다.")
            with lock_path.open("a+b") as stream:
                if stream.tell() == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    acquired = False
                    for _ in range(100):
                        try:
                            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                            acquired = True
                            break
                        except OSError:
                            time.sleep(0.1)
                    if not acquired:
                        raise HostedAuthError("다른 CLI가 로그인 세션을 갱신 중입니다. 잠시 뒤 다시 시도하세요.")
                    try:
                        yield
                    finally:
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    # No persistence is permitted on non-Windows; this branch
                    # only lets an empty status report absence before login.
                    yield

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            self._directory()
            with self.path.open("rb") as stream:
                encrypted = stream.read(MAX_AUTH_BYTES + 1)
            if len(encrypted) > MAX_AUTH_BYTES:
                raise ValueError
            record = json.loads(_dpapi(encrypted, self.binding.encode(), decrypt=True))
            if not isinstance(record, dict) or record.get("binding") != self.binding or record.get("schemaVersion") != "realytics-cli-session-v1":
                raise ValueError
            return record
        except HostedAuthError:
            raise
        except (OSError, ValueError):
            raise HostedAuthError("CLI 로그인 저장 정보를 확인할 수 없습니다. 다시 로그인하세요.") from None

    def save(self, record: dict):
        if record.get("binding") != self.binding:
            raise HostedAuthError("다른 운영 환경의 로그인 정보를 저장할 수 없습니다.")
        raw = json.dumps(record).encode()
        if len(raw) > MAX_AUTH_BYTES // 2:
            raise HostedAuthError("CLI 로그인 저장 정보의 크기를 확인할 수 없습니다.")
        encrypted = _dpapi(raw, self.binding.encode(), decrypt=False)
        self._directory()
        temporary = self.directory / (self.binding + "." + secrets.token_hex(8) + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(encrypted)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def delete(self):
        self._directory()
        self.path.unlink(missing_ok=True)


class HostedAuth:
    def __init__(self, config: AuthConfig, *, store=None, opener=None, clock=time.time):
        self.config = config
        self.api_origin = config.api_origin
        self.store = store or WindowsSessionStore(config.binding)
        self._opener = opener or build_opener(_NoRedirect())
        self._clock = clock

    @classmethod
    def from_environment(cls):
        return cls(AuthConfig.from_environment())

    def _auth(self, path: str, *, payload: dict | None = None, token: str | None = None,
              empty_ok: bool = False) -> dict:
        return _request_json(self._opener, self.config.supabase_url + "/auth/v1/" + path,
                             payload=payload, headers={"apikey": self.config.publishable_key,
                             **({"Authorization": "Bearer " + token} if token else {})}, empty_ok=empty_ok)

    def _identity(self, token: str) -> dict:
        result = _request_json(self._opener, self.api_origin + "/auth/me",
                               headers={"Authorization": "Bearer " + token}, timeout=120)
        user = result.get("user")
        if (result.get("authenticated") is not True or not isinstance(user, dict)
                or not all(isinstance(user.get(key), str) and 1 <= len(user[key]) <= 256 for key in ("userId", "tenantId"))):
            raise HostedAuthError("이 계정의 REALYTICS 사용 권한을 확인하지 못했습니다. 운영 사이트에서 로그인 상태를 확인하세요.")
        return {"userId": str(user["userId"]), "tenantId": str(user["tenantId"]),
                "displayName": str(user.get("displayName") or "")[:160], "role": str(user.get("role") or "")[:80]}

    def _session(self, response: dict, *, previous: dict | None = None) -> dict:
        access, refresh = response.get("access_token"), response.get("refresh_token")
        duration = response.get("expires_in")
        user = response.get("user")
        if (not _valid_token(access) or not _valid_token(refresh) or isinstance(duration, bool)
                or not isinstance(duration, (int, float)) or not 1 <= duration <= 604800
                or not isinstance(response.get("token_type", "bearer"), str)
                or response.get("token_type", "bearer").lower() != "bearer"
                or not isinstance(user, dict) or not isinstance(user.get("id"), str) or not user["id"]):
            raise _InvalidSession("로그인 서버가 유효한 세션을 반환하지 않았습니다.")
        identity = self._identity(access)
        if previous and (previous.get("authSubject") != user["id"] or previous.get("identity", {}).get("userId") != identity["userId"]
                         or previous.get("identity", {}).get("tenantId") != identity["tenantId"]):
            raise _InvalidSession("세션 갱신 중 계정 또는 작업 공간이 달라졌습니다. 다시 로그인하세요.")
        return {"schemaVersion": "realytics-cli-session-v1", "binding": self.config.binding,
                "authSubject": user["id"], "accessToken": access, "refreshToken": refresh,
                "expiresAt": self._clock() + duration, "identity": identity}

    def login_password(self, email: str, password: str) -> dict:
        if not isinstance(email, str) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 320:
            raise HostedAuthError("로그인 이메일을 확인하세요.")
        if not isinstance(password, str) or not 1 <= len(password) <= 4096:
            raise HostedAuthError("비밀번호를 확인하세요.")
        response = {}
        try:
            response = self._auth("token?grant_type=password", payload={"email": email, "password": password})
            record = self._session(response)
            with self.store.locked():
                self.store.save(record)
        except _AuthHttpError as error:
            self._discard_new_session(response)
            if error.status in {400, 401, 403, 422}:
                raise HostedAuthError("로그인 정보 또는 계정 사용 상태를 확인하세요. Google로 가입한 계정은 login --provider google을 사용하세요. 비밀번호는 저장하지 않았습니다.") from None
            raise HostedAuthError("로그인을 완료하지 못했습니다. 잠시 뒤 다시 시도하세요.") from None
        except HostedAuthError:
            self._discard_new_session(response)
            raise
        return self._public_status(record)

    def _discard_new_session(self, response: dict):
        token = response.get("access_token")
        if _valid_token(token):
            try:
                self._auth("logout?scope=local", payload={}, token=token, empty_ok=True)
            except HostedAuthError:
                pass

    def login_oauth(self, provider: str) -> dict:
        if provider != "google":
            raise HostedAuthError("현재 CLI 소셜 로그인은 Google만 지원합니다. 다른 로그인은 운영 웹을 사용하세요.")
        from hosted_oauth import authorize_google

        response = {}
        try:
            response = authorize_google(self.config, opener=self._opener)
            record = self._session(response)
            with self.store.locked():
                self.store.save(record)
        except _AuthHttpError:
            self._discard_new_session(response)
            raise HostedAuthError("Google 로그인 후 REALYTICS 사용 권한을 확인하지 못했습니다. 운영 웹에서 같은 계정을 확인하세요.") from None
        except HostedAuthError:
            self._discard_new_session(response)
            raise
        return self._public_status(record)

    def access_token(self, force_refresh: bool = False) -> str:
        with self.store.locked():
            record = self.store.load()
            if record is None:
                raise HostedAuthError("CLI 로그인이 필요합니다. realytics_cli.py login으로 연결하세요.")
            if record.get("binding") != self.config.binding or not _valid_token(record.get("accessToken")) or not _valid_token(record.get("refreshToken")):
                raise HostedAuthError("CLI 로그인 저장 정보를 확인할 수 없습니다. 다시 로그인하세요.")
            expiry = record.get("expiresAt")
            if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not 0 <= expiry <= 1_000_000_000_000:
                raise HostedAuthError("CLI 세션 만료 정보를 확인할 수 없습니다. 다시 로그인하세요.")
            if not force_refresh and expiry > self._clock() + 60:
                return record["accessToken"]
            try:
                response = self._auth("token?grant_type=refresh_token", payload={"refresh_token": record["refreshToken"]})
                updated = self._session(response, previous=record)
            except _AuthHttpError as error:
                if error.status in {400, 401, 403, 422}:
                    self.store.delete()
                    raise HostedAuthError("CLI 로그인 세션이 만료되거나 해제됐습니다. realytics_cli.py login으로 다시 연결하세요.") from None
                raise HostedAuthError("CLI 세션을 갱신하지 못했습니다. 잠시 뒤 다시 시도하세요.") from None
            except _InvalidSession:
                self.store.delete()
                raise
            self.store.save(updated)
            return updated["accessToken"]

    @staticmethod
    def _public_status(record: dict) -> dict:
        identity = record.get("identity") or {}
        return {"schemaVersion": "realytics-cli-auth-status-v1", "authenticated": True,
                "expiresAt": int(record["expiresAt"]), "identity": {key: identity.get(key, "") for key in ("userId", "tenantId", "displayName", "role")},
                "credentialStorage": "windows_current_user_dpapi"}

    def status(self) -> dict:
        with self.store.locked():
            present = self.store.load()
        if present is None:
            return {"schemaVersion": "realytics-cli-auth-status-v1", "authenticated": False, "needsLogin": True}
        token = self.access_token()
        try:
            identity = self._identity(token)
        except _AuthHttpError as error:
            if error.status != 401:
                raise HostedAuthError("현재 계정의 운영 접근 권한을 확인하지 못했습니다.") from None
            token = self.access_token(force_refresh=True)
            try:
                identity = self._identity(token)
            except _AuthHttpError:
                raise HostedAuthError("현재 계정의 운영 접근 권한을 확인하지 못했습니다. 다시 로그인하세요.") from None
        with self.store.locked():
            record = self.store.load()
        if record is None or record.get("identity", {}).get("userId") != identity["userId"] or record.get("identity", {}).get("tenantId") != identity["tenantId"]:
            raise HostedAuthError("현재 CLI 계정 또는 작업 공간이 달라졌습니다. 다시 로그인하세요.")
        return self._public_status(record)

    def logout(self) -> dict:
        revoked = False
        with self.store.locked():
            record = self.store.load()
            try:
                if record and _valid_token(record.get("accessToken")):
                    self._auth("logout?scope=local", payload={}, token=record["accessToken"], empty_ok=True)
                    revoked = True
            except HostedAuthError:
                pass  # Local logout must still clear this app's own cache.
            finally:
                self.store.delete()
        return {"schemaVersion": "realytics-cli-auth-status-v1", "authenticated": False,
                "localSessionCleared": True, "serverSessionRevoked": revoked,
                "message": "CLI 로그인 저장 정보를 삭제했습니다." if revoked else
                           "CLI 로그인 저장 정보를 삭제했습니다. 서버 세션 폐기는 확인하지 못했습니다."}

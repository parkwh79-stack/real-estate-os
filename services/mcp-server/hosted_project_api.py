"""Authenticated client for the hosted project land and legal preview API."""

from __future__ import annotations

import json
import os
import re
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class HostedProjectApiError(RuntimeError):
    """An error safe to print without credentials or response payloads."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_HTTP_ERRORS = {
    400: "검토 항목이나 필지 선택을 확인하세요.",
    401: "로그인 토큰이 만료됐거나 사용할 수 없습니다.",
    403: "현재 계정에 이 프로젝트를 검토하거나 저장할 권한이 없습니다.",
    404: "접근 가능한 프로젝트 또는 검토 API를 찾지 못했습니다.",
    409: "프로젝트 또는 근거가 변경됐습니다. 미리보기를 다시 실행하세요.",
    413: "요약 보관 한도를 넘었습니다. 저장 항목을 줄여 주세요.",
    422: "미리보기 범위나 필지 번호를 확인하세요.",
    429: "요청이 많습니다. 잠시 뒤 다시 시도하세요.",
}


class HostedProjectApi:
    def __init__(self, origin: str, access_token: str) -> None:
        try:
            parsed = urlsplit(origin)
            parsed.port  # Reject invalid port syntax instead of failing in transport.
            valid_origin = (
                parsed.scheme == "https"
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and parsed.path in ("", "/")
                and not any(char in origin for char in "?#\\")
                and not any(char.isspace() or ord(char) < 32 for char in origin)
            )
        except ValueError:
            valid_origin = False
        if not valid_origin:
            raise HostedProjectApiError("REALYTICS_API_ORIGIN에 운영 HTTPS API 주소를 설정하세요.")
        if not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", access_token):
            raise HostedProjectApiError("REALYTICS_ACCESS_TOKEN에 유효한 계정 액세스 토큰을 설정하세요.")
        self._origin = origin.rstrip("/")
        self._access_token = access_token
        self._opener = build_opener(_NoRedirect())

    @classmethod
    def from_environment(cls) -> HostedProjectApi:
        return cls(os.environ.get("REALYTICS_API_ORIGIN", ""), os.environ.get("REALYTICS_ACCESS_TOKEN", ""))

    def preview_land_legal(
        self,
        project_id: str,
        *,
        parcel_offset: int | None = None,
        parcel_revision: str | None = None,
        parcel_indexes: list[int] | None = None,
    ) -> dict:
        return self._request(
            project_id,
            "preview",
            payload={
                "parcelOffset": parcel_offset,
                "parcelRevision": parcel_revision,
                "parcelIndexes": parcel_indexes,
            },
        )

    def save_land_legal_summary(
        self,
        project_id: str,
        *,
        sections: list[str],
        expected_preview_hash: str,
        parcel_indexes: list[int] | None = None,
        parcel_offset: int | None = None,
        parcel_revision: str | None = None,
        reviewed_parcel_indexes: list[int] | None = None,
    ) -> dict:
        return self._request(
            project_id,
            "preview-summary",
            payload={
                "sections": sections,
                "expectedPreviewHash": expected_preview_hash,
                "parcelIndexes": parcel_indexes,
                "parcelOffset": parcel_offset,
                "parcelRevision": parcel_revision,
                "reviewedParcelIndexes": reviewed_parcel_indexes,
            },
        )

    def list_land_legal_summaries(self, project_id: str) -> dict:
        return self._request(project_id, "preview-summary")

    def _request(self, project_id: str, endpoint: str, *, payload: dict | None = None) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", project_id):
            raise HostedProjectApiError("유효한 프로젝트 ID를 입력하세요.")
        headers = {"Authorization": f"Bearer {self._access_token}", "Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps({key: value for key, value in payload.items() if value is not None}).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self._origin}/projects/{project_id}/legal-pod/{endpoint}",
            data=body,
            headers=headers,
            method="GET" if body is None else "POST",
        )
        try:
            with self._opener.open(request, timeout=120) as response:
                data = response.read(4 * 1024 * 1024 + 1)
        except HTTPError as error:
            status = error.code
            error.close()
            message = _HTTP_ERRORS.get(status, "운영 검토 API에 연결하지 못했습니다.")
            raise HostedProjectApiError(f"{message} (HTTP {status})") from None
        except (OSError, HTTPException):
            raise HostedProjectApiError("운영 검토 API에 연결하지 못했습니다.") from None
        if len(data) > 4 * 1024 * 1024:
            raise HostedProjectApiError("운영 검토 응답의 크기 한도를 넘었습니다.")
        try:
            result = json.loads(data)
        except ValueError:
            raise HostedProjectApiError("운영 검토 API의 응답 형식이 올바르지 않습니다.") from None
        if not isinstance(result, dict):
            raise HostedProjectApiError("운영 검토 API의 응답 형식이 올바르지 않습니다.")
        # Reject a misconfigured upstream that reflects request authorization.
        if self._access_token in json.dumps(result, ensure_ascii=False):
            raise HostedProjectApiError("운영 검토 API의 응답을 안전하게 표시할 수 없습니다.")
        return result

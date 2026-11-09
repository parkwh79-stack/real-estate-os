"""Human login and read-only access to the authenticated REALYTICS operating API."""

from __future__ import annotations

import argparse
import getpass
import json
import sys

from hosted_project_api import HostedProjectApi, HostedProjectApiError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="REALYTICS 로그인과 운영 문서·원본 셀 읽기. 사용자 AI 키는 필요하지 않습니다.")
    commands = parser.add_subparsers(dest="command", required=True)
    login = commands.add_parser("login", help="이메일 숨김 입력 또는 --provider google 브라우저 로그인으로 본인 계정을 연결합니다.")
    login.add_argument("--email", help="REALYTICS 로그인 이메일; 생략하면 대화식으로 입력합니다.")
    login.add_argument("--provider", choices=("email", "google", "kakao"), default="email")
    commands.add_parser("status", help="키나 토큰 없이 현재 CLI 로그인 상태를 확인합니다.")
    commands.add_parser("logout", help="현재 CLI 세션을 해제합니다.")
    projects = commands.add_parser("projects", help="현재 계정의 최근 운영 프로젝트를 읽습니다.")
    projects.add_argument("--limit", type=int, default=20)
    documents = commands.add_parser("documents", help="프로젝트 원본 문서·파싱·검토 상태를 읽습니다.")
    documents.add_argument("project_id")
    documents.add_argument("--offset", type=int, default=0)
    documents.add_argument("--limit", type=int, default=50)
    candidates = commands.add_parser("candidates", help="실제 저장된 파싱 후보와 출처·검토 상태를 읽습니다. 승인하지 않습니다.")
    candidates.add_argument("project_id")
    candidates.add_argument("--archive-id")
    candidates.add_argument("--offset", type=int, default=0)
    candidates.add_argument("--limit", type=int, default=50)
    sheets = commands.add_parser("workbook-sheets", help="보관된 원본 Excel의 시트 목록과 탐색 범위를 읽습니다.")
    sheets.add_argument("project_id")
    sheets.add_argument("archive_id")
    cells = commands.add_parser("workbook-cells", help="원본 셀의 입력값·수식·저장 캐시를 읽습니다. 수식은 실행하지 않습니다.")
    cells.add_argument("project_id")
    cells.add_argument("archive_id")
    cells.add_argument("--sheet-name", required=True)
    cells.add_argument("--cell-reference", required=True)
    cells.add_argument("--context-rows", type=int, default=0)
    cells.add_argument("--context-columns", type=int, default=0)
    cells.add_argument("--expected-archive-version")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = vars(_parser().parse_args(argv))
    command = arguments.pop("command")
    from hosted_auth import HostedAuth, HostedAuthError

    try:
        if command in {"login", "status", "logout"}:
            auth = HostedAuth.from_environment()
            if command == "login":
                if not sys.stdin.isatty():
                    raise HostedAuthError("사용자 터미널에서 login을 실행하세요. 이메일 비밀번호는 숨김 입력하고 Google 계정은 --provider google로 연결하세요. 비밀번호를 명령 인수·환경변수·AI 채팅에 넣지 마세요.")
                if arguments["provider"] != "email":
                    print("브라우저에서 Google 로그인을 완료하세요. REALYTICS 연결 결과는 이 창에 표시됩니다. 최대 5분 기다립니다.", file=sys.stderr)
                    result = auth.login_oauth(arguments["provider"])
                else:
                    email = arguments["email"] or input("REALYTICS 이메일: ").strip()
                    password = getpass.getpass("REALYTICS 비밀번호 (숨김 입력): ")
                    try:
                        result = auth.login_password(email, password)
                    finally:
                        password = ""
            elif command == "status":
                result = auth.status()
            else:
                result = auth.logout()
        else:
            api = HostedProjectApi.from_environment()
            method = {"projects": "list_projects", "documents": "list_documents", "candidates": "list_candidates",
                      "workbook-sheets": "workbook_sheets", "workbook-cells": "workbook_cells"}[command]
            result = getattr(api, method)(**arguments)
    except (HostedProjectApiError, HostedAuthError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("작업을 취소했습니다. 입력한 비밀번호는 보관하지 않았습니다.", file=sys.stderr)
        return 130
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

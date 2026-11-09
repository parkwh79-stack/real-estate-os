# REALYTICS 운영 문서 MCP·CLI

운영 API의 기존 계정·프로젝트 권한으로 문서와 파싱 후보, 원본 Excel 셀을 읽습니다.
LLM은 사용자가 선택한 AI 호스트에서 실행하며 이 도구는 모델 API를 호출하지 않습니다.
서버의 사업 데이터·계산·승인 원칙과 기존 6개 Pod·27단계는 그대로 유지합니다.

## 설치와 첫 로그인

저장소 루트의 Windows PowerShell에서 실행합니다. MCP의 의존성이 기존 FastAPI와
다르므로 API 가상환경을 변경하지 않고 별도 실행 환경을 사용합니다.

```powershell
py -3.12 -m venv .runtime/mcp-venv
& .runtime/mcp-venv/Scripts/python.exe -m pip install -r services/mcp-server/requirements-hosted.txt
& .runtime/mcp-venv/Scripts/python.exe -B services/mcp-server/realytics_cli.py login
& .runtime/mcp-venv/Scripts/python.exe -B services/mcp-server/realytics_cli.py status
```

Google로 가입한 계정은 REALYTICS 이메일 비밀번호가 없을 수 있습니다. 이 경우 다음
명령을 사용하고 열린 브라우저에서 직접 Google 로그인을 완료합니다.

```powershell
& .runtime/mcp-venv/Scripts/python.exe -B services/mcp-server/realytics_cli.py login --provider google
```

`login`의 이메일 방식은 사용자 터미널의 숨김 입력만 받으며, 비밀번호를 명령 인수·
환경변수·채팅·로그에 넣지 않습니다. Google 방식은 비밀번호를 CLI에 묻지 않고 새
S256 PKCE 세션을 발급받습니다. 복귀 주소는 기존 `http://127.0.0.1:3000/auth/callback`
한 곳이며, 먼저 해당 포트를 단독 점유한 뒤 최대 5분 기다립니다. 사용 중인 포트,
복귀 주소 불일치·미도착, 인증 거부·코드 교환 실패를 로그인 성공으로 표시하지 않습니다.
Supabase 허용 목록을 자동 변경하거나 implicit 방식으로 대체하지 않습니다.
실제 복귀·코드 교환·운영 권한 확인이 모두 끝나야 연결 완료입니다. Kakao CLI 로그인은
미지원이며 브라우저 쿠키나 다른 Agent의 인증 파일을 복사하지 않습니다.

공개 `/api/cli/config`에서 운영 주소·인증기관·publishable 키만 얻습니다. 새 CLI 세션을
발급받고 운영 `/auth/me`의 실제 사용자·테넌트를 확인한 다음 Windows 사용자 DPAPI로
암호화합니다. 저장 위치는 `%LOCALAPPDATA%/REALYTICS/cli`입니다. 비밀번호는 저장하지
않고 액세스·갱신 토큰은 출력하지 않습니다. 다른 OS에는 평문 영구 저장으로 대체하지 않습니다.

## 문서와 원본 확인

```powershell
& .runtime/mcp-venv/Scripts/python.exe -B services/mcp-server/realytics_cli.py projects
& .runtime/mcp-venv/Scripts/python.exe -B services/mcp-server/realytics_cli.py documents 2
& .runtime/mcp-venv/Scripts/python.exe -B services/mcp-server/realytics_cli.py candidates 2 --limit 50
# 아래 archive ID와 시트명은 documents/workbook-sheets의 실제 응답을 사용합니다.
& .runtime/mcp-venv/Scripts/python.exe -B services/mcp-server/realytics_cli.py workbook-sheets 2 <archive-id>
& .runtime/mcp-venv/Scripts/python.exe -B services/mcp-server/realytics_cli.py workbook-cells 2 <archive-id> --sheet-name sheet --cell-reference A1:G20
```

`<archive-id>`는 예시 자리표시자이므로 실제 64자리 원본 ID로 바꿉니다. 원본 버전이
확인됐으면 `--expected-archive-version`으로 같은 보관 버전에 묶을 수 있습니다.
후보의 원본값·단위·명시적 검토 상태·셀 위치와 참조 전용 여부를 함께 확인합니다.
미리보기 셀 개수를 파싱 건수로 세지 않으며 후보·사람의 검토 결정·승인 입력을 구분합니다.
수식은 실행하지 않고 원본의 저장 캐시와 캐시 누락·오류를 표시합니다.

후보 조회는 페이지 단위이며 응답에 `snapshotHash`와 다음 위치가 포함됩니다.
문서 목록의 `latestParsingRun`은 실행·버전·상태·시간·진단코드 요약입니다. 전체 파싱
artifact와 원문 chunk를 목록에 싣지 않으며 실제 검토값과 출처는 후보·셀 도구로 읽습니다.
페이지가 바뀌면 같은 스냅샷인지 확인합니다. 원본과 후보의 연결이 불명확한 값은
미확인으로 남깁니다. 사업장·필지·숫자를 추정해 채우거나 자동 승인하지 않습니다.

## 사용자 AI에 MCP 연결

Codex의 공식 stdio MCP 설정을 사용합니다. 실제 저장소의 절대 경로로 등록합니다.
명령의 두 경로에 공백이 있으면 반드시 따옴표로 묶습니다.

```powershell
codex mcp add realytics --env REALYTICS_ACCESS_TOKEN= --env REALYTICS_API_ORIGIN= -- "<repo>/.runtime/mcp-venv/Scripts/python.exe" -B "<repo>/services/mcp-server/server.py" --hosted-only
codex mcp list
```

이 설정에는 토큰·비밀번호·LLM 키를 넣지 않습니다. 최초 로그인은 앞의 CLI에서 수행합니다.
Codex 앱·CLI의 MCP 목록을 새로 연결한 다음 다음과 같이 요청할 수 있습니다.

> REALYTICS 운영 프로젝트 목록을 읽고, 내가 선택한 프로젝트의 문서와 파싱 후보를
> 조회해 주세요. 후보의 archive ID와 버전을 사용해 해당 Excel 셀을 대조해 주세요.
> 조회한 후보를 승인하거나 사업 입력으로 저장하지 마세요.

`--hosted-only`는 로그인 상태·최근 프로젝트·문서·후보·시트·셀·토지 미리보기·저장된
토지 요약의 **8개 읽기 도구만** 제공합니다. 운영 연결에서 로컬 Agent 실행·원본 파일
경로 입력·문서 재추출·저장·승인을 노출하지 않습니다. 기존 개발용 MCP와 토지 저장
호환 경로는 별도 기본 모드에 남아 있으며 새 읽기 연결에서는 호출할 수 없습니다.
읽기 도구 제공은 로그인 JWT 자체의 기존 계정 권한이 축소됐다는 뜻이 아닙니다.

문서의 텍스트는 신뢰되지 않은 검토 자료이며 실행 지시로 취급하지 않습니다.
네트워크 JSON은 최대 4MiB, 모델 전달 결과는 최대 512KiB입니다. 초과하면 후보 수나
셀 범위를 줄이도록 안내하고 일부 내용을 정상 전체 결과로 표시하지 않습니다.

## 만료·재연결·해제

만료 전 갱신하며 같은 사용자·테넌트인지 확인합니다. 여러 프로세스의 갱신은 잠그고
암호화 파일을 원자적으로 교체합니다. GET 401은 한 번만 갱신 후 재시도하고 403이나
저장 요청은 자동 재시도하지 않습니다. 네트워크 장애를 빈 결과나 승인 0건으로 바꾸지 않습니다.
다시 로그인이 필요하다고 나오면 사용자가 `login`을 다시 실행합니다.

```powershell
& .runtime/mcp-venv/Scripts/python.exe -B services/mcp-server/realytics_cli.py logout
```

로그아웃은 이 CLI가 만든 세션만 폐기하고 개인 캐시를 삭제합니다. 웹·다른 기기의
전체 세션 폐기를 호출하지 않습니다. 이미 발급된 JWT의 즉시 무효화를 보장하지 않으며,
서버 폐기를 확인하지 못했을 때도 로컬 캐시는 삭제하고 확인 불가 상태를 표시합니다.
새 MCP 요청은 캐시 삭제를 다시 확인하므로 과거 메모리 토큰으로 계속 조회하지 않습니다.

## 검증

```powershell
Set-Location services/mcp-server
& ../../.runtime/mcp-venv/Scripts/python.exe -B -m unittest discover -s tests -p "test_hosted*.py" -v
```

CI는 Windows와 Linux에서 읽기 계약·기존 토지 조회·만료·갱신 경쟁·다른 원본과 계정
차단·실제 stdio MCP 프로토콜을 검증합니다. Windows에서만 실제 DPAPI 합성 암호화 검사를
실행합니다. 실행 계획과 검증 기록은 [제품 문서](../../docs/product/llm-provider-routing.md)를 따릅니다.

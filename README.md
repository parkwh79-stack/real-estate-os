# REALYTICS × PLATFORM9

부동산 개발과 PF 검토에서 자료를 다시 입력하는 시간, 숫자를 대조하는 시간, 보완자료를 요청하는 시간을 줄이는 업무 도구다.

Updated: 2026-09-28

현재 목표는 **자료 업로드 → 숫자·출처 확인 → 가정 변경과 재계산 → 검토 초안 저장**을 실제 프로젝트 하나에서 끝내는 것이다. 결정론적 계산과 명시적인 사용자 가정으로 먼저 가치를 만들고, AI 설명과 외부 데이터는 필요한 곳에 붙인다.

## 먼저 읽을 문서

- [현재 구현 현황](docs/product/current-implementation-status.md): 있는 코드, 확인한 근거, 남은 연결 작업.
- [제품 실행 계획](docs/product/realytics-os-next-work-roadmap.md): 이번 제품 범위와 다음 구현 순서의 단일 기준.
- [전체 문서 지도](docs/README.md): 현재 명세·확장 설계·과거 검증 기록의 구분.
- [작업 원칙](AGENTS.md), [변경 이력](docs/backlog.md).

## 현재 코드

| 위치 | 역할 |
|---|---|
| `apps/web` | 프로젝트 자료 검토, 사업수지·Cash Flow, PF·리스크, 보고서 화면과 BFF |
| `services/api-server` | FastAPI, 계산 엔진, 자료 파싱·검토, Pod 실행과 저장 |
| `packages/ui` | 실제 화면에서 사용하는 금융 수치·상태 표현 |
| `supabase/migrations` | 기존 DB 마이그레이션 이력 |
| `docs/product` | 기능별 계약과 검증 근거 |

개발 환경에는 로컬 SQLite 호환 경로가 있고, 다중 사용자 운영의 데이터 권한 기준은 PostgreSQL이다. 로그인·테넌트·저장소·Worker의 운영 연결은 별도 검증 대상이다. 현재 상세 경계는 [아키텍처 계약](docs/architecture/architecture-boundary.md)에 있다.

## 로컬 실행

Node.js 20.9 이상, 저장소의 npm 설정, Python 3.12를 사용한다. 기존 설치 환경이 있으면 다시 설치할 필요가 없다. 신규 환경은 저장소 루트에서 준비한다.

```powershell
npm ci
python -m venv .runtime/api-venv
.runtime/api-venv/Scripts/python.exe -m pip install -r services/api-server/requirements.txt
```

기존 설정을 사용해 API와 Web을 시작한다.

```powershell
./scripts/start-local-dev.ps1
```

Web은 `http://127.0.0.1:3000`, API liveness는 `http://127.0.0.1:8000/health/live`, 설정 준비 상태는 `/health/ready`다. 시작 스크립트는 인증 설정이나 데이터 권한을 대신 구성하지 않는다. 무료 개발은 기존 PC와 로컬 기능으로 진행하며, API 키가 없는 분석은 지원 가능한 입력·계산 범위를 표시한다.

검증 명령은 루트 기준이다.

```powershell
npm run check-types
npm run lint
npm run test:contracts
Push-Location services/api-server
../../.runtime/api-venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider
Pop-Location
```

Web 배포 빌드는 `npm run build --workspace web`이다. 사용하지 않던 `apps/docs` 예제 서버는 제거했다. 업무 문서는 [docs](docs/README.md)에서 읽는다.

## 개발 기준

- 이미 있는 API·화면·계산 경로를 완성한다. 실사용 경로가 없는 코드와 예제 앱은 제거한다.
- 자료가 부족하면 계산 가능한 범위, 사용한 가정, 필요한 다음 자료를 함께 제공한다.
- 분석 초안과 사람의 최종 승인·발행은 구분한다. AI가 금융 수치나 증거를 만들어내지 않는다.
- 새로운 유료 인프라·외부 AI 호출을 개발의 선행조건으로 삼지 않는다.
- 과거 CI 숫자는 해당 revision의 결과다. 이번 변경의 검증은 [변경 이력](docs/backlog.md)에 별도로 남긴다.

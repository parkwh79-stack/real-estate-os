# 프로젝트 폴더 정리 — 2026-09-14

## 후속 재진단에서 추가 정리한 내용

아래 최초 정리 이후, 기존 순회에서 제외되던 API 내부 `.runtime`의 과거
pytest basetemp 폴더 9개를 확인했다. 정확한 이름만 허용 목록에 추가하고
하위 SQLite WAL/SHM/journal이 있는 대상은 보존하도록 보강했다.
안전성 테스트 5개가 통과한 뒤 **154개 파일 / 65.98 MiB를 추가 삭제**했다.
이어서 사용 참조가 없는 웹 시작 템플릿 CSS 1개·SVG 7개를 제거하고 웹 빌드·린트를
재검증했다. 후속 정리에서 삭제한 파일은 총 162개이며 업무/회귀 테스트 소스는 보존했다.
전체 검증 및 보존/삭제 판단은
[코드베이스 재진단](independent-codebase-audit-2026-09-14.md)에 기록했다.
아래 4개 안전성 시험/시험 격리 미완료 문구는 최초 정리 시점의 이력이다.

## 범위와 보존 기준

사용자 홈 전체가 아닌 `realytics-platform9-monorepo`만 대상으로 한다.
소스, 회귀 테스트, fixture, migration, 문서, 의존성, 가상환경은 보존한다.
다른 작업에서 변경 중인 `calculation_run_repository.py`와
`deal_state_repository.py`는 이번 정리에서 수정하지 않는다.

운영 SQLite, 업로드 원본, 현재 baselines, pilot-input, 공시/지도 캐시,
환경변수 파일, 실행 로그, `.next`는 자동 삭제 대상이 아니다.
폴더명이 오래됐다는 이유만으로 사업 자료를 삭제하지 않는다.

## 적용 도구

```powershell
# 프로젝트 루트에서: 목록만 확인
.\scripts\cleanup-generated.ps1
# 7일 이상 지난 허용 대상만 삭제
.\scripts\cleanup-generated.ps1 -Apply
```

- 기본 동작은 dry run. 예약 실행은 등록하지 않았다.
- 캐시(`__pycache__`, `.pytest_cache`, `.turbo`), API `.pytest*` 임시 폴더,
  웹 테스트 결과, `.runtime/pytest-tmp`, 명시된 과거 회귀시험 SQLite만 허용한다.
- 폴더 또는 자식에 최근 수정 파일이 있으면 폴더 전체를 보존한다.
- Git 추적 파일이 포함된 대상은 보존한다.
- 절대경로를 프로젝트 내부로 제한하고 junction/symlink가 있는 대상은 제외한다.
- 시험 DB에 WAL/SHM/journal이 있으면 제외한다. 이것이 모든 실행 중 프로세스를
  감지하는 잠금 검증은 아니므로, 별도 장기 시험 실행 중에는 정리하지 않는다.
- 실행 중 삭제 실패는 경고로 남긴다. 부분 삭제 가능성이 있으므로 경고가 있으면
  완료 개수만으로 전체 성공을 판단하지 않는다.

## 검증

실제 정리 결과: 86개 경로의 2,561개 파일, 1,010.02 MiB를 삭제했다.
삭제 경고 없이 완료했으며 운영 DB·환경변수·초기화 백업·기존 수정 파일을
삭제 대상에서 제외했다.

앱을 import하지 않는 임시 Git 저장소에서 다음을 검증했다.

1. dry run은 파일을 삭제하지 않는다.
2. 허용된 캐시/시험 DB만 삭제하고 운영 DB·원본·백업·테스트 소스는 보존한다.
3. 추적 파일, 최근 파일, WAL이 있는 시험 DB를 보존한다.
4. 심볼릭 링크 바깥의 파일을 보존한다.

```powershell
.\.runtime\api-venv\Scripts\python.exe -m unittest discover -s scripts/tests -v
```

4개 테스트 통과. API/Web 동작 변경이 없어 전체 pytest 및 Web 빌드는 실행하지
않았다. 이번 검증은 사업계획서 파싱·Pod 재계산·승인 Gate의 E2E 검증을 대체하지 않는다.

## 후속 상태

후속 감사에서 시험 격리를 구현·검증하고 생성물 162개를 정리했다.
최신 결과와 삭제 차단 항목은 [실행 기록](remediation-execution-2026-09-14.md)을 참고한다.
아래 목록은 최초 정리 당시의 기록이며 업무 백업은 계속 보존한다.

## 최초 정리 후 남은 작업

- [ ] 과거 초기화 백업의 보존 기한과 삭제 승인을 결정한다. 현재
  `baselines.deleted-full-reset` 약 4,080.90 MiB,
  `uploads.deleted-full-reset` 약 287.90 MiB,
  `realytics.sqlite3.bak-full-reset` 약 928.87 MiB를 보존했다.
  원본이 섞여 있으므로 이 도구의 자동 삭제 범위에 추가하지 않는다.
- [ ] API 테스트 실행의 저장 경로 격리를 별도 검증한다. `storage.py`의 기본 경로는
  운영 로컬 SQLite이고, 현재 `tests/conftest.py`는 외부 연결 플래그를 주로 제어한다.
  전체 테스트를 실행하기 전에 임시 SQLite/JSON/object 경로와 테스트용 DB 설정을
  확정해야 한다. PostgreSQL 통합 시험은 명시적인 시험 DB만 사용하도록 검증한다.
- [ ] 751 프로젝트의 사업계획서·인허가 문서 → 추출 근거 → 사업수지 → Cash Flow →
  Pod 재검토 경로는 별도 기능 작업으로 검증한다. 이번 정리는 프로젝트 원장이나
  blocker 상태를 변경하지 않는다.

# REALYTICS 현재 구현 현황

Updated: 2026-09-28

문서와 코드의 연결을 다시 점검한 현재 기준이다. 과거의 기능별 개발 일지와 중복된 상태 설명은 [통합 이전 기록](../archive/documentation-baseline-2026-09-28.md)에 보존했다. 작업 순서는 [제품 실행 계획](realytics-os-next-work-roadmap.md) 한 곳에서 관리한다.

## 판단

핵심 도구는 상당 부분 구현돼 있다. **기존 6개 Chief Pod와 업무 Agent를 유지하면서 한 프로젝트의 자료·입력·계산·검토 초안을 이어서 사용하는 경험을 완성**한다. 일관된 입력 버전과 Agent 산출물을 화면·다운로드에 반영하는 작업이 우선이다. 이번 정리에서 Agent·Pod나 27단계 PF 흐름은 삭제하지 않았다.

아래의 “코드 있음”은 현재 파일·호출 경로를 확인했다는 뜻이며, 오늘 모든 기능을 실사용 검증했다는 뜻은 아니다.

| 사용자 작업 | 확인한 구현 근거 | 현재 판단 / 필요한 작업 |
|---|---|---|
| 가입·로그인 | Web `/login`, Supabase password/OAuth BFF, `supabase/functions/naver-userinfo` | 이메일 가입·비밀번호·재설정과 카카오/Google 진입을 같은 화면에 연결. 네이버는 앱 신규 등록·서버 응답 변환·실제 로그인 검증 후 활성화하며 현재 준비 중. [설정 및 검증](live-auth-smoke-runbook.md). |
| 프로젝트 생성·재개 | `use_cases/project_management.py`, Web `/start`, `/dashboard`, `/project/[id]` | 기존 흐름 재사용. 첫 사용자가 다음 행동을 찾을 수 있는지 실제 자료로 확인. |
| 자료 업로드와 문서 분류 | `routers/project_source_documents.py`, `use_cases/intake_finalize.py`, `parsing_platform` | 코드 있음. 외부 AI 없이 처리되는 문서와 수동 확인이 필요한 문서를 명확히 표시. |
| 숫자·출처 검토 | `use_cases/document_review_workspace.py`, `routers/project_parser_v2.py`, Web `/project/[id]/documents/review` | 후보 검토·스냅샷 경로 있음. 편집·저장·재개를 한 사용자 흐름으로 검증. |
| 사업수지 Excel 검증·입력 | `use_cases/approved_workbook_authority.py`, `workbook_import_approval.py`, `feasibility_input_builder.py`, `feasibility_workbook_llm.py` | 기존 입력 패키지에 AI 없는 직접 입력·원본 셀 보기·이유/가정 저장·원 단위 금액 보존·재개를 연결. 정식 승인과 분석용 입력은 구분. 실제 실무 자료 전체 흐름 검증은 남아 있음. |
| 사업수지·월별 CF·부족자금 | `core/calculations.py`, `use_cases/development_feasibility.py`, `cash_flow.py`, Web feasibility/cashflow | 계산 엔진 있음. 가정 변경 전후를 같은 단위·기간·입력 기준으로 비교하는 경험을 완성. |
| PF·상환·미분양 시나리오 | `agents/scenario_engine_agent.py`, `use_cases/cash_flow_dependencies.py`, Web `/project/[id]/pf-structure` | 계산·화면 있음. 동일 부모 계산을 사용하는 시나리오 묶음과 불완전 입력 설명을 우선. |
| 토지·시장·법률 보완 | `use_cases/land_ledger_import.py`, `market_comparables.py`, `legal_pod.py` | 업로드 자료·수동 근거를 먼저 활용. 실제 커넥터 사용 가능 여부와 자료 기준일은 별도 확인. |
| 보완사항과 재분석 | `use_cases/reanalyze_on_evidence.py`, `risk_register_agent.py`, Evidence/Risk 화면 | 기존 로직을 이용해 다음 행동을 모은다. UUID 변경을 업무 결과 변경으로 보는 hash 문제는 남아 있음. |
| 검토 초안·다운로드 | `use_cases/report_generation.py`, `agents/report_export_agent.py`, Web 프로젝트 보고서와 Cash Flow export | PDF/DOCX/Excel 경로가 이미 있다. 출처·가정·미확인 항목을 포함한 초안 사용 흐름을 검증. |
| 로그인과 조직 경계 | Web `app/api/auth/supabase`, `core/authz.py`, DB repositories | 코드 있음. 운영 준비를 단순 로그인 화면 존재로 판단하지 않음. 기존 권한 유지. |
| 자동 코딩·자기수정 | `agent_platform/coding` | 현재 제품 우선순위에서 제외. 취소/완료 경합과 실행 격리 과제가 있어 운영 활성화하지 않음. |

API 코드 경로는 `services/api-server/` 기준이다. 프로젝트 Web 경로와 실제 파일은 [문서 지도](../README.md)의 관련 기능 계약에서 찾는다.

## 바로잡은 상태 설명

- PF 기본 흐름은 `schemas/orchestration.py::PF_REVIEW_STEPS`의 **27단계**다. 과거 문서의 18단계는 이전 구현이다.
- PDF·DOCX·Excel은 “화면만 있고 내보내기는 미구현” 상태가 아니다. 생성 기능과 최종 발행 자격은 구분한다.
- 프로젝트 Cash Flow export는 `cashflow-export-completions`를 호출한다. 과거 backlog의 “callback 연결 필요”를 현재 미구현으로 다시 등록하지 않는다.
- `/api/export-cf`의 고정 값은 별도 샘플 템플릿 경로다. 실제 프로젝트 다운로드 경로와 혼합하지 않는다. 사용 중인 샘플·회귀 fixture를 무조건 삭제하지 않는다.
- 인증·PostgreSQL·비동기 실행 코드가 이미 있다. “로그인이 전혀 없음”, “모든 실행이 동기식”, “PostgreSQL 미구현” 같은 포괄적 설명은 폐기한다.
- 공공정책 Pod 이름만으로 독립된 정책 시뮬레이터가 구현됐다고 볼 수 없다. 현재 개발가능성 평가 재사용 범위를 표시한다.

## 사용자 가치에 영향을 주는 남은 연결

1. 입력 수정부터 재계산·비교·재열기까지 이어지는 작업 흐름을 실제 자료로 검증한다.
2. 분석용 사용자 가정과 확인된 증거를 함께 보여주되, 누락된 최종 승인 자료가 무관한 분석까지 막지 않게 한다.
3. 결과 화면과 다운로드가 같은 CalculationRun·입력 스냅샷을 사용하는지 확인한다.
4. PDF 비교 기준은 실행별 새 report ID와 별개로 연결해야 한다. 최초 이미지를 만든 상태는 검토된 기준과의 비교 통과가 아니다.
5. Coding Worker의 상태 경합은 해당 기능의 활성화 과제다. 일반 사업성 검토 제품을 먼저 만드는 순서를 막는 전역 조건으로 쓰지 않는다.

## 검증의 범위

[2026-09-15 코드 감사](pod-agent-code-audit-2026-09-15.md)에는 `cad030a`의 API 2,418개, 별도 PostgreSQL 56개, 브라우저 238개 통과 기록이 있다. 이는 보존한 과거 결과다. 오늘의 문서·예제 정리 및 R1 첫 변경 검증은 [backlog](../backlog.md)에 기록한다. Supabase에서 적용된 migration 26개의 버전·이름을 로컬과 대조했다. 운영 권한·백업·외부 배포 및 실제 사용자 파일럿을 재인증한 것은 아니다.

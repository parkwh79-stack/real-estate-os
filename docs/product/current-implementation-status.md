# REALYTICS 현재 구현 현황

Updated: 2026-10-14

문서와 코드의 연결을 다시 점검한 현재 기준이다. 과거의 기능별 개발 일지와 중복된 상태 설명은 [통합 이전 기록](../archive/documentation-baseline-2026-09-28.md)에 보존했다. 작업 순서는 [제품 실행 계획](realytics-os-next-work-roadmap.md) 한 곳에서 관리한다.

## 판단

핵심 도구는 상당 부분 구현돼 있다. **기존 6개 Chief Pod와 업무 Agent를 유지하면서 한 프로젝트의 자료·입력·계산·검토 초안을 이어서 사용하는 경험을 완성**한다. 일관된 입력 버전과 Agent 산출물을 화면·다운로드에 반영하는 작업이 우선이다. 이번 정리에서 Agent·Pod나 27단계 PF 흐름은 삭제하지 않았다.

아래의 “코드 있음”은 현재 파일·호출 경로를 확인했다는 뜻이며, 오늘 모든 기능을 실사용 검증했다는 뜻은 아니다.

2026-10-14 사용자가 샘플 전체 검토를 먼저 지시했다. 개인전용·법인 공유공간은
현재 범위에서 제외한다. [샘플 검토 결과](sample-material-review-2026-10-14.md)는
실제 자료의 단위·계약·기준일 충돌과 코드의 의미 매핑 문제를 분리해 기록한다.
기존 예제 테스트 통과와 실제 원본 검증 완료는 구분하며, 다음 구현 순서는 실행
계획의 현재 착수점을 따른다.

후속 지시로 실제자료 수정·재계산·비교·재열기와 화면/다운로드의 동일 계산 기준을
우선 검증했다. 항목별 원본 대조·사용자 가정을 각 CF 실행에 저장해 비교와 초안에
표시하고, PDF 기준 연결·명시적 검토 상태·한글 글꼴을 보완했다. 실제 본리동 금액과
명시적 검증용 금융 가정의 격리 API 흐름은 통과했으며, 실사용자·운영 DB·실제 약정
인수와 검토된 운영 PDF 기준 등록은 별도다. Coding Worker 경합은 해당 기능의
활성화 과제로 유지하며 일반 분석을 막는 조건으로 사용하지 않는다.

추가 보완으로 토지조서 기지급과 미지급·예정액을 분리했고, 누락된 물류 입력으로
NOI·LTV·점수를 생성하던 처리를 수정했다. 입력이 부족한 지표만 미확인으로
남기며 독립적인 분석은 계속한다. 조서상 지급 기록은 실제 송금 증빙과 다르고,
기존 저장본 재가져오기·실제 계약 조건·물류 비교사례 분류 확인은 남아 있다.

| 사용자 작업 | 확인한 구현 근거 | 현재 판단 / 필요한 작업 |
|---|---|---|
| 가입·로그인 | Web `/login`, Supabase password/OAuth BFF, `supabase/functions/naver-userinfo` | 이메일 가입·비밀번호·재설정과 카카오/Google 진입을 같은 화면에 연결. 네이버 앱 등록·저장 설정 확인과 회원정보 변환 함수 v1 배포 완료. Supabase 공급자 설정과 실제 로그인 검증 후 활성화하며 현재 네이버 버튼은 비활성이다. [설정 및 검증](live-auth-smoke-runbook.md). |
| 프로젝트 생성·재개 | `use_cases/project_management.py`, Web `/start`, `/dashboard`, `/project/[id]` | 기존 흐름 재사용. 첫 사용자가 다음 행동을 찾을 수 있는지 실제 자료로 확인. |
| 자료 업로드와 문서 분류 | `routers/project_source_documents.py`, `use_cases/intake_finalize.py`, `parsing_platform` | 코드 있음. 외부 AI 없이 처리되는 문서와 수동 확인이 필요한 문서를 명확히 표시. |
| 숫자·출처 검토 | `use_cases/document_review_workspace.py`, `routers/project_parser_v2.py`, Web `/project/[id]/documents/review` | 후보 검토·스냅샷 경로 있음. 편집·저장·재개를 한 사용자 흐름으로 검증. |
| 사업수지 Excel 검증·입력 | `use_cases/approved_workbook_authority.py`, `workbook_import_approval.py`, `feasibility_input_builder.py`, `feasibility_workbook_llm.py` | AI 없는 직접 입력·원본 셀 보기·이유/가정 저장과 원 단위 금액·사업 일정·대출 조건의 저장·재개를 연결. 정식 승인과 분석용 입력은 구분. 실제 자료 검증의 범위와 결과는 실행 계획에 기록한다. |
| 사업수지·월별 CF·부족자금 | `core/reviewed_monthly_financing.py`, `use_cases/cash_flow.py`, `cash_flow_comparison.py`, Web feasibility/cashflow | 명시적으로 선택한 검토 정책으로 월별 이자·수수료·잔액·상환을 계산. 저장된 두 CalculationRun의 입력·집계 차이를 비교하고 선택을 저장·재개한다. 기간·금융방식이 다르면 부분 비교로 표시하며 승인·최종 보고서 자격은 부여하지 않는다. |
| PF·상환·미분양 시나리오 | `agents/scenario_engine_agent.py`, `use_cases/cash_flow_dependencies.py`, Web `/project/[id]/pf-structure` | 기존 계산·화면 유지. CF 화면은 같은 부모·입력 hash의 후속 결과만 표시한다. 새 월별 검토안에 기존 일괄 청구액 방식의 후속 시나리오를 붙이는 작업은 별도 상환 계약 검증까지 보류한다. |
| 토지·시장·법률 보완 | `use_cases/land_ledger_import.py`, `market_comparables.py`, `legal_pod.py` | 공동계약·개인별 미기재·합계 오류를 구분하고 계약/수지 대사 메모를 저장·재개한다. 조서 기지급과 예정액·미기재를 따로 표시하며 이전 지급 완료 오판정을 재사용하지 않는다. 설계 용적률 오매핑과 물류 누락값/실제 0 구분은 보완했다. 물류 비교 자산 분류·실제 계약 대사는 남아 있다. [범위와 검증](land-ledger-import-and-enrichment.md). |
| 보완사항과 재분석 | `use_cases/reanalyze_on_evidence.py`, `risk_register_agent.py`, Evidence/Risk 화면 | 위험별 보완 사유·다음 할 일을 기존 검토 메모로 입력·저장·재개하고 이력을 확인한다. 같은 ID/run/본문/출처 재생성은 사람 검토를 보존하며, 바뀐 근거나 새 실행에 과거 승인을 넘기지 않는다. 편집 중 다른 검토를 막고 실패 시 입력을 유지한다. 다중 사용자 동시 수정 CAS는 미구현이다. CF 비교는 실행 메타데이터와 업무 입력·출처 차이를 구분하며, 다른 재분석·consensus 경로는 별도 확인한다. |
| 검토 초안·다운로드 | `use_cases/report_generation.py`, `agents/report_export_agent.py`, Web 프로젝트 보고서와 Cash Flow export | 저장된 CF 비교의 두 계산·입력·출처·검토 이유를 고정한 분석 초안을 기존 보고서 저장·재조회·내보내기에 연결한다. 현재 입력 변경과 분리하고 hash 검증·최종 발행 차단을 유지한다. 상세 검증과 PDF/DOCX 범위는 실행 계획을 따른다. |
| Chief Pod 계산 계보 | `use_cases/chief_deal_orchestration.py`, `chief_pod_records.py`, `agent_platform/domain_pod_runtime.py` | 6개 Pod 모두 실행 경로가 있다. CF Pod는 실제 계산 요청·입력 hash와 Chief/Pod 실행 ID를 저장하며 소비 입력 변경 시 재실행한다. 원가 Pod·child 캐시와 실제 Agent의 입력을 일치시키고, 재실행한 파생 결과를 수동·승인 입력 보호하에 프로젝트 조회에 반영한다. 총사업비 누락은 미확인으로 유지한다. 공공정책의 전용 시뮬레이션은 미구현이며 기존 개발가능성 분석 재사용이다. |
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

[2026-09-15 코드 감사](pod-agent-code-audit-2026-09-15.md)는 해당 revision의 과거 결과다. 최신 R1/R2 변경·검증은 [backlog](../backlog.md)와 [실행 계획](realytics-os-next-work-roadmap.md)에 기록한다. Supabase migration 26개의 버전·이름과 Naver v1 배포 소스는 2026-10-14 대조 결과 일치하며 이번 변경에 새 migration은 없다. 실제 사업수지는 사용자 승인 반올림과 원본값·처리 이유를 보존해 저장·재개·기본 계산을 격리 검증했다. 원본 수식 오류와 미확인 대출 조건 때문에 전체 실자료·월별 금융 인수는 남아 있다. 운영 권한·백업·외부 배포의 완료를 뜻하지 않는다.

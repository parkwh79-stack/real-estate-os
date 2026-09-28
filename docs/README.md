# REALYTICS 문서 지도와 정리 판단

Updated: 2026-09-28

## 현재 읽을 순서

1. [제품 실행 계획](product/realytics-os-next-work-roadmap.md): 사용자 가치, 기존 Agent·Pod 유지, R1–R4 구현 순서.
2. [구현 현황](product/current-implementation-status.md): 현재 코드 근거와 남은 연결 작업.
3. 필요한 기능의 상세 계약: 아래 문서 목록에서 선택.
4. [변경 이력](backlog.md): 이번 변경과 검증. 과거 진단은 해당 시점의 기록으로 읽는다.

## 전수 조사 범위와 한계

프로젝트 저장소 111개와 바깥 작업 폴더 2개, 총 **113개 Markdown(약 1.60MB)**를 목록화하고 내용·목차·상태 표기·내부 Markdown 링크를 검사했다. 의존성 패키지의 설명서, 가상환경, 자동 생성 런타임 파일은 제품 문서에서 제외했다. 핵심 진입 문서·실행 계획·상태표·SDD·보고서/배포 설명은 본문과 현재 코드 경로를 대조했다. 나머지 상세 명세는 목적·연결·이력/설계 구분을 검토한 것이며, 모든 문장과 도메인 규칙을 새로 인증하거나 전체 기능을 다시 시험한 것은 아니다.

## 실제 정리한 내용

- MVP·서비스 로드맵·출시·유지보수의 네 계획 문서를 하나의 실행 계획으로 통합하고 중복 파일을 삭제했다.
- README와 구현 현황의 누적 개발 일지를 현재 코드 기준의 짧은 진입 문서로 바꿨다.
- 기존 README·현황·로드맵·네 계획의 원문은 [통합 이전 기록](archive/documentation-baseline-2026-09-28.md)에, 긴 backlog는 [과거 변경 이력](archive/backlog-through-2026-09-28.md)에 보존했다. 과거 기록이 현재 작업을 지시하지 않도록 구분했다.
- 예제 `apps/docs` 앱과 미사용 `packages/ui/src/{button,card,code}.tsx`를 제거했다. 실서비스의 금융 수치·상태 컴포넌트, **Agent·Pod·handoff·27단계 PF 흐름은 유지**했다.
- 18단계/27단계 혼용, 미구현으로 남아 있던 내보내기·로그인 설명, 오래된 migration 요약, PDF 첫 baseline의 승인/비교 성공 표현을 정정했다.
- 큰 구조·핵심 기능의 추가/삭제는 제안 후 사용자 승인 대상으로 두고, 진단·참조 확인·관련 검증은 진행한다.

## 문서를 합치는 기준

같은 결정을 두 군데에서 관리할 때 합친다. 용어 스키마와 용어 운영, 법률 ADR과 법률 운영 절차, Agent 전달 규약과 실행 상태 규약처럼 **역할이 다른 계약은 유지**한다. 기존 Agent·Pod를 유지하는 데 필요한 명세를 문서 수를 줄이려고 삭제하지 않는다. `agents/llm_agent_policies.md`는 실제 런타임 프롬프트이므로 별도 코드 변경으로 취급한다.

남은 도메인별 로드맵의 오래된 Sprint/Next 항목은 각 기능을 구현하는 R1–R3에서 코드와 대조해 갱신한다. 지금 전부 새로 미구현으로 등록하거나 이미 있는 기능을 다시 만들지 않는다. 긴 설계 문서 전체를 최신 구현 완료 문서로 선언하지도 않는다.

## 원래 문서 113개의 처리 목록

“명세 유지”는 해당 기능의 계약으로 남긴다는 뜻이다. “이력”은 기록된 시점에만 해당하며 “확장 설계”는 첫 사용 흐름의 선행조건이 아니다.

| 원래 문서 | 처리 | 판단 |
|---|---|---|
| [docs/product/phase-11-pf-review-report.md](product/phase-11-pf-review-report.md) | 삭제 | 바깥 폴더의 0바이트 중복 파일. 실제 명세는 저장소 안에 있음. |
| [AGENTS.md](../AGENTS.md) | 수정·유지 | 실행 범위·현재 기준·과거 기록의 구분을 수정. |
| [apps/docs/README.md](../README.md) | 예제와 함께 삭제 | 실제품 문서 서버가 아닌 Turborepo 시작 예제. |
| [apps/web/AGENTS.md](../apps/web/AGENTS.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [apps/web/CLAUDE.md](../apps/web/CLAUDE.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [apps/web/README.md](../apps/web/README.md) | 현행 문서로 재작성 | 기본 Next.js 예제 설명을 실제 Web 실행·설정·작업 경로로 교체. |
| [CLAUDE.md](../CLAUDE.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/api.md](api.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/architecture/architecture-boundary.md](architecture/architecture-boundary.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/architecture/legal-intelligence-core.md](architecture/legal-intelligence-core.md) | 명세 유지 | 법률 자료의 출처·버전 설계. 운영 runbook과 목적이 달라 별도 유지. |
| [docs/architecture/postgres-runtime-authority.md](architecture/postgres-runtime-authority.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/architecture.md](architecture.md) | 수정·유지 | 실행 범위·현재 기준·과거 기록의 구분을 수정. |
| [docs/backlog.md](backlog.md) | 현행 문서로 재작성 | 현재 목적·경로·구현 상태로 정리. 과거 내용은 통합 이력에 보존. |
| [docs/engineering/bonri-feasibility-gap-analysis.md](engineering/bonri-feasibility-gap-analysis.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/engineering/legal-corpus-operations.md](engineering/legal-corpus-operations.md) | 명세 유지 | 자료 동기화·복구 운영 절차. ADR과 중복 제거를 이유로 합치지 않음. |
| [docs/integrations/korean-law-mcp.md](integrations/korean-law-mcp.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/agent-ai-brain-and-track-workflow-policy.md](product/agent-ai-brain-and-track-workflow-policy.md) | 설계 유지 | Pod·업무 트랙 설계 유지. 모든 트랙 완성을 첫 사용자 기능의 조건으로 삼지 않음. |
| [docs/product/agent-interaction-operating-model.md](product/agent-interaction-operating-model.md) | 명세 유지 | Agent 간 전달·대사 계약 유지. 구현 순서는 실행 계획으로 통일. |
| [docs/product/agent-platform-control-plane-v1.md](product/agent-platform-control-plane-v1.md) | 명세 유지 | 기존 Agent 등록·실행 계약 유지. 사용하지 않는 신규 계층 확장은 보류. |
| [docs/product/ai-work-order-ux.md](product/ai-work-order-ux.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/auth-storage-design-validation-2026-07-22.md](product/auth-storage-design-validation-2026-07-22.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/billing-entitlement-contract.md](product/billing-entitlement-contract.md) | 설계 유지 | 기존 기능·계약을 보존하고 첫 사용자 흐름에 필요한 확장만 우선. |
| [docs/product/calculation-run-registry.md](product/calculation-run-registry.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/calculation-scenario-engine.md](product/calculation-scenario-engine.md) | 명세 유지 | 계산·입출력 명세 유지. 옛 Sprint/UI 예정 항목은 R2에서 실제 코드와 대조. |
| [docs/product/cash-flow-engine-agent.md](product/cash-flow-engine-agent.md) | 명세 유지 | 공식·월별 모델 명세 유지. 반복되는 실행 계획은 현행 R2가 우선. |
| [docs/product/chief-deal-orchestrator-prompt.md](product/chief-deal-orchestrator-prompt.md) | 명세 유지 | Chief의 목표 행동 참고. 실제 분기·단계는 코드와 현황표 기준. |
| [docs/product/chief-pod-runtime-integrity.md](product/chief-pod-runtime-integrity.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/credit-review-agent.md](product/credit-review-agent.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/current-implementation-status.md](product/current-implementation-status.md) | 현행 문서로 재작성 | 현재 목적·경로·구현 상태로 정리. 과거 내용은 통합 이력에 보존. |
| [docs/product/data-quality-agent.md](product/data-quality-agent.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/decision-workspace-ui.md](product/decision-workspace-ui.md) | 명세 유지 | 자료 검토·금융 화면 연결과 남은 UX 과제. R1–R3의 주요 구현 기준. |
| [docs/product/deployment-plan.md](product/deployment-plan.md) | 수정·유지 | 실행 범위·현재 기준·과거 기록의 구분을 수정. |
| [docs/product/deployment-readiness-2026-09-15.md](product/deployment-readiness-2026-09-15.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/durable-agent-runtime-v1.md](product/durable-agent-runtime-v1.md) | 명세 유지 | 실행 상태·재시도·취소 계약 유지. |
| [docs/product/e2e-rehearsal-2026-07-06.md](product/e2e-rehearsal-2026-07-06.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/e2e-validation-2026-06-24.md](product/e2e-validation-2026-06-24.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/end-to-end-remediation-2026-09-14.md](product/end-to-end-remediation-2026-09-14.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/evidence-graph-agent.md](product/evidence-graph-agent.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/excel-workbook-learning-agent.md](product/excel-workbook-learning-agent.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/feasibility-study-v2-schema.md](product/feasibility-study-v2-schema.md) | 명세 유지 | 정규 입력과 수식 계보 계약 유지. 월별 모델 명세와 역할을 분리. |
| [docs/product/feasibility-workbook-validation.md](product/feasibility-workbook-validation.md) | 명세 유지 | 원본 수식 검증 기준 유지. |
| [docs/product/historical-similar-case-search.md](product/historical-similar-case-search.md) | 설계 유지 | 기존 기능·계약을 보존하고 첫 사용자 흐름에 필요한 확장만 우선. |
| [docs/product/identity-provider-integration.md](product/identity-provider-integration.md) | 명세 유지 | 이미 LEGACY로 표시된 호환 경로 참고. 운영 인증 설정에 사용하지 않음. |
| [docs/product/identity-tenant-access-contract.md](product/identity-tenant-access-contract.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/independent-codebase-audit-2026-09-14.md](product/independent-codebase-audit-2026-09-14.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/infrastructure-proposal-2026-09-15.md](product/infrastructure-proposal-2026-09-15.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/ir-deck-implementation-gap.md](product/ir-deck-implementation-gap.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/kakao-intake-workflow.md](product/kakao-intake-workflow.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/kakao-login-message-integration.md](product/kakao-login-message-integration.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/land-data-api-connector.md](product/land-data-api-connector.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/land-ledger-import-and-enrichment.md](product/land-ledger-import-and-enrichment.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/legal-evidence-package-workflow.md](product/legal-evidence-package-workflow.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/legal-pod-roadmap.md](product/legal-pod-roadmap.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/live-auth-smoke-runbook.md](product/live-auth-smoke-runbook.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/llm-provider-routing.md](product/llm-provider-routing.md) | 명세 유지 | 라우팅 정책 유지. 6월의 모델·무료 제공 예시는 현재 가격/가용성 근거가 아님. |
| [docs/product/maintenance-plan.md](product/realytics-os-next-work-roadmap.md) | 통합·삭제 | 단일 제품 실행 계획에 범위·출시·운영 기준 통합. 원문은 이력 보존. |
| [docs/product/map-intelligence.md](product/map-intelligence.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/market-data-integration.md](product/market-data-integration.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/marketability-agent-roadmap.md](product/marketability-agent-roadmap.md) | 명세 유지 | 분양형 자산의 가정·근거 명세 유지. 외부 데이터 없이 가능한 검토를 구분. |
| [docs/product/mobile-kakao-review-ux.md](product/mobile-kakao-review-ux.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/modular-document-parsing-platform.md](product/modular-document-parsing-platform.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/monetization-plan.md](product/monetization-plan.md) | 수정·유지 | 실행 범위·현재 기준·과거 기록의 구분을 수정. |
| [docs/product/mvp-definition.md](product/realytics-os-next-work-roadmap.md) | 통합·삭제 | 단일 제품 실행 계획에 범위·출시·운영 기준 통합. 원문은 이력 보존. |
| [docs/product/next-actions-2026-05-13.md](product/next-actions-2026-05-13.md) | 수정·유지 | 실행 범위·현재 기준·과거 기록의 구분을 수정. |
| [docs/product/next-work-audit-2026-07-03.md](product/next-work-audit-2026-07-03.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/operational-gate-validation-2026-07-27.md](product/operational-gate-validation-2026-07-27.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/pdf-print-layout-qa.md](product/pdf-print-layout-qa.md) | 수정·유지 | 실행 범위·현재 기준·과거 기록의 구분을 수정. |
| [docs/product/phase-11-pf-review-report.md](product/phase-11-pf-review-report.md) | 수정·유지 | 실행 범위·현재 기준·과거 기록의 구분을 수정. |
| [docs/product/pilot-e2e-2026-07-10.md](product/pilot-e2e-2026-07-10.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/pilot-hardening-notes.md](product/pilot-hardening-notes.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/pilot-latest-execution-status.md](product/pilot-latest-execution-status.md) | 수정·유지 | 실행 범위·현재 기준·과거 기록의 구분을 수정. |
| [docs/product/pilot-project-runbook.md](product/pilot-project-runbook.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/pilot-run-daegu-mixed-use-pf.md](product/pilot-run-daegu-mixed-use-pf.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/pod-agent-code-audit-2026-09-15.md](product/pod-agent-code-audit-2026-09-15.md) | 이력 유지 | 해당 revision의 통합 검증 기록. 현재 Agent·Pod 삭제의 근거가 아님. |
| [docs/product/pod-agent-control-audit-2026-09-15.md](product/pod-agent-control-audit-2026-09-15.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/pod-agent-evidence-audit-2026-09-15.md](product/pod-agent-evidence-audit-2026-09-15.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/pod-agent-financial-audit-2026-09-15.md](product/pod-agent-financial-audit-2026-09-15.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/pod-ai-reviewer-roadmap.md](product/pod-ai-reviewer-roadmap.md) | 설계 유지 | 기존 기능·계약을 보존하고 첫 사용자 흐름에 필요한 확장만 우선. |
| [docs/product/privacy-retention-support-policy.md](product/privacy-retention-support-policy.md) | 명세 유지 | 실제 자료 접근·보존·복구 계약 유지. 무관한 로컬 화면 수정을 막는 절차로 확대하지 않음. |
| [docs/product/production-hardening-2026-07-15.md](product/production-hardening-2026-07-15.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/project-751-recovery-preparation-2026-09-15.md](product/project-751-recovery-preparation-2026-09-15.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/project-751-source-recovery-review-2026-09-14.md](product/project-751-source-recovery-review-2026-09-14.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/project-snapshots.md](product/project-snapshots.md) | 명세 유지 | 입력·결과 버전 저장 계약 유지. 유사사례 검색과 역할을 분리. |
| [docs/product/publication-and-next-steps-2026-09-15.md](product/publication-and-next-steps-2026-09-15.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/real-estate-glossary-canonical-field-map.md](product/real-estate-glossary-canonical-field-map.md) | 명세 유지 | 용어·정규 필드 매핑 계약 유지. 용어 운영 문서와 역할을 분리. |
| [docs/product/realytics-design-system.md](product/realytics-design-system.md) | 수정·유지 | 실행 범위·현재 기준·과거 기록의 구분을 수정. |
| [docs/product/realytics-os-knowledge-learning-directive.md](product/realytics-os-knowledge-learning-directive.md) | 설계 유지 | 기존 기능·계약을 보존하고 첫 사용자 흐름에 필요한 확장만 우선. |
| [docs/product/realytics-os-next-work-roadmap.md](product/realytics-os-next-work-roadmap.md) | 현행 문서로 재작성 | 현재 목적·경로·구현 상태로 정리. 과거 내용은 통합 이력에 보존. |
| [docs/product/realytics-sdd-fullstack-operating-standard.md](product/realytics-sdd-fullstack-operating-standard.md) | 수정·유지 | 실행 범위·현재 기준·과거 기록의 구분을 수정. |
| [docs/product/recursive-optimization-loop.md](product/recursive-optimization-loop.md) | 설계 유지 | 기존 기능·계약을 보존하고 첫 사용자 흐름에 필요한 확장만 우선. |
| [docs/product/release-plan.md](product/realytics-os-next-work-roadmap.md) | 통합·삭제 | 단일 제품 실행 계획에 범위·출시·운영 기준 통합. 원문은 이력 보존. |
| [docs/product/remediation-execution-2026-09-14.md](product/remediation-execution-2026-09-14.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/risk-register-agent.md](product/risk-register-agent.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/runtime-activation-gates-2026-09-14.md](product/runtime-activation-gates-2026-09-14.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/runtime-activation-progress-2026-09-15.md](product/runtime-activation-progress-2026-09-15.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/runtime-login-preflight-hardening-2026-09-15.md](product/runtime-login-preflight-hardening-2026-09-15.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/sample-excel-similarity-checklist.md](product/sample-excel-similarity-checklist.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/server-storage-and-user-audit-log.md](product/server-storage-and-user-audit-log.md) | 명세 유지 | 저장·감사 인벤토리/이력. 현행 권한은 architecture-boundary와 개인정보 계약 기준. |
| [docs/product/service-development-roadmap.md](product/realytics-os-next-work-roadmap.md) | 통합·삭제 | 단일 제품 실행 계획에 범위·출시·운영 기준 통합. 원문은 이력 보존. |
| [docs/product/smart-intake.md](product/smart-intake.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/social-login-setup.md](product/social-login-setup.md) | 명세 유지 | 이미 LEGACY로 표시된 직접 OAuth 참고. 현행 인증 계약 우선. |
| [docs/product/supabase-integration.md](product/supabase-integration.md) | 수정·유지 | 실행 범위·현재 기준·과거 기록의 구분을 수정. |
| [docs/product/term-standard-database-operating-model.md](product/term-standard-database-operating-model.md) | 명세 유지 | 용어 DB 운영·후보 검토 모델 유지. |
| [docs/product/ui-ux-gap-review-2026-07-27.md](product/ui-ux-gap-review-2026-07-27.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/workflow-state-model.md](product/workflow-state-model.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [docs/product/workspace-cleanup-2026-09-14.md](product/workspace-cleanup-2026-09-14.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/workspace-cleanup-2026-09-28.md](product/workspace-cleanup-2026-09-28.md) | 이력 유지 | 해당 날짜·revision의 결과. 현재 우선순위는 실행 계획을 따름. |
| [docs/product/workspace-page-data-contract.md](product/workspace-page-data-contract.md) | 명세 유지 | 공유 프로젝트 문맥 계약 유지. 수치의 최종 근거는 정확한 계산·입력 버전으로 확인. |
| [packages/eslint-config/README.md](../packages/eslint-config/README.md) | 명세 유지 | 고유 기능·설정·운영 계약으로 유지. 구현 시 현재 코드와 대조. |
| [README.md](../README.md) | 현행 문서로 재작성 | 현재 목적·경로·구현 상태로 정리. 과거 내용은 통합 이력에 보존. |
| [services/api-server/agents/llm_agent_policies.md](../services/api-server/agents/llm_agent_policies.md) | 런타임 유지 | agent_policy가 읽는 프롬프트. 문서 정리로 내용이나 동작을 바꾸지 않음. |
| [services/api-server/README.md](../services/api-server/README.md) | 현행 문서로 재작성 | 기존 가상환경·격리 테스트·실제 API 구조에 맞게 교체. |
| [REALYTICS_PLATFORM9_Service_Development_Roadmap.md](../../REALYTICS_PLATFORM9_Service_Development_Roadmap.md) | 수정·유지 | 실행 범위·현재 기준·과거 기록의 구분을 수정. |

## 이번 진단으로 내린 제품 판단

기술을 처음부터 다시 만들 필요는 없다. 기존 Pod·Agent가 자료 검토, 계산, 보완사항, 보고서에 제공하는 결과를 연결하면 첫 실무 도구를 만들 수 있다. 가장 먼저 검증할 작업은 **R1: 한 자료 묶음의 숫자·출처 확인, 수정, 저장, 재개**다. 다음은 같은 기준의 가정 비교와 검토 초안이다. 자동 코딩, 전 조직 운영 기반, 과금, 모든 외부 API의 완성을 먼저 요구하지 않는다.

2026-09-28 사용자가 R1 구현과 GitHub 반영을 승인했다. 기존 사업수지 검토 영역의 첫 구현 범위와 남은 실제 자료 검증은 [제품 실행 계획](product/realytics-os-next-work-roadmap.md)에 기록한다. 문서 정리만으로 제품 전체를 완성했다고 주장하지 않는다.

## 이번 변경 검증

- Web과 공유 UI의 TypeScript 검사 및 경고 없는 lint 통과.
- 기존 Excel/프로젝트 연결 계약 검사 **5개 통과**.
- 기존 Chief Pod 스키마·실패 무결성·Agent Platform 계약 테스트 **31개 통과**. 임시 런타임에서 실행했으며 외부 유료 서비스는 호출하지 않았다.
- 현재 저장소 Markdown **109개**의 로컬 파일 링크 오류 0개. 바깥 초기 기획 문서도 현행 계획으로 연결했다.
- npm의 활성 workspace는 Web과 공유 패키지이며, 제거한 docs 앱의 lockfile 항목이 남지 않았다.
- Agent·Pod 구현, use_cases, core, schemas, API tests에는 Git 변경이 없다. PF 단계 수는 AST로 **27개**임을 확인했다.
- 전체 API·브라우저·운영 배포를 재검증한 것은 아니다. 해당 기능을 새로 변경할 때 필요한 범위의 검증을 수행한다.

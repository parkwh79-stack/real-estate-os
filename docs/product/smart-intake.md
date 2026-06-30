# Smart Intake (AI 문서 자동 분류)

Updated: 2026-06-29

가진 자료를 종류 구분 없이 한 번에 올리면 파서 에이전트가 문서 유형을 자동 분류하고
원본을 보관한다. 기존의 "유형별 슬롯에 파일 하나씩" 방식의 불편을 대체한다.

## 분류기 (deterministic)

`agents/document_classifier_agent.py` `classify_document(filename, content_type,
size, sheet_names, json_keys, sample_text)`:
- 신호: 파일명/확장자 prior + 스프레드시트 **시트명**(사업수지/현금흐름/토지조서…) +
  **JSON 키**(officialLandPrice/registry…) + (선택) 본문 sample.
- 출력: `documentType` + 한글 `label` + `confidence`(0–1) + `needsReview` +
  `reasons`(설명가능) + `candidates`(랭킹).
- 유형: business_plan / basic_architectural_plan / land_ledger /
  feasibility_workbook / risk_report_workbook / land_evidence / market_evidence /
  unknown. 각 유형은 `suggestedAction`(upload_and_parse / land_ledger_import /
  feasibility_workbook_validate / …)을 가진다.
- **AGENTS.md 준수**: 규칙 기반이 근간, 설명가능, 숫자/판단을 만들지 않음. LLM은
  향후 모호 케이스 보조용(게이트)으로만 — 결정론 증거를 덮어쓰지 않는다.

## 엔드포인트

`POST /projects/{id}/intake/classify` (multipart, `files[]`): 각 파일을 sniff
(xlsx 시트명 via openpyxl, json 키) → 분류 → 원본을 업로드 아카이브에 보관 +
source-document 메타에 분류 결과 기록 → `{count, byType, needsReviewCount,
files[]}` 반환. 비조작: 보관 실패해도 분류는 반환, 미지원 파일은 unknown.

## 프론트엔드

`components/intake/smart-intake.tsx` — 드래그앤드롭/다중 선택 업로드, AI 분류 결과
표(파일 / AI 분류 / 신뢰도 / 유형 수정 select / 보관 여부). 입력패키지 페이지
(`/project/[id]/input-package`) 상단 주력 섹션으로 배치하고, 기존 유형별 업로드는
"개별 업로드(고급)"로 강등. 페이지 전체 국문화. `initialFiles?: File[]` prop을 주면
마운트 시 해당 파일을 자동으로 분류한다(대시보드 진입점에서 전달).

## 대시보드 진입점 (로그인 후 첫 화면) — Done 2026-06-29

로그인 후 첫 화면(`/dashboard`)을 "프로젝트 관리 표" 중심에서 **"자료 한 번에 올리고
심사 시작"** 히어로 중심으로 전환. `components/intake/dashboard-intake.tsx`
(`DashboardIntake`): 상단 드롭존에 자료를 올리면 → ① 초안 프로젝트를 즉석 생성
(`POST /projects/`, 이름은 첫 파일명에서 유도, `status: draft`) → ② 생성된 projectId로
`SmartIntake`를 `initialFiles`와 함께 렌더해 **드롭한 파일을 바로 AI 분류**. 생성되면
대시보드 프로젝트 목록을 새로고침(`onProjectCreated`)하고 "심사 상세로 이동" 링크 노출.
프로젝트 생성 중에는 "심사 공간을 준비하고 있어요…" 로딩 표시. 기존 KPI·프로젝트
카드 목록은 히어로 아래로 유지.

> 알려진 한계: `POST /projects/` 응답이 현재 ~13초로 느림(스토어 전체 persist 추정,
> `storage.py` 주입 persist). 별도 백엔드 성능 작업 대상. 그동안엔 로딩 표시로 안내.

## 분류대로 처리 (자동 라우팅) — Done 2026-06-25

스마트 업로드 표의 **"분류대로 처리"** 버튼이 각 파일을 (사용자가 수정 가능한)
유형대로 기존 파서 엔드포인트에 자동 전송한다 — 백엔드 추가 없이 재사용:
business_plan/기본설계→`/upload`, land_ledger→`/land-ledger/analyze`,
feasibility_workbook→`/feasibility-workbook/validate`,
risk_report_workbook→`/risk-report-workbook/validate`, land_evidence(JSON)→
`/land-evidence`. market_evidence/unknown은 보관만. 행별 상태(처리중/완료/보관만/
실패)를 표시하고, 완료 후 입력패키지 상태를 새로고침. 클라이언트가 원본 File을
보관했다가 재전송하므로 분류→처리가 한 화면에서 끝난다.

## Next

- 처리 결과(파싱/검증 산출물)를 표에 인라인 요약.
- 유형 수정(override) 결과를 분류기 학습 신호로 (human-gated optimization-log).
- LLM 보조 분류(모호 케이스, ai_router 게이트).

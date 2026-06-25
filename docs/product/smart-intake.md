# Smart Intake (AI 문서 자동 분류)

Updated: 2026-06-25

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
"개별 업로드(고급)"로 강등. 페이지 전체 국문화.

## Next

- 분류 결과의 `suggestedAction`대로 **자동 라우팅**(사업계획서→파싱,
  토지대장→임포트, 사업수지→검증)을 한 번에 실행하는 "분류대로 처리" 버튼.
- 유형 수정(override) 결과를 분류기 학습 신호로 (human-gated optimization-log).
- LLM 보조 분류(모호 케이스, ai_router 게이트).

# 증거·시장·법률·리포트 Agent 코드 감사

검토일: 2026-09-15. 유료 인프라 없이 실행한 로컬 코드/회귀 감사 기록이다. 이 문서의 범위는 아래 21개 Agent와 직접 관련 테스트이며, 운영 적합성 인증이나 전체 코드의 무결점 판정이 아니다.

## 실행 및 범위

- API 테스트의 `conftest.py`가 생성하는 임시 SQLite·파일 경로만 사용했다. 외부 AI/법률/시장 연결과 개발 자격증명은 테스트 설정에서 비활성화한다.
- 새 클라우드 자원, 유료 API 호출, 원본 업무자료·업무 DB 변경은 수행하지 않았다. 테스트 PDF는 메모리에서 만든 합성 문서이고 baseline은 pytest 임시 폴더에만 작성했다.
- 21개 모듈의 입력·출력 경계와 테스트 연결을 조사했다. 아래 표의 테스트는 기능 검증 연결이지 line/branch coverage 수치가 아니다. 금융 계산 공식·정책 임계치, orchestrator/worker 구현은 별도 담당 범위다.
- `tests/test_evidence_agent_audit.py`에 39개 매개변수화 회귀를 추가했다. 초기 핵심 19개 중 18개 실패, 추가 리포트·리스크 5개 모두 실패, PNU/수집시각 3개 실패를 수정 전에 확인했다. 시장/표시 정규화 회귀는 수정 후 검증을 추가했다.

## 재현한 오류와 수정

| 영역 | 수정 전 재현 | 수정 결과 |
| --- | --- | --- |
| 법률 FAR/BCR | 문자열 `200`/`90`을 초과로, `900`/`1000`을 정상으로 비교 | 숫자·별칭·`value` wrapper를 정규화하여 비교. 누락·비유한 값은 caution 또는 not_assessable |
| 법률 개발가능성 | 법정 FAR=`NaN`/`Infinity`로 conditionally_feasible, 일부 잘못된 참조 목록에서 예외 | 비유한 값 제외, 참조 행 유형 검사. 대지면적·허가일 canonical 경로를 land/schedule로 정정 |
| 데이터 품질 | NaN/+Infinity 두 출처 비교가 clear, 사업명 0건도 clear | 유한 숫자만 비교하고 boolean 제외. 근거 부족은 missing_data, 혼합 유효·무효 출처는 warning. 누락 사업명도 missing_data |
| 시장 비교사례 | 거리 0m를 9999m로 바꾸어 조정가·유사도가 변경 | 명시적 0을 보존하고 누락값에만 기존 기본값 적용. 숫자 파서의 NaN/Infinity 거부, 잘못된 사례 행 제외 |
| 시장 출처·문서 표시 | 비유한 confidence/페이지 번호가 통과하거나 예외; 크레딧/Term Sheet 숫자 표시에서 NaN/Infinity 노출 | confidence 유효 범위 밖은 metadata partial, locator 정수 변환 안전화. 크레딧/Term Sheet 숫자 표시의 비유한 값은 미확인 표시 |
| 보고서 누락 값 | 입력이 없는데 PF금액·LTV·개발이익·마진·현금흐름·납입비율이 0으로 생성 | 구조화 보고서는 None, Markdown은 미확인. 명시적 정상 숫자는 기존 계산식을 사용. 백만원 변환도 None 보존 |
| 보고서 테스트 기대값 | 수입100/비용90인데 `expected_results.development_profit=999`가 보고서 본문을 덮음 | 원입력 수입·비용으로만 계산. 계산에 사용하는 canonical 입력 경로를 `metricSourceFields`에 보존 |
| 보고서 확정 표현 | 소유권 자료가 없는데 지분100%, 빈 자료에서 자금 부족 충당 가능·목표 분양률 달성 가능·보증구조 확보 문장 | 미확인 지분을 None으로 유지. 근거 없는 확정 문장을 증빙 필요 표현으로 변경. 기본 자금흐름 분석 부재는 not_assessable |
| 리스크 집계 | artifacts={}에서 clear / approval_possible | 기존 데이터 제약(현금흐름/시나리오 결과 부재)이 있고 위험이 0건이면 needs_review / insufficient_evidence |
| VisualQA | baseline 생성 자체를 passed/비교완료로 표시; 1→2 및 2→1 페이지 변경 모두 passed | 최초 baseline_created, pages_compared=0, matched_pages=0. 추가·삭제 페이지를 regression_detected로 기록하고 기존 baseline을 자동 확장하지 않음 |
| VisualQA 경로/비교 | report_id의 경로 구분자 허용, RGB가 아닌 baseline 비교 불일치 가능 | 단일 파일 식별자와 양의 정수 dpi 검사. 양쪽 RGB/비알파 비교. 기존 동일 PDF 비교와 성능 회귀 유지 |
| 토지 보강 | 다른 PNU라도 동일 sequence fallback으로 타 필지 기록 첨부 | 양측 PNU가 충돌하면 sequence/address fallback을 거부. 일치 PNU 연결은 유지 |
| 토지 증거 시각 | 미제공 fetchedAt을 현재 시각으로 만들어 수집 시각처럼 표시 | 제공된 과거 fetchedAt 유지, 미제공은 None. 적용/첨부 시각은 별도 recordedAt으로 기록 |

## Agent별 테스트 연결

경로는 `services/api-server/agents/`, 테스트는 같은 서비스의 `tests/` 기준이다.

| Agent | 확인한 경계 / 이번 변경 | 직접 또는 관련 테스트 |
| --- | --- | --- |
| market_evidence_agent | 비교사례·출처·locator 숫자 정규화 수정 | test_marketability_and_legal_agents, test_evidence_agent_audit |
| marketability_agent | 유효 숫자, 사례 행 유형, 0m, 출처 confidence 수정 | test_marketability_and_legal_agents, test_evidence_agent_audit |
| market_product_depth_agent | 누락 비교근거 및 잘못된 흡수율 기존 계약 확인; 코드 변경 없음 | test_market_product_depth |
| product_planning_agent | null 가격·벤치마크 구분, 필지별 FAR가 오래된 상위값보다 우선하는 기존 회귀; 변경 없음 | test_product_planning |
| legal_review_agent | FAR/BCR 누락·문자열·정규화 수정 | test_marketability_and_legal_agents, test_evidence_agent_audit |
| legal_developability_agent | 비유한 값·참조 유형·canonical 경로 수정; 기존 직접 import 테스트 부재 보완 | test_evidence_agent_audit + LegalReview 간접 테스트 |
| land_ledger_agent | 합성/익명 workbook 및 빈/잘못된 파일 파싱 경계; 변경 없음 | test_land_ledger_agent |
| land_ledger_enrichment_agent | 필지 매칭·수집 시각 보존 수정 | test_land_data_connectors, test_evidence_agent_audit |
| data_quality_agent | 숫자 출처 일치·누락 입력·비유한 값 수정 | test_data_quality_agent, test_evidence_agent_audit |
| evidence_graph_agent | 증거/출처/위험/리포트 연결 구조 확인; 변경 없음 | test_evidence_graph_agent |
| risk_register_agent | 비어 있는 집계를 승인 가능으로 오인하는 상태 수정 | test_risk_register_agent, test_evidence_agent_audit |
| report_agent | 구조화 및 Markdown 누락값·출처·확정 표현 수정 | test_report_agent, test_scenarios, test_schedule_normalization, test_orchestration_engine_exposure, test_evidence_agent_audit |
| report_export_agent | JSON/HTML/PDF/DOCX 및 None 표시 경계 확인; 변경 없음 | test_report_agent, test_visual_regression |
| credit_document_agent | 비유한 숫자 표시 수정 | test_credit_document_agent, test_term_sheet_stages, test_evidence_agent_audit |
| term_sheet_agent | 비유한 숫자 표시 수정; 기존 간접 테스트에 직접 helper 회귀 추가 | test_term_sheet_stages, test_evidence_agent_audit |
| term_annotation_agent | 빈 입력·canonical 필드/locator 연결 기존 계약; 변경 없음 | test_term_annotation_agent |
| document_classifier_agent | unknown 및 needsReview와 승인 판정의 구분; 변경 없음 | test_document_classifier, test_document_taxonomy |
| intake_triage_agent | 파일 신호 추천·빈 업로드의 기본 제안 설명·내용판단 제한; 변경 없음 | test_intake_triage_agent |
| parser_agent | 분할 텍스트 보존·출처 locator·익명 fixture 계약; 변경 없음 | test_lossless_chunk_extraction, test_parser_regression_fixtures |
| excel_workbook_learning_agent | unknown workbook 수동 검토·자동 승격 shadow-mode 경계; 변경 없음 | test_excel_workbook_learning_agent |
| visual_qa_agent | baseline/페이지 증감/경로/dpi 및 기존 동일문서 비교 수정 | test_visual_regression, test_scenarios, test_evidence_agent_audit |

## 검증 결과

- 첫 수정 후 4개 관련 테스트 파일: **54 passed**.
- 리포트·리스크 포함 중간 검증: **46 passed, 1 failed**. 최초 VisualQA baseline 생성은 `baseline_created`, `qualityCheck.pass=false`, `run.completed_with_warnings`로 기대값을 정렬했다.
- 23개 파일 선택 회귀: **201 passed, 4 failed (205개)**. 후속 확인에서 4건의 실제 오류는 모두 정상 시나리오 테스트 fixture의 비용합계 누락으로 인한 `cash_flow_cost_total_mismatch`였다. 동시 금융 검증 강화가 누락된 비용을 자동 배분하지 않고 차단한 결과다.
- 테스트 fixture만 명시적으로 보완했다. VisualQA 정상 fixture: 총1200억=토지300억+공사700억+금융100억+기타100억. 엔진 노출 fixture: 총2050=토지640+공사850+금융120+기타440. 운영 예시나 실제 사업 자료는 변경하지 않았다.
- 실패했던 통합 4건 재실행: **4 passed (25.54초)**. 따라서 선택 검증 205개는 최초 통과 201개와 수정 후 재검증 4개로 모두 확인했다. 신규 회귀 39개도 통과했다. 전체 API 회귀는 공통 작업에서 별도로 수행한다.
- 소유 변경 파일의 `git diff --check` 통과.

```powershell
cd services/api-server
../../.runtime/api-venv/Scripts/python.exe -m pytest tests/test_evidence_agent_audit.py tests/test_marketability_and_legal_agents.py tests/test_market_product_depth.py tests/test_product_planning.py tests/test_land_ledger_agent.py tests/test_land_data_connectors.py tests/test_data_quality_agent.py tests/test_evidence_graph_agent.py tests/test_risk_register_agent.py tests/test_report_agent.py tests/test_visual_regression.py tests/test_credit_document_agent.py tests/test_term_sheet_stages.py tests/test_term_annotation_agent.py tests/test_document_classifier.py tests/test_document_taxonomy.py tests/test_intake_triage_agent.py tests/test_lossless_chunk_extraction.py tests/test_parser_regression_fixtures.py tests/test_excel_workbook_learning_agent.py tests/test_scenarios.py tests/test_schedule_normalization.py tests/test_orchestration_engine_exposure.py -q --tb=short
```

## 남은 경계와 후속 검증

- 다른 project/run/snapshot의 Agent 결과는 공통 orchestration 입력·재사용 경계에서 차단해야 한다. 개별 Agent는 프로젝트 식별자를 받지 않는 함수도 있으므로 로컬 Agent 테스트만으로 이 경계의 통과를 주장하지 않는다. 병행 담당의 공통 경계 회귀와 전체 API 통합 결과를 함께 확인한다.
- 일반 시장 비교자료의 유효기간 정책을 임의의 일수로 만들지 않았다. 기존 날짜/출처를 보존하지만 오래된 모든 거래자료를 자동 배제하지는 않는다. 법률 공식 근거 검증은 기존 hash/date-bound 검증 경계를 유지한다.
- VisualQA baseline 저장은 기준 이미지 생성이며 승인된 시각검토의 증거가 아니다. 이후 동일 기준 이미지와의 pixel 비교 통과도 사람의 콘텐츠 승인과 구분해야 한다. 생성·승인된 baseline의 버전/승인 이력은 별도 운영 절차가 필요하다.
- 보고서의 `qualityChecks.isComplete`는 기존 20개 섹션 존재 검사다. 증거 완전성이나 금융 승인 의미가 아니다. 금융 헤드라인 누락을 None으로 정정했지만 모든 중첩 입력 유형, 모든 문구·시나리오 경로, 모든 개별 Agent의 직접 호출을 완전 검증한 것은 아니다.
- 소유권 `share`가 전체 부지 소유자 지분인지 필지별 지분인지의 입력 계약, 부분 누락 tranche 총액의 크레딧 문서 표현, typed input 오류의 공통 검증은 추가 범위로 남긴다. 이번에 확인·수정한 오류의 통합 회귀를 먼저 완료한다.

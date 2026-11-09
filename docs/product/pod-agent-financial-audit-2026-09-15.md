# 금융·사업성 Pod/Agent 코드 점검 — 2026-09-15

## 범위와 원칙

사용자 요청에 따라 유료 서비스·인프라 작업보다 로컬 Agent 코드의 오류 재현과 수정을 먼저 수행했다. 아래 11개 Agent와 직접 연결된 현금흐름/계산 코드를 읽고 정상 입력, 빈 근거, 0, 음수 원금, NaN/Infinity, 단위, 상환 순위, 부모 계산 불일치를 점검했다. 이 문서는 제한된 코드 감사 결과이며 모든 금융 정책과 모든 입력 조합에 대한 인증이 아니다.

원본 프로젝트 751 DB·워크북·승인 상태는 변경하지 않았다. 테스트는 `tests/conftest.py`의 임시 SQLite·로컬 파일·외부 자격증명 제거 격리를 사용했다. 유료 API 호출, 클라우드 생성, 원격 업로드, Git commit/push는 수행하지 않았다. 최종 backlog와 통합 검증은 root 작업 범위다.

## 검사 대상별 결과

| 대상 (`services/api-server/`) | 읽기·검증 범위 | 결과 |
| --- | --- | --- |
| `agents/development_feasibility_agent.py` | 프로그램/수입·비용/개발이익/PF 준비도/V2 draft→현금흐름 연결, 정상 개발·물류 예제 | NaN/Infinity 입력을 계산 전에 거부. CF 원가 대사 불일치만 부분검토로 제공하며, 현금흐름 결과는 `None`, downstream 준비도는 false로 둔다. |
| `agents/developability_score_agent.py` | 규제·토지·사업성·시장성 점수의 근거와 숫자 fallback | 원가 누락을 0으로 채워 100% 이익률을 만드는 경로 제거. NaN 시장점수는 미확보 자료로 처리. |
| `agents/credit_review_agent.py` | 심사 게이트/스트레스/수익률·LTV/보고 문구 | 필수 게이트의 빈 입력을 승인하지 않고 보완 후 재심으로 표시. 비유한 숫자는 복사본에서 `None`으로 남기고 구체 경로를 제한사항에 기록. 기존 부결 권고는 유지. |
| `agents/credit_enhancement_agent.py` | 근거 유무/보강효과/신용등급·DSCR | 빈 문자열 Evidence ID와 빈 locator 객체는 보강효과를 활성화하지 않음. NaN/Infinity 사전 거부. |
| `agents/covenant_eod_agent.py` | 약정 임계값/순위/공사비 초과 버퍼/source locator | 명시적으로 입력된 `costOverrunBuffer=0`을 기본 5%로 바꾸지 않음. |
| `agents/covenant_monitoring_agent.py` | ratio 임계값/DSCR 적용 제외/경고·상태·단위 | NaN 지표를 정상으로 판정하지 않고 평가불가 경고/watch 처리. 비유한 임계값 거부. DSCR 경고를 `1.00x`로 표시. 기존 DSCR≤0 적용 제외 정책은 유지. |
| `agents/cash_sweep_agent.py` | 월별 이자/준비금/원금상환/동일 tranche 이름/0·음수 | 동일 이름으로 원금이 dict에서 덮어써지는 입력과 음수 원금 거부. 초기 원금 0의 상환요약 모순 수정. NaN/Infinity 거부. |
| `agents/stress_test_agent.py` | 기본/민감도 시나리오, bound cash flow의 timing, IRR fallback | NaN/Infinity 거부. 부모 현금흐름과 다른 매출·토지·공사·금융·기타 비용을 거부. 금융 입력 오류가 uniform fallback으로 성공하지 않도록 재전파. |
| `agents/scenario_engine_agent.py` | Waterfall/미분양 LTV/회수 matrix/단위·규칙 | 이진 부동소수점 오차로 20실이 19실이 되는 오류 수정. KRW 외 통화·금액 단위와 미구현 custom WaterfallRule은 명시적 입력오류로 차단. NaN/Infinity 거부. |
| `agents/restructuring_agent.py` | 선순위 감액/담보 cure/기본 시나리오와 부모 ID | 이미 입력된 추가담보를 cure에 포함. priority=1 하드코딩 대신 실제 선순위 tranche 사용. 0 버퍼 유지. 다른 scenarioSetId의 결과 거부. |
| `agents/deal_optimization_agent.py` | 법규 gate/이익률·LTV gap/대안 레버와 verdict | 원가 미확보 시 승인 가능 금지. 공사비 절감 또는 분양가 인상을 같은 이익률 gap의 대안으로 처리. 보강 후 LTV=0 유지. 비유한 점수는 숫자 근거로 채택하지 않음. |

## 직접 연결된 계산 경계

- `agents/cash_flow_engine.py`: 월별 매출·원가 스케줄, PF draw, 계정 배분, Max Minus, 총액 요약, report eligibility를 읽었다. 선언된 양수 `totalProjectCost`와 실제 월별 원가 합계가 다르면 계산 전에 `FinancialInputError`로 차단한다. 비교는 `math.isclose(rel_tol=1e-9, abs_tol=1e-6)`의 숫자 오차 허용만 사용하며 금융 중요성 임계값을 추가하지 않았다. NaN/Infinity도 거부한다.
- `agents/cash_flow_mapper.py`: V2 수입·비용 항목, 금융/Equity/Bridge 분류, 월별 실제 비용 이벤트, source locator, 승인 여부의 draft 전달을 읽었다. 이 파일의 계산/배분을 수정하지 않았다. 기존 financing·월별 horizon 테스트를 함께 실행했다.
- `core/calculations.py`: 평·㎡, 매출/감정/미분양 LTV의 분모 구분, LTC/DSCR, Max Minus, Waterfall, XIRR/IRR과 수익 지표를 읽었다. dated XIRR에서 비유한·누락·잘못된 금액을 `invalid_cashflow`로 처리하는 범위만 수정했다. 금리·이익률·LTV 정책과 IRR 해법을 변경하지 않았다.
- `use_cases/cash_flow_dependencies.py`: `allow_nan=False`로 정렬된 입력 해시 생성, 부모 계산 ID 전달, 복사본을 통한 stress scaling, 변경된 숫자의 기존 승인 제거를 읽었다. 같은 부모라도 다른 hash를 가진 export 선택을 거부하는 기존 회귀 테스트를 실행했다. 이 파일은 수정하지 않았다.
- `core/financial_input_validation.py`: 새 `FinancialInputError(ValueError)`와 비유한 숫자 검사를 제공한다. 일반 내부 `ValueError`와 분리해 root가 API 입력오류 처리를 한정할 수 있도록 했다. API handler 및 미저장 검증은 별도 root 범위다.
- 개발사업성의 부분검토 역할을 보존하기 위해 `cash_flow_cost_total_mismatch` 오류 코드만 구조화된 `dataQuality.blockingIssues`/`missingFields`로 전환한다. 상태는 `completed_with_review`/`partial`, `cashFlowRunResult=None`, PF 심사 준비 및 후속 계산 가능 여부는 false다. 다른 금융 입력오류는 삼키지 않으며 CashFlow engine 직접 실행은 계속 거부한다.

## 실제 재현 → 수정

첫 회귀 실행에서 신규 18개 사례가 모두 실패했음을 확인한 뒤 수정했다.

| 재현 입력 | 수정 전 | 수정 후 |
| --- | --- | --- |
| `CreditReviewAgent.decide({}, {})` | `approve`, 사람 검토 불필요 | 필수 risk/legal/data quality/reconciliation gate 누락을 기록하고 재심 |
| 정상 gate + `exit_sale_rate=NaN` | 승인 가능·NaN 노출 | 재심·미확정 값 `None`, JSON 안전 |
| 매출 100만 존재, 원가 없음 | 사업성 차원 100% 이익률 | 사업성 근거 미확보 |
| 시장점수 NaN | 100점 | 근거 미확보 |
| 보강근거 `[""]`, `["  "]`, `[{}]` | 책임준공 보강 활성화·LTV 5%p 감소 | `needs_evidence`, 효과 0 |
| Cash Sweep `Senior` 원금 100, 200 두 항목 | 이름 충돌로 원금 100 소실 | 고유 이름 요구 |
| Cash Sweep 원금 0 | 결과는 0개월 상환, 문구는 미상환 | 문구와 계산 일치 |
| NaN covenant LTV·임계값 | 정상 판정 가능 | 지표 평가불가 경고 또는 임계값 입력오류 |
| DSCR=1.0 | `100.00%` 경고 | `1.00x` 경고 |
| 공사비 1000, 비용 버퍼 0 | 약정 한도 1050 | 한도 1000 |
| 공사비 절감 불가·분양가 인상 가능 | 두 대안을 동시에 요구하여 부결 | 같은 gap의 실행 가능한 대안으로 협의 가능 |
| 100실·분양률 0.8 | 미분양 19실 | 미분양 20실; 기존 정수 버림 원칙 유지 |
| PF 800·미분양 가치 200·추가담보 100·목표 LTV 0.9 | 이미 제공된 담보 100을 다시 요구 | 담보 합계 300 기준 감액 530·추가담보 약589 |
| 선순위 priority=5, Senior buffer=0 | 선순위 누락·기본 10억원 버퍼 | 실제 선순위 확인·명시 버퍼 0 |
| 다른 scenarioSetId의 부모 결과 | 결과 사용 | 입력오류 |
| dated XIRR `[-100, 110, NaN]` | XIRR 100%, `calculated` | `value=None`, `invalid_cashflow` |
| 보강 전 LTV=NaN | 보강 후 LTV 0 | 계산 전 입력오류 |
| Cash Sweep 원금 -10 | 음수 잔액 출력 | 입력오류 |
| stress 토지비 100, 부모 현금흐름 토지비 200 | 표시 비용 100·IRR 비용 200 | 계산 전 부모 금액 불일치 오류 |
| USD 또는 `MILLION_KRW`·미구현 WaterfallRule | KRW/default 규칙으로 완료 | 지원하지 않는 계약을 거부 |
| 총사업비 600·월별 비용 0 | 비용 요약 600·Max Minus 0·completed | 원가 대사 입력오류, 배분 생성 안 함 |

## 검증

- 신규 회귀: `services/api-server/tests/test_financial_agent_input_regressions.py`.
- 11개 Agent의 기존 테스트와 `test_calculations.py`, `test_cash_flow_dependencies.py`, `test_cash_flow_mapper_financing.py`를 함께 실행: **159 passed, 1 deselected**. 제외한 `test_orchestration_quality_check_visual_qa`는 root가 병행 수정 중인 실행 상태 통합 검증으로, 기존 완료 기대가 root의 실패 보존 변경과 충돌하여 전달했다.
- 이후 원가 대사를 숫자 오차 기준으로 좁히고 stress fallback 재전파 회귀를 추가한 최종 관련 6개 파일 실행: **72 passed**. 이는 앞선 검증을 대체하는 전체 검증이 아니라 해당 후속 변경 범위 재검증이다.
- 개발사업성 부분검토 처리 후 신규 38개 회귀, 개발사업성·Covenant/EOD·Stress 기존 테스트 및 root의 API 422/미저장 테스트 실행: **62 passed**. 실제 부분분석은 유지하면서 가짜 CF 결과를 만들지 않고, 다른 입력오류를 재전파하는 것을 검증했다.
- 정상 fixture 두 곳의 누락 비용을 보완했다. `test_covenant_eod.py`의 총850억원에 빠진 토지비300억원, `test_cash_flow_dependencies.py`의 총80에 빠진 월0 토지비20을 명시했다. 테스트 기대값을 느슨하게 만들지 않았다.
- root가 추가로 양도한 `test_agent_platform_operational_runtime.py`의 정상 durable workflow fixture에도 총사업비800의 공사비800을 명시했다. production 비용 배분을 생성하는 대신 테스트 입력만 완전하게 만들었다.
- 해당 durable workflow 단일 회귀: **1 passed**.
- 전체 API 회귀 후 추가 양도된 `test_product_collection_schedule.py`의 집계 수납 스케줄 fixture는 총사업비600만 있고 비용 항목이 없었다. 같은 파일의 정상 예제와 동일하게 공사비600을 명시했으며 파일 전체 **3 passed**를 확인했다. mapper와 비용 대사 guard는 변경하지 않았다.
- 원래 `python_multipart`/httpx 사용 중단 예정 경고만 관찰했다. 원격 DB·Supabase 검증은 수행하지 않았다.

## 남은 제한과 필요한 업무 판단

1. **단위와 custom WaterfallRule 지원**: 현재 Scenario는 KRW만 처리하며 custom 규칙을 거부한다. 통화·단위 변환의 원본 근거와 세금/필수사업비/이자/원금/후순위 우선순위 계약을 명세한 뒤 구현해야 한다. 거부된 요청에 임의 기본 규칙을 적용하지 않는다.
2. **미배분 사업비**: 총액과 실제 월별 비용이 다르면 계산을 막는다. 어느 항목·월에 비용을 배분할지는 원본·검토자 결정을 요구한다. 원가와 VAT·예수보증금·순수 금융이동의 포함 범위를 임의로 정하지 않는다.
3. **0 분모와 적용 제외 정책**: 기존 core ratio helper의 0 분모 반환값, CovenantMonitoring의 DSCR≤0 적용 제외, 100% 분양 시 미분양 LTV 정의는 이번 변경에서 재정의하지 않았다. 임대 NOI가 실제 0인 경우와 분양형 프로젝트의 미적용을 구분하는 명시적인 입력 계약이 필요하다.
4. **일부 ScenarioDrivers**: `discountRates`는 미분양 matrix에서 쓰지만 Waterfall의 [0, 0.1], 회수 matrix의 [0, 0.1, 0.2] grid는 기존 고정값이다. 각 matrix가 같은 driver grid를 공유해야 하는지 또는 고정 비교 시나리오인지 명세가 필요하다. `unsoldRates`·`recoveryHaircuts` 전체 계약도 별도 확정 대상이다.
5. **근거 승인·부모 권한**: 빈 근거 제거는 문서의 진위·승인을 검증한 것이 아니다. 부모 ID/금액 일치 검사는 저장된 tenant 범위의 원본·승인·hash 검증을 대체하지 않는다. repository/API의 승인·권한 경계는 별도 통합 검증 범위다.
6. **호환 경로와 전체 경계**: 기본 prototype 일정·금리 추정, unbound stress의 기술 오류 시 uniform fallback, 광범위 core 숫자 파서, 각 요청의 음수 금리/음수 매출 등의 업무 허용 범위를 전면 개편하지 않았다. 이번 결과를 전체 금융 모델의 운영 승인으로 해석하면 안 된다.

# E2E 통합 리허설 — 실제 토지조서 (2026-07-06)

실제 파일럿 자료(`sample/01.본리동토지조서.xls`, 대구 달서구 본리동 주상복합)로
업로드→분류→필지 persist→다필지 PNU→지도→오케스트레이션→리포트 전 구간을 한 번에
꿰어 검증했다. 목적은 이번 세션에 만든 조각들이 실데이터 하나로 end-to-end 작동하는지,
그리고 어디서 깨지는지를 정직하게 드러내는 것.

## 흐름 결과

| 단계 | 엔드포인트 | 결과 |
|---|---|---|
| 1. 프로젝트 생성 | `POST /projects/` | OK (한글 body는 `--data-binary @file`로 인코딩 안전) |
| 2. 토지조서 업로드·분석 | `POST /{id}/land-ledger/analyze` | **9개 필지** 파싱 + 프로젝트에 persist (.xls 지원 확인) |
| 3. 주소 컨텍스트 | `PUT /{id}` | land.address = "대구광역시 달서구 본리동 1-3" |
| 4. 다필지 PNU 일괄 해석 | `POST /{id}/land/parcels/resolve` | 고유 9필지, **1 해석 + 8 lot_mismatch 거부** |
| 5. 지도 | `GET /{id}/map?resolve_geometry=true` | 9 features, 1개 실 MultiPolygon 경계 + 공시지가 3,216,000원/㎡ |
| 6. Chief 전체 자동 실행 | `POST /{id}/chief-orchestration/run-all-pods` | **6/6 Pod completed**, 자동 보강(PNU·실거래가) 성공, 0 blocked, Gate HOLD, 9.7s |
| 7. 리포트 생성 | `POST /{id}/reports/generate` | 20 섹션 생성 OK |

## 리허설이 발견해 고친 실제 버그 2건

1. **필지 이중 카운팅** — `land-ledger/analyze`가 같은 필지를 `land_acquisition`과
   `landLedger.parcels` 양쪽에 저장 → `parcel_pnu_resolution._iter_parcel_lists`가
   둘 다 순회해 9필지가 18로 집계되고 같은 필지를 두 번 해석했다. 필지 식별자(dong+정규화
   지번)로 그룹화해 **1회만 시도하고 결과를 동일 필지 전 항목에 반영**하도록 수정
   (`_grouped_parcels`). 회귀 테스트 추가.
2. **리포트 생성 500** — `report_agent._build_land_acquisition`가 파싱 필지의 `share`가
   None일 때 `None*100`으로 크래시(`area_sqm`도 camelCase `areaSqm`만 있어 `:,` 포맷 실패).
   숫자일 때만 포맷하고 owner/area/mortgage 폴백(addressFragment/areaSqm/landCategory)으로
   방어. `_build_use_of_funds`의 동일 패턴도 보강. 회귀 테스트 2건 추가.

## 정직한 한계 (버그 아님, 후속 과제)

- **하위 지번 지오코딩 정밀도** (→ 해소됨, 2026-07-06): VWorld geocode가 "본리동 1-4"를
  인접 필지 "1-1"의 좌표로 폴백 → point-in-parcel 조회가 1-1을 반환. 지번 대조 가드가 이를
  **정확히 거부**(오필지 PNU 부착 차단 = 비조작)하여 9필지 중 1필지만 해석됐다.
  **후속으로 정밀 PNU 구성 방식을 구현**: 동을 1회 지오코딩해 법정동코드(level4LC 앞 10자리)를
  얻고, 각 지번의 본번/부번으로 PNU를 직접 구성한 뒤 VWorld `attrFilter=pnu:=:`로 존재
  확인·jiga 조회한다(point-in-polygon 왕복 제거 → 오필지 부착 원천 차단). 실측 결과 1-3은
  정확히 해석되고 나머지 8필지는 `not_in_current_cadastre` — **2019년 토지조서의 옛 지번이
  재개발로 합병·재번지되어 현 지적도에 없다는 진실**을 드러냈다(구성 PNU를 감사용으로 남김).
  즉 해석률 1/9는 지오코딩 한계가 아니라 이 부지의 실제 지적 상태였다. 현 지적도에 살아있는
  필지를 가진 프로젝트에서는 이 방식이 지번 단위로 정확히 해석한다.
- **근거 링크 0건**: 리허설 프로젝트는 시나리오/스트레스/pf-review 계산을 돌리지 않아
  `deal_state_records`가 0이므로 문단 근거 링크도 0. 계산을 실행한 프로젝트(585: 49건)에서는
  정상 동작함(별도 검증 완료). 자료만으로는 근거 원장이 채워지지 않는다는 정상 동작.

## 확장 리허설 — 사업수지/계산 체인 (같은 날)

토지조서에 이어 사업수지 워크북 경로로 계산 체인이 근거 원장까지 채우는지 검증.

- **LLM 워크북 추출 제약**: 실제 `19.12.11 본리동 주상복합 사업수지 (PF검토용).xlsx`를
  `/feasibility-workbook/extract`에 넣으니 Gemini 키가 **429 쿼터 초과**로 `llm_unavailable`.
  외부 제약(무료 티어 소진)이라 LLM 추출은 지금 실행 불가 — 비조작 원칙대로 아무 수치도
  지어내지 않고 빈 후보로 정직하게 반환.
- **계산 체인은 결정론 데이터로 검증**: 같은 사업의 구조화 데이터
  (`data/examples/daegu_bondong_mixed_use_pf.json`, 본동 주상복합)로 프로젝트를 만들고
  feasibility/calculate → cashflow/calculate → pf/structure → scenarios/recovery →
  stress-test/run 전부 200. 결과:
  - **계산 run 7건** — feasibility, cashflow, pf_metrics, waterfall, unsold_ltv,
    tranche_recovery, discount_stress (레지스트리 확장이 전 엔진 캡처 확인).
  - **deal_state_records 51건** (metric 48 + risk_issue 3), completeness 51/51 locator 보유.
  - **리포트 생성**: 20 섹션, **근거 링크 46건**, locator-ready 15 섹션 — 계산을 돌린
    프로젝트에서는 문단→record 근거 링크가 실제로 채워짐(토지조서만 올린 프로젝트의 0건과 대비).

이로써 이번 세션의 조각(계산 레지스트리·deal_state 필터·근거 링크·자동 보강)이 실데이터로
end-to-end 연결됨을 실증. 남은 것은 LLM 쿼터(운영 키/유료 전환)와 다필지 PNU 정밀도.

## 결론

자료 1개(토지조서)만으로 업로드→9필지 persist→지도 렌더→6 Pod 자동 실행→리포트까지
end-to-end로 통과한다. 리허설이 없었으면 파일럿에서 처음 터졌을 500 버그 2건을 사전에
잡았다. 파일럿 준비도: 데이터 인입·오케스트레이션·리포트 골격은 준비됨; 다필지 PNU 정밀도가
남은 정확도 리스크.

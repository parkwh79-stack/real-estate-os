# Map Intelligence

Updated: 2026-11-06

## 운영 화면 복구 범위 (2026-11-06)

운영 2D 타일 프록시가 제공기관 실패를 68바이트 투명 PNG·200과 10분 캐시로
바꾸어 빈 지도가 정상처럼 보이는 현상을 확인했다. 같은 브라우저용 키의 공식
WMTS 요청은 실제 이미지로 응답했고, 운영 `/vworld-3d`도 실제 위성 지도와 탐색
컨트롤을 렌더링했다. 따라서 OpenAI 키나 Markdown 저장 여부를 지도 인증 원인으로
간주하지 않는다. 프로젝트 화면의 위치·PNU 조건, 3D 비동기 준비 상태와 2D 서버
프록시의 실패 처리·Referer·환경값 공백을 각각 검증한다.

타일 실패 응답은 원문·키·요청 URL을 제외한 고정 사유와 상태로 분류하고 저장하지
않는다. 배경 지도 실패 시 무료 대체 배경과 재시도를 제공하며 필지 선택은 유지한다.
WMS 일부 실패로 정상 배경 전체를 갈아 끼우지 않는다. 좌표는 유한한 WGS84 범위와
유효한 닫힌 경계로 확인하며 잘못된 도형은 확인 수에서 제외하고 재조회 대상으로 남긴다.
3D는 준비 콜백과 실제 캔버스를 기다리고 시간 초과·컨텍스트 손실은 재시도로 연결한다.
이 준비 판정은 엔진 초기화 확인이며 지형·영상 타일 전체 다운로드 완료를 뜻하지 않는다.
선택 PNU는 일치 필지의 위치를 사용하고, 일치하지 않으면 다른 토지의 중심을 사용하지 않는다.
전체 뷰의 사업 위치가 없으면 전국 참고지도임을 명시하며 사업 좌표나 경계를 만들지 않는다.

로컬 검증은 지도·경계·PNU API 48개, 합성 공개 키로 외부 요청을 차단한 PC·모바일
2D/3D 화면 22개, 웹 계약 16개와 타입·전체 린트를 통과했다. 잘못된 거대 정수
좌표·Z값에 의한 500, 잘못된 PNU의 다른 사업지 중심 사용, 초기화 전에 준비 완료를
보내는 경로, WMS 일부 실패가 배경 전체를 지우는 동작을 회귀로 확인한다.
운영 제공기관의 응답과 실제 3D 영상 표시는 합성 검증과 구분해 배포 후 다시 확인한다.
검증용 Next 출력은 정리하고 원본 자료·저장 좌표·DB 스키마·권한은 변경하지 않는다.

Cloud Build `5914f53c-4f8f-48d6-9a26-2ffcff133a6c`의 이미지
`sha256:9a31d4c026037881b056d60d1f53e414fb30e4d1870f3f126a07369a3243733b`를
배포 명세에 고정했다. `realytics-api-00052-six` 별도 점검 주소의 health·readiness
200, 미로그인 확인과 프로젝트 지도 401을 검증했다. 같은 소스의 GitHub 전체 CI와
Vercel 배포를 확인한 뒤 이 리비전에 운영 트래픽을 연결한다.

### 배경 타일의 브라우저 직접 조회

첫 수정 배포의 운영 재확인에서 Vercel 서버의 VWorld 호출은 실제 제공기관 502를
받았다. 같은 키를 사용한 브라우저·로컬 Node의 WMTS는 20,650바이트 PNG·200과
`Access-Control-Allow-Origin: *`로 응답했다. 이 차이의 제공기관 내부 원인은
확정하지 않았으며 서버 위치 제한이라고 단정하지 않는다.
이미 3D에 사용하던 전용 공개 브라우저 키로 고정된 공식 WMTS 주소를 직접
조회하도록 배경만 연결했다. 서버 전용 키를 브라우저로 보내지 않는다.
직접 경로의 공백 제거·URL 인코딩·고정 호스트를 검증했으며 브라우저 실패 시에는
동일한 필지·선택을 유지하고 OSM으로 전환한다. 변경 후 2D PC·모바일 4개를 다시
확인했고 전체 웹 계약 18개·타입·린트가 통과했다.

WMS는 실제 응답에 CORS 허용 헤더가 없어 프록시를 유지한다. 운영 제공기관 502가
남은 지적도·용도지역 타일은 부분 실패 안내로 구분하며, 정상 배경과 저장된 필지
경계 전체를 감추지 않는다. 운영 3D는 실제 브라우저에서 위성 영상과 3D 건물,
로딩 문구 해제를 확인했다. 이는 각 사업장의 미확인 필지나 법적 경계의 확정을 뜻하지 않는다.

Map Intelligence is the project-level spatial view for REALYTICS. It connects
land ledger parcels, PNU, VWorld cadastral boundaries, official land price,
zoning overlays, market evidence, and risk context.

## Current Behavior

- `GET /projects/{id}/map` returns `realytics-map-intelligence-v1`.
- 지도와 토지 검토는 같은 프로젝트 필지를 사용한다. 토지조서에 저장된 필지의 검증된
  PNU로 브이월드 지적도에 조회한다. 대표 PNU를 다른 필지에 사용하지 않는다.
- 필지 상세의 **지도에서 보기**는 같은 업무 트랙을 유지하고 선택한 PNU만 조회하고
  강조한다. 프로젝트에 없는 PNU는 조회하지 않는다. 전체 지도에서는 여러 필지를
  선택할 수 있다.
- 지도에 Polygon/MultiPolygon 경계, 참고 위치(Point), 위치 미확인을 구분한다.
  브이월드가 다른 PNU의 도형을 반환하면 사용하지 않는다. 미확인 위치는 만들지 않는다.
- `resolve_geometry=true`는 저장된 경계가 없는 필지의 경계를 조회한다.
  `focus_pnu`가 있으면 해당 프로젝트의 일치 필지만 조회한다. `geometry_limit`은 최대
  요청 건수이며, 시간 한도를 넘기거나 응답이 없는 필지는 확인되지 않은 상태로 표시한다.
- 운영에 브이월드 설정이 없으면 연결 필요 상태를 표시한다. 토지조서와 이미 확인된
  PNU는 그대로 읽을 수 있다. 지도 GET 요청에서 조회한 경계는 응답에만 사용한다.
  공공 응답 원문이나 경계를 데이터베이스에 새로 저장하지 않는다.
- 이 경로에는 기존 분석 가정이나 구조조정 기록을 읽을 필요가 없다. 운영 Postgres의
  제한된 서비스 권한에서 토지 원장과 토지 증빙만 읽는다. Markdown은 화면의 입력
  데이터가 아니다.

## 112-Parcel Selection UX

The project map page now supports large land-ledger packages:

- Select all parcels.
- Clear all parcels.
- Select only parcels with confirmed boundary geometry.
- Show only selected parcels.
- Fit the map to the selected parcel group. An empty selection stays empty.
- Inspect every parcel in a scrollable selector and a detail table.

This means a 112-parcel land ledger can be reviewed as one package while still
allowing parcel-by-parcel inspection.

## Layer Model

| Layer | Source |
| --- | --- |
| Parcel boundary | VWorld cadastral map / PostGIS `land_parcels` |
| Official land price | Public data / VWorld / manual evidence |
| Zoning | VWorld land-use plan overlay |
| Transactions and market price | MOLIT transaction API / Valuemap |
| Nearby supply | Marketability evidence |
| Risk | Risk Register |

## Data Policy

- PNU, official land price, and parcel boundary must be source-backed.
- Missing geometry is displayed as a data limitation, not fabricated.
- Registry and encumbrance evidence remain manual/licensed evidence unless a
  legally usable connector is added.
- Map output should be treated as spatial evidence support, not as a final legal
  title opinion.

## Next

- Add unresolved-parcel correction UI for parcels without PNU or boundary.
- Persist selected parcel groups as named review scopes.
- Connect selected parcel groups to Risk Register and Evidence Graph.
- Add radius-based market comparable search from selected parcel centroid.

## 운영 3D와 2D 연결 (2026-11-05)

3D WebGL은 배포 도메인에서 허용된 브라우저용 `NEXT_PUBLIC_VWORLD_API_KEY`로 시작한다. 서버 전용 `VWORLD_API_KEY`는 브라우저 HTML로 보내지 않는다. 브라우저 키가 없는 경우 같은 프로젝트·트랙·선택 PNU의 기존 2D 필지 지도를 표시한다. 사용자가 제공한 키를 Vercel 브라우저 환경과 Google Secret Manager의 서버 환경에 분리 연결한다. 공식 지오코더에서 공개 주소의 좌표 응답을 확인했다. 3D SDK 시작에 실패하면 제한 사유와 재시도, 같은 프로젝트의 2D 지도를 제공한다. 2026-11-06부터 전체 뷰의 좌표가 없을 때는 사업 위치 미확인인 전국 참고지도를 표시하고, 선택 PNU의 위치가 없거나 일치하지 않을 때는 2D에서 자료 상태를 확인한다.

VWorld 프록시의 Referer는 실제 요청의 서비스 주소를 사용한다. 운영 코드의 localhost 고정 헤더는 제거했다.

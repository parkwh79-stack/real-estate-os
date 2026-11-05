# Map Intelligence

Updated: 2026-11-04

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

3D WebGL은 배포 도메인에서 허용된 브라우저용 `NEXT_PUBLIC_VWORLD_API_KEY`로 시작한다. 서버 전용 `VWORLD_API_KEY`는 브라우저 HTML로 보내지 않는다. 브라우저 키가 없는 경우 같은 프로젝트·트랙·선택 PNU의 기존 2D 필지 지도를 표시한다. 사용자가 제공한 키를 Vercel 브라우저 환경과 Google Secret Manager의 서버 환경에 분리 연결한다. 공식 지오코더에서 공개 주소의 좌표 응답을 확인했다. 3D SDK 시작에 실패하거나 좌표가 없으면 제한 사유를 표시하고 같은 프로젝트의 2D 지도를 제공한다.

VWorld 프록시의 Referer는 실제 요청의 서비스 주소를 사용한다. 운영 코드의 localhost 고정 헤더는 제거했다.

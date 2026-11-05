# Smart Intake (AI 문서 자동 분류)

Updated: 2026-11-05

## 보관 문서와 파싱 결과 조회

문서 검토는 프로젝트의 보관 원본과 Parser V2 실행·후보값을 읽는다.
운영 PostgreSQL에서 권한이 없는 구형 용어집 후보 조회를 분리한다.
용어집 후보를 확인할 수 없어도 원본과 파싱 결과는 표시한다. 문서 검토 조회 실패는
빈 목록과 구분하며, 문서별로 파싱 실행 상태와 후보 수를 별도로 표시한다.

운영 재현에서 프로젝트 1의 PDF 원본 4건과 완료된 Parser V2 실행 4건을 확인했다.
기본 접수 프로필로 구조를 확인한 이 원본에는 정식 검토 후보가 생성되지 않았다.
필요한 PDF 페이지는 문서 검토에서 글자 읽기로 확인할 수 있다.

## PDF 페이지 글자 읽기 (2026-11-04)

문서 검토의 PDF 원본 보기에서 페이지를 선택하고 ‘글자 읽기’를 누르면 기존
ParserAgent가 보조 판독 결과를 만든다. 원본만 있는 PDF는 첫 페이지부터 열 수 있다.
텍스트가 있는 페이지는 원문 텍스트를 읽고, 스캔이 필요한 페이지는 검증된
한국어·영어 언어 데이터와 PyMuPDF의 로컬 OCR을 사용한다. [OCR 엔진](https://pymupdf.readthedocs.io/en/latest/ocr/index.html)

기존 원본 읽기 권한과 보관 상태를 확인하고, 실제 객체 버전으로 원본을 읽어
SHA-256을 검증한다. 판독 결과는 원본 버전과 페이지 번호, 좌표, 텍스트 해시에
연결한다. 사용자는 원본 이미지 옆에서 판독 텍스트를 대조할 수 있다. OCR 값은
검토 자료이며 숫자 입력과 보고서에 적용할 때는 기존 확인 절차를 따른다.

`POST /projects/{id}/source-documents/{archiveId}/reading`은 선택한 최대 세 페이지를
읽는다. UI는 현재 페이지 하나를 요청한다. `GET .../reading/pages/{pageNumber}`는
현재 원본에 연결된 저장 결과를 조회한다. 300dpi·12MP와 페이지당 20초 제한을
적용하고, 한 인스턴스에서 OCR 하나를 실행한다. 실패와 미처리 페이지는 개별적으로
표시한다. 미리보기 PNG와 읽기 응답은 3 MiB 한도에서 제공한다. PNG의 크기 조정은
화면 표시용이며 원본의 페이지 좌표와 OCR 해상도는 별도로 유지한다.

완료 결과는 원본·엔진·언어 데이터 버전에 연결해 파생 결과로 저장한다. 운영 GCS의
`realytics/derived/pdf-reading/`에는 7일 삭제 규칙이 있다. 프로젝트가 삭제되면
접근 권한 확인에서 차단되며 물리 정리는 파생 객체의 수명과 기존 soft-delete 정책을
따른다. 저장에 실패하면 읽은 텍스트와 저장 상태를 구분해 표시한다.

운영 이미지는 빌드 시 [고정된 언어 데이터](https://github.com/tesseract-ocr/tessdata_fast/tree/87416418657359cb625c412a48b6e1d6d41c29bd)의
해시를 확인하고 실제 OCR을 실행한다. 로컬 설치는 `python -m scripts.prepare_ocr_languages --directory <path>`로
준비한 뒤 `REALYTICS_OCR_TESSDATA_DIR`를 지정한다. 검증은 스캔 이미지에서 한국어·영어·숫자
인식, 권한과 원본 검증, 재열기와 응답 제한을 포함한다. 선택 페이지 읽기가 이번
사용 범위이며 문서 전체의 일괄 판독과 Pod의 자동 활용은 후속 범위다.

## 대용량 전송 복구 (2026-11-04)

Vercel Function의 요청 본문 한도는 약 4.5 MB다. 시간 제한이나 Next.js body-parser
설정을 늘려 해결하지 않는다. `lib/intake-upload-client.ts`가 작은 파일을 합계
3 MiB 이하로 묶고, 큰 파일은 원본을 3 MiB 조각으로 나눠 동일 출처 BFF로 전송한다.
파일당 전송 상한은 64 MiB이며, 모든 64 MiB PDF/압축 워크북의 파싱 성능이나
메모리 안전성을 검증했다는 뜻은 아니다. 기존 파서와 악성 파일 검사 한계도 적용된다.

전송 API는 다음과 같다.

- `POST /projects/{id}/intake/transfers`: 파일명·종류·크기·SHA-256을 받아 임의 전송 ID,
  조각 크기·개수·만료 시각을 반환한다.
- `PUT .../{transferId}/parts/{index}`: 한 조각만 받으며 같은 바이트의 명시적 재전송을
  허용한다. 다른 내용·잘못된 순번·크기는 거절한다.
- `POST .../{transferId}/classify`: 조각을 순서대로 재조합하고 전체 크기·SHA-256을
  확인한 뒤 기존 원본 보관·악성 파일 검사·분류를 사용한다.
- `POST .../{transferId}/process`: 원본을 브라우저에서 다시 보내지 않고 기존 유형별
  파서/검증/JSON 처리 경로를 사용한다. 추출 실패는 완료로 캐시하지 않는다.
- `DELETE .../{transferId}`: 호출자의 전송 임시 파일을 취소한다.

각 요청에서 인증·`project:write`·권위 프로젝트의 테넌트와 사용자 소유 범위를 확인한다.
일반 사용자 토큰을 브라우저에 노출하거나 GCS 직접 쓰기 권한을 부여하지 않는다.
전송 manifest/조각은 증거가 아니며 기존 보관·검사 경로를 통과해야 원본으로 사용한다.
전송 만료는 1시간이다. 처리 성공 뒤 조각을 정리하고 재요청 결과를 보관한다.
동시 작업은 같은 전송의 lease로 제한하며, 강제 종료로 lease가 남으면 만료 후 새
전송으로 재시도한다. 프로젝트 적용과 object receipt는 단일 DB 트랜잭션이 아니므로
임의 장애에 대한 전역 exactly-once를 보장하지 않는다.

현재 전송 저장소는 운영 GCS와 로컬 파일을 지원한다. 기존 S3 put에는 동일한
불변 생성 계약이 없어 이 경로에서 명시적으로 미지원이다. GCS의
`realytics/intake-transfers/quarantine/` 임시 경로에만 age=1 삭제 lifecycle을 설정했다.
자동 삭제는 즉시 실행되는 타이머가 아니며 기존 7일 soft-delete 정책이 적용된다.
정식 원본의 3일 보존과 다른 경로는 이 규칙의 대상이 아니다. 재설정할 때는
`deploy/cloud-storage-intake-lifecycle.json` 규칙을 기존 bucket 규칙과 병합해야 한다.
신규 DB 스키마·IAM·유료 API·저장소 서비스는 추가하지 않았다.

AI 판독에는 원본과 별도로 추출 텍스트와 필요한 페이지 이미지를 함께 사용하는 방향을 권장한다.
축소 이미지를 숫자·수식의 권위 원본으로 사용하지 않는다. 이번 수정은 원본 전송을
복구하며, 모든 문서를 작은 사진으로 바꿔 저장하는 신규 처리기는 포함하지 않는다.

가진 자료를 종류 구분 없이 한 번에 올리면 파서 에이전트가 문서 유형을 자동 분류하고
원본을 보관한다. 기존의 "유형별 슬롯에 파일 하나씩" 방식의 불편을 대체한다.

## 분류기 (deterministic)

`agents/document_classifier_agent.py` `classify_document(filename, content_type,
size, sheet_names, json_keys, sample_text)`:
- 먼저 실제 파일 형식과 사용 가능한 처리 경로를 확인한다. 그 범위 안에서
  파일명, PDF 앞쪽의 읽을 수 있는 본문, 스프레드시트 시트명, JSON 키로 대표 유형을 고른다.
- 투자심사위원회 부의안건 PDF는 **투자심의 메모**를 대표 유형으로 추천한다.
  PDF의 수지·금융·토지·시장·리스크는 본문에서 확인된 주제로 표시한다.
  PDF에 수지표가 있어도 Excel 검증 경로를 선택할 수 없다.
- PDF 첫 3쪽의 텍스트를 최대 4,000자까지 확인한다. 표본 판독은 16MiB 이하에서
  최대 4초간 별도 프로세스로 실행한다. 스캔본, 암호화 문서, 판독 제한에 걸린
  문서는 파일명으로 추천할 수 있지만 확인이 필요하다. 분류 중 OCR·유료 AI 호출이나
  본문 원문 별도 저장은 하지 않는다.
- 출력: `documentType` + 한글 `label` + `confidence`(0–1) + `needsReview` +
  `reasons`(설명가능) + `candidates`(랭킹). Smart Intake는 `detectedFormat`,
  `processingTypes`, `topics`, 문서 검토에 해당하는 `processingMode`도 반환한다.
- 유형: business_plan / basic_architectural_plan / investment_committee_memo / land_ledger /
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
목록에 파일명, 대표 유형, 확인이 필요한 항목을 표시한다. 호환되는 다른 처리 경로가
필요할 때만 유형 변경을 연다. 세부 근거와 분류 점수는 펼쳐 볼 수 있다. 입력패키지 페이지
(`/project/[id]/input-package`) 상단 주력 섹션으로 배치하고, 기존 유형별 업로드는
"개별 업로드(고급)"로 강등. 페이지 전체 국문화. `initialFiles?: File[]` prop을 주면
마운트 시 해당 파일을 자동으로 분류한다(대시보드 진입점에서 전달).

## 대시보드 진입점 (2026-11-04 현재)

로그인 후 `/dashboard`는 최근 프로젝트 재개 중심이다. 새 프로젝트 버튼으로 작업
창을 열어 파일을 선택하면 초안 프로젝트를 생성하고 SmartIntake를 시작한다.
현재 페이지에서 작업 창 닫기/재열기는 파일과 진행 상태를 유지한다. 전송·분류
실패는 파일을 다시 선택하지 않고 명시적으로 재시도하며, 성공한 조각은 재전송하지
않는다. 다른 페이지 이동·새로고침을 넘는 영구 임시저장은 보장하지 않는다.

전체 대시보드 범위와 최신 검증은 [제품 실행 계획](realytics-os-next-work-roadmap.md)을 따른다.

## 분류 후 처리

- 확실한 분류는 사용자가 다시 유형을 고르지 않고 한 번에 처리한다. 모호한 자료는
  해당 행에서만 확인하거나 원본만 남길 수 있다. 먼저 끝난 자료는 재처리하지 않는다.
- PDF·DOCX의 문서 검토는 저장된 원본 ID로 `/parsing-v2/runs`를 실행한다.
  원본을 다시 전송하지 않으며 문서 구조와 원본 위치를 검토에 연결한다. 승인·계산 입력에는
  자동 반영하지 않는다. 지원하는 XLS/XLSX와 JSON 증빙은 기존 전용 파서를 사용한다.
- **문서 검토 열기**는 같은 프로젝트에서 권한이 확인된 해당 원본을 바로 연다.
  읽을 수 있는 텍스트가 없는 PDF는 원본 보기의 기존 **글자 읽기**로 필요한 쪽을 확인한다.
- 형식이 맞지 않으면 파일별로 이유를 표시한다. PDF를 Excel 검증기로 보내는 요청은
  작은 파일과 분할 전송 모두 서버에서 차단한다.

## Next

- 처리 결과(파싱/검증 산출물)를 표에 인라인 요약.
- 유형 수정(override) 결과를 분류기 학습 신호로 (human-gated optimization-log).
- LLM 보조 분류(모호 케이스, ai_router 게이트).

### 운영 용어 후보 조회 권한 검토

운영 표준 용어 조회는 기존 정규화 `glossary_terms` 읽기를 사용한다. GET에서 자동 시드나 구형 앱 상태 저장을 실행하지 않는다. 용어 후보는 테넌트 범위 `glossary_term_candidates`를 읽는다. 현재 런타임에 해당 테이블 SELECT 권한이 없어 후보 조회는 연결 불가 상태로 안내한다. 문서 원본과 파싱값 조회는 계속 제공한다.

검토할 최소 권한 변경은 다음과 같다. 실제 적용은 사용자 검토 후 진행한다. 기존 RLS가 활성화되어 있고 `realytics.tenant_id`와 후보 tenant_id가 일치하는 정책을 확인했다. 용어 편집은 별도의 거버넌스 쓰기 권한 검토가 필요하다.

```sql
GRANT SELECT ON realytics.glossary_term_candidates TO realytics_runtime;
```

### 문서 검토 화면 정리 (2026-11-05)

문서는 상단 카드에서 선택하고 원본과 검토 내용을 두 영역에서 읽는다. 첫 행동은 원본 확인이며 기본 탭은 파싱값과 검토할 항목이다. 출처·충돌·용어·검토 결정·승인본 변경·이력은 검토 더보기에서 연다. 서버 해시와 조회 규격은 접힌 진단 영역에 둔다. 상단 장식에 기존 유리·패럴랙스 표현을 재사용하며 본문은 읽기 쉬운 고정 표면으로 표시한다. 동작 줄이기 설정과 모바일에서는 배경 이동을 제한한다.

선택 문서에 파싱값이 없으면 다른 문서의 후보를 표시하지 않는다. 보관만 된 PDF도 페이지 보기와 글자 읽기를 계속 사용할 수 있다. 원본·후보·검토 결정·승인 스냅샷의 상태와 권한은 각각 확인한다.

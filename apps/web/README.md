# REALYTICS Web

실무자의 자료 검토, 사업수지·Cash Flow, PF·리스크, 검토 보고서를 제공하는 Next.js 앱이다.

실행 환경과 설치는 [루트 README](../../README.md), 제품 범위는 [실행 계획](../../docs/product/realytics-os-next-work-roadmap.md)을 따른다.

저장소 루트에서:

```powershell
npm run dev --workspace web
npm run check-types --workspace web
npm run lint --workspace web
npm run build --workspace web
```

기본 포트는 3000이다. FastAPI 연결은 서버의 `REALYTICS_API_ORIGIN` 설정과 같은 출처 BFF를 사용한다. 비밀값을 브라우저 환경변수에 넣지 않는다. 실제 사용자 인증은 [인증 계약](../../docs/product/identity-tenant-access-contract.md)을 따른다.

주요 작업은 `/project/[id]/input-package`, `/documents/review`, `/feasibility`, `/cashflow`, `/pf-structure`, `/report`에서 이어진다. 뒤의 경로도 동일 프로젝트 경로 아래에 있다. `/api/export-cf`는 샘플 템플릿이며 실제 프로젝트 Cash Flow 내보내기는 `/api/projects/[id]/cashflow/export`다.

수정 전 [Next.js 작업 규칙](AGENTS.md)을 읽는다. 미사용 예제와 중복 컴포넌트를 보관하기보다 실제 사용자 흐름에서 사용하는 코드를 유지한다.

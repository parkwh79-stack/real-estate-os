# Social Login Setup — Kakao & Google (실제 계정 연동)

Updated: 2026-06-29

로그인 페이지의 "실제 카카오 계정으로 연동하기" / "실제 구글 계정으로 연동하기" 버튼이
**실제 계정으로 동작**하려면 각 개발자 콘솔에서 OAuth 앱을 등록하고 Client ID/Secret을
백엔드 `.env`에 넣어야 합니다. 자격증명이 없으면 데모용 **파일럿 mock**(가짜 계정 생성)으로
폴백하며, 이는 실제 인증이 아님을 메타데이터(`loginMode: pilot_mock`)와 표시명("(파일럿)")으로
명확히 구분합니다.

## 코드 구성 (이미 구현됨)

| 단계 | 카카오 | 구글 |
|---|---|---|
| Authorize URL | `POST /integrations/kakao/login/authorize-url` | `POST /integrations/google/login/authorize-url` |
| 실제 콜백 | `GET /auth/kakao/callback` | `GET /integrations/google/callback` |
| 토큰 교환·유저정보 | `integrations/kakao.py` | `integrations/google.py` |
| 상태 조회 | `GET /integrations/kakao/status` | `GET /integrations/google/status` |

콜백은 인증 후 공통 프론트 수신 페이지 `web /auth/kakao/session?session_id=...`로 303
리다이렉트하고, 프론트가 세션을 localStorage(`realytics.auth.session.v1`)에 저장합니다.
authorize-url 응답의 `mode` 필드로 현재 모드를 알 수 있습니다 (`oauth` = 실제, `pilot_mock` = 데모).

## 1) 카카오 로그인 등록

1. <https://developers.kakao.com> → 내 애플리케이션 → 애플리케이션 추가.
2. **카카오 로그인** 활성화 ON, **OpenID Connect** 활성화 ON.
3. **Redirect URI** 등록 (백엔드 `KAKAO_REDIRECT_URI`와 정확히 일치):
   - 로컬: `http://127.0.0.1:8000/auth/kakao/callback`
   - 운영: `https://<api-domain>/auth/kakao/callback`
4. **동의항목**: `openid`, `profile_nickname`, `account_email` 사용 설정(이메일은 검수 필요할 수 있음).
5. 키 확인: **REST API 키** = `KAKAO_REST_API_KEY`. 보안 → **Client Secret** 발급 시 `KAKAO_CLIENT_SECRET`.

```
KAKAO_REST_API_KEY=<REST API 키>
KAKAO_CLIENT_SECRET=<Client Secret (사용 설정 시)>
KAKAO_REDIRECT_URI=http://127.0.0.1:8000/auth/kakao/callback
```

## 2) 구글 로그인 등록

1. <https://console.cloud.google.com> → 프로젝트 생성/선택.
2. **APIs & Services → OAuth consent screen** 구성(External, 앱 이름, 지원 이메일, 테스트 사용자 추가).
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID**, 유형 **Web application**.
4. **Authorized redirect URIs**에 등록 (백엔드 `GOOGLE_REDIRECT_URI`와 일치):
   - 로컬: `http://127.0.0.1:8000/integrations/google/callback`
   - 운영: `https://<api-domain>/integrations/google/callback`
5. 발급된 Client ID/Secret을 넣습니다.

```
GOOGLE_CLIENT_ID=<...>.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<client secret>
GOOGLE_REDIRECT_URI=http://127.0.0.1:8000/integrations/google/callback
```

## 3) 베이스 URL

콜백이 프론트로 되돌아올 주소입니다. 프론트 접속 origin과 동일하게 맞추세요
(예: `localhost`로 접속하면 `localhost`, `127.0.0.1`로 접속하면 `127.0.0.1` — localStorage 세션이
origin별로 분리되므로 섞이면 로그인 직후 세션이 보이지 않을 수 있음).

```
REALYTICS_API_BASE_URL=http://127.0.0.1:8000
REALYTICS_WEB_BASE_URL=http://localhost:3000
```

## 4) 적용 및 검증

1. `services/api-server/.env`에 위 값 입력 후 **API 서버 재시작**(env는 기동 시 로드).
2. `GET /integrations/kakao/status`, `GET /integrations/google/status`에서 `clientIdConfigured`/
   `restApiKeyConfigured`가 `true`인지 확인.
3. authorize-url 응답의 `"mode": "oauth"` 확인(= 실제 모드).
4. 로그인 페이지에서 버튼 클릭 → 실제 카카오/구글 동의 화면 → 동의 → 대시보드로 복귀.

## 보안 메모

- `.env`는 gitignore 대상. Client Secret은 절대 커밋·프론트 노출 금지(상태 엔드포인트는 키를 echo하지 않음).
- 카카오 콜백은 HMAC 서명 `state`로 CSRF를 방어합니다. 구글 콜백의 `state`는 현재 redirect 경로만
  운반(safe-relative 검증)하므로, 운영 강화 시 서명 state/PKCE 적용을 권장합니다(후속 작업).
- 외부 IdP(엔터프라이즈) 우선 정책은 `REALYTICS_IDP_MODE`로 별도 관리(설정 페이지 참조).

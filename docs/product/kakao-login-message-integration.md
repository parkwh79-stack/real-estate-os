# Kakao Login and KakaoTalk Message Integration

Updated: 2026-06-15

This document records how REALYTICS should use Kakao Login and KakaoTalk messaging without weakening PF review security.

## Source Study

Official Kakao Developers references:

- Kakao Login REST API and OIDC discovery: <https://developers.kakao.com/docs/en/kakaologin/rest-api>
- Kakao Login concepts and OIDC: <https://developers.kakao.com/docs/en/kakaologin/common>
- Kakao Login prerequisites and redirect URI: <https://developers.kakao.com/docs/en/kakaologin/prerequisite>
- KakaoTalk Message API concepts: <https://developers.kakao.com/docs/en/kakaotalk-message/common>
- KakaoTalk Message REST API reference: <https://developers.kakao.com/docs/latest/en/rest-api/reference>

## Recommended Product Position

Kakao should be used as:

1. **External collaborator login option**
   - Useful for developers, 시행사, 분양대행사, external advisors, and pilot reviewers.
   - Enterprise users should still prefer Azure AD, Okta, Google Workspace, or another company IdP.

2. **Notification channel**
   - Good for "review required" or "report ready" alerts.
   - Not a place to send PF amounts, registry details, owner names, account numbers, PNU, or legal opinions.

Kakao should not become the main PF review workspace. All sensitive review details stay inside REALYTICS after login.

## Kakao Login / OIDC

Kakao Login can issue an ID token when OpenID Connect is enabled in the Kakao app settings. Kakao provides a standard OIDC discovery endpoint:

```text
https://kauth.kakao.com/.well-known/openid-configuration
```

REALYTICS target configuration:

```text
REALYTICS_IDP_MODE=oidc_jwks
REALYTICS_IDP_PROVIDER=kakao
REALYTICS_IDP_ISSUER=https://kauth.kakao.com
REALYTICS_IDP_AUDIENCE=<Kakao REST API key or configured audience>
KAKAO_REST_API_KEY=<secret-manager-value>
KAKAO_CLIENT_SECRET=<secret-manager-value, if used>
KAKAO_REDIRECT_URI=https://<realytics-domain>/auth/kakao/callback
```

Current implementation baseline:

- `GET /integrations/kakao/status`
- `POST /integrations/kakao/login/authorize-url`
- Existing Bearer-token path can accept Kakao ID token later when OIDC/JWKS verification is enabled.
- Settings UI panel for Kakao readiness and notification policy.
- Mobile Kakao action center: `/project/{project_id}/mobile?source=kakao&event={event_type}`.

Production hardening still required:

- OIDC discovery fetch
- JWKS / RS256 signature validation
- Authorization code callback
- Token exchange
- Nonce/state persistence and replay protection
- Kakao unlink webhook handling

## KakaoTalk Message API

KakaoTalk Message API has important service constraints:

- It supports messages between Kakao Login users in the same service.
- Friend messages require consent and additional Kakao permission.
- Apps without permission and test apps can generally send only to app team members.
- Direct business notifications are usually better handled through Kakao BizMessage / AlimTalk providers.

REALYTICS use cases:

| Event | Kakao message content |
|---|---|
| `risk_review_required` | "리스크 검토 필요. REALYTICS에서 확인하세요." |
| `handoff_review_required` | "에이전트 인계사항 검토 필요." |
| `legal_evidence_required` | "법무/인허가 증빙 보완 필요." |
| `report_export_ready` | "심사보고서 생성 완료." |
| `approval_readiness_changed` | "승인 가능성 상태가 변경됨." |

Prohibited in message body:

- PF amount
- Loan amount / tranche amount
- Owner name
- Bank account
- Registry / encumbrance details
- PNU
- Resident registration number
- Phone number
- Legal opinion detail
- Risk memo detail

The message body should contain only a short notification and a link back to REALYTICS. Mobile UX policy is maintained in `docs/product/mobile-kakao-review-ux.md`.

Current implementation baseline:

- `GET /integrations/kakao/status`
- `POST /integrations/kakao/messages/preview`
- Preview only by default; no live send without explicit `KAKAO_MESSAGE_MODE=send` and provider approval.
- Settings UI exposes whether Kakao REST API key, redirect URI, and message token are configured without exposing secret values.
- If `REALYTICS_WEB_BASE_URL` is configured and a `projectId` is supplied, the message preview generates a mobile-first REALYTICS action URL.

## Adapter Policy

The Kakao adapter must follow these rules:

1. Never store Kakao REST API key, client secret, or message access token in the database.
2. Never return secrets through API responses.
3. Never send sensitive PF data in KakaoTalk message body.
4. Always include a REALYTICS action URL instead of detailed content.
5. Treat KakaoTalk Message API and AlimTalk/BizMessage as separate adapters.
6. Use official Kakao APIs or approved BizMessage providers only.
7. Record notification preview/send attempts in the audit log before production enablement.

## Next Engineering Steps

1. Add OIDC/JWKS verification mode to `core/idp.py`.
2. Add `/auth/kakao/callback` with state/nonce validation and token exchange.
3. Add notification repository records for message previews/send attempts.
4. Add BizMessage/AlimTalk provider abstraction for operational alerts.
5. Add Kakao consent/permission review workflow before enabling live message sends.

# Mobile Kakao Review UX

Updated: 2026-06-15

This document defines the mobile review experience for KakaoTalk-triggered REALYTICS workflows.

## Goal

KakaoTalk should move a reviewer from a short notification into the correct REALYTICS action without exposing sensitive PF data in the message body.

The mobile experience must answer three questions quickly:

1. Is the project approval-ready?
2. What is blocking approval?
3. What should I do next?

## Primary Mobile Entry

Kakao notification links should land on:

```text
/project/{project_id}/mobile?source=kakao&event={event_type}
```

The current implementation provides:

- `apps/web/app/project/[id]/mobile/page.tsx`
- `apps/web/app/auth/kakao/session/page.tsx`
- `recommendedMobilePath` in `POST /integrations/kakao/messages/preview`
- Default Kakao action URL generation when `REALYTICS_WEB_BASE_URL` is configured
- Kakao OAuth callback session handoff through `/auth/kakao/session?session_id=...&redirect=...`

## Mobile Screen Structure

The mobile action center is intentionally simpler than the desktop orchestration page.

1. Project header
   - Project name
   - Address
   - Latest run time

2. Approval readiness summary
   - 승인 검토 가능
   - 보완 후 재심 필요
   - 미실행

3. Mobile KPIs
   - 차단항목
   - 리스크 미검토
   - 인계 미검토
   - 여신심사 의견

4. Action cards
   - 사업자료 패키지 등록
   - 토지권원·증빙 보완
   - 금융구조 보강안 검토
   - 리스크 검토상태 확정
   - 에이전트 인계사항 검토
   - 승인 검토 가능

5. Key blockers
   - Top 3 approval blockers only
   - Severity converted to Korean review terminology

6. Quick links
   - 사업자료·증빙 입력
   - AI 통합심사
   - 근거 연결도

## Kakao Message Policy

KakaoTalk message body may include only:

- Project display name
- Event type summary
- Generic review-needed message
- REALYTICS web action link

KakaoTalk message body must not include:

- PF amount
- Loan or tranche amount
- Owner name
- Bank account
- Resident registration number
- Phone number
- Email address
- PNU
- Registry or encumbrance details
- Legal opinion detail
- Risk memo detail

## Why Mobile Action Center Exists

The desktop orchestration screen is designed for full review work. It contains risk register actions, evidence graph details, handoff review, restructuring cures, data quality overrides, similar case search, and report generation.

That density is useful on desktop but too heavy after a KakaoTalk notification.

The mobile page is therefore a decision router:

```text
KakaoTalk notification
    -> Kakao OIDC callback
    -> REALYTICS session connector
    -> Mobile Action Center
    -> One next action
    -> Desktop/deep workflow only when needed
```

## Acceptance Criteria

- A Kakao message preview can produce a mobile action path from `projectId`.
- A Kakao callback can create a REALYTICS session and redirect to the mobile session connector.
- The mobile session connector stores the verified REALYTICS session through `/auth/me`.
- The mobile page can load without requiring the desktop orchestration UI.
- Sensitive PF data is not embedded in Kakao message body.
- Kakao message preview is recorded as a user activity audit event.
- The first viewport shows readiness status and next action.
- The page uses Korean financial review terminology instead of internal machine labels.
- The page provides clear escalation into input package, orchestration, and evidence graph workflows.

## Next Steps

1. Add JWKS/RS256 verification before treating Kakao ID tokens as production-grade.
2. Add mobile review actions for simple `approve`, `needs_revision`, and `acknowledge` decisions where policy allows.
3. Add a QR/deep-link preview in the settings page for pilot testers.
4. Test on real iOS/Android KakaoTalk in-app browser after deployment.
5. Add live-send notification repository records and delivery status when Kakao permissions are approved.

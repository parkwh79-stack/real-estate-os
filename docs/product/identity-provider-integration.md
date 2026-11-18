# Identity Provider Integration — Legacy Compatibility Reference

Updated: 2026-07-22

> **LEGACY / DO NOT USE AS THE PRODUCTION SETUP GUIDE**
>
> This document preserves the earlier HS256/local-session migration baseline.
> The production contract is now
> `docs/product/identity-tenant-access-contract.md` with the component boundary
> in `docs/architecture/architecture-boundary.md`. Production uses Supabase Auth
> Google/Kakao, Next.js HttpOnly cookie/BFF handling, FastAPI JWKS verification,
> and live PostgreSQL `principals`/`tenant_memberships`. JWT tenant/role claims,
> raw identity headers, unsigned tokens, and local sessions are not production
> authorization sources.

The following sections describe the compatibility path that preceded the
current Supabase Auth boundary. They remain only for local migration history and
tests that explicitly opt into legacy behavior.

## Current Capability

Historical snapshot as of 2026-06-12; do not use this table as current
production readiness.

| Area | Status |
|---|---|
| Local pilot tenant/user/session | Implemented |
| Role-based permissions | Implemented |
| Strict RBAC mode | Implemented |
| Bearer token identity context | Implemented |
| HS256 JWT verification | Implemented |
| Tenant mismatch blocking | Implemented |
| IdP status API | Implemented |
| Secret-safe status response | Implemented |
| OIDC JWKS / RS256 verification | Planned |
| Full redirect login flow | Planned |
| Production secret manager integration | Planned |

## Environment Variables

```text
REALYTICS_IDP_MODE=off | hs256 | dev
REALYTICS_IDP_PROVIDER=okta | azure-ad | auth0 | internal-proxy
REALYTICS_IDP_ISSUER=https://issuer.example.com
REALYTICS_IDP_AUDIENCE=realytics-api
REALYTICS_IDP_JWT_SECRET=<secret-manager-value>
REALYTICS_IDP_JWKS_URL=https://issuer.example.com/.well-known/jwks.json
REALYTICS_IDP_ALLOW_UNSIGNED_DEV_TOKENS=false
REALYTICS_RBAC_MODE=strict
REALYTICS_TENANT_ISOLATION_MODE=strict
```

`REALYTICS_IDP_JWT_SECRET` must be supplied by an environment secret manager in operation. It is never returned by `/auth/idp/status` or stored in browser local storage.

## Supported JWT Claims

| Claim | Meaning |
|---|---|
| `realytics_user_id`, `preferred_username`, `email`, `sub` | User ID fallback order |
| `realytics_tenant_id`, `tenant_id`, `tid` | Tenant ID |
| `realytics_role`, `role`, first item in `roles`/`groups` | REALYTICS role |
| `email` | User email |
| `name`, `display_name` | Display name |
| `iss` | Issuer |
| `aud` | Audience |
| `exp` | Expiration |
| `nbf` | Not-before |
| `jti` | Token ID for audit metadata |

## API Behavior

`Authorization: Bearer <jwt>` is evaluated before local session or user headers. When the token is valid:

- The user is upserted into server identity storage.
- The tenant is upserted if missing.
- Role permissions are resolved from the server role map.
- `authSource` is returned as `bearer_token`.
- Tenant mismatch between token and `X-REALYTICS-TENANT-ID` / `tenant_id` is rejected.

Status endpoint:

```text
GET /auth/idp/status
```

Identity endpoint:

```text
GET /auth/me
Authorization: Bearer <jwt>
```

## Non-Goals For This Step

- Do not store IdP access tokens in browser local storage.
- Do not store API keys or IdP secrets in the database.
- Do not enable unsigned dev tokens in production.
- Do not treat HS256 pilot/proxy verification as final OIDC federation.

## Next Hardening

1. Add OIDC discovery and JWKS/RS256 signature validation.
2. Add IdP group-to-role mapping configuration.
3. Add tenant-scoped project ownership checks across all project APIs.
4. Move secrets to the chosen secret manager.
5. Add frontend redirect login through the selected IdP.

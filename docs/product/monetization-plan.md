# Monetization Plan

Updated: 2026-07-22
Status: Pilot commercial direction; exact pricing requires business approval

The authoritative system and data contract is
`docs/product/billing-entitlement-contract.md`.

## Current decision

REALYTICS begins with an institutional B2B pilot rather than public self-service
billing.

| Phase | Customer | Commercial model | Runtime enforcement | Status |
|---|---|---|---|---|
| Institutional pilot | Developer, lender, securities, asset manager, professional reviewer | Fixed-term/annual tenant contract, tax invoice and bank transfer | Reviewed manual contract and entitlement activation | Current target |
| Enterprise rollout | Large institutions and multi-workspace organizations | Platform fee plus included users/projects/processing, security/support options | Versioned contract and PostgreSQL entitlements | Planned |
| Self-service expansion | SME developers and professional firms | Monthly/annual subscription with controlled limits/overage | Payment-provider adapter plus entitlement projection | Deferred |

## Recommended billable dimensions

- tenant/workspace platform access;
- active users and active projects;
- document pages or processing jobs;
- report/export packs;
- legal, market, or external-data packs;
- API/integration access;
- retention, private-network, SSO, and support profile.

Pure seat-based pricing is not recommended because document processing,
external data, storage, and worker costs do not scale only with seats. Pure
usage pricing is also not recommended for institutional customers because it
makes budgets unpredictable. The target is a platform fee with included
capacity and controlled overage or contract amendment.

## Pilot rules

- No payment gateway is required for the current pilot.
- Authentication does not establish paid access.
- Billing account, tenant, user, contract, and entitlement remain separate.
- Manual activation requires contract evidence, effective dates,
  maker-checker review, and an audit event.
- Payment/renewal failure never deletes tenant data automatically.
- Exact prices, tax/refund/cancellation terms, included quantities, and SLA are
  `NEEDS-DOMAIN-DECISION` until approved.

## Deferred capabilities

- Public checkout and stored payment methods.
- Automatic recurring card payment.
- Self-service upgrade/downgrade and proration.
- Automated refund/credit workflow.
- Marketplace settlement or revenue sharing.
- JWT-based billing authority.

See `docs/product/billing-entitlement-contract.md` for the provider-neutral
entities, state machines, idempotency, usage ledger, failure policy, and
acceptance gates.

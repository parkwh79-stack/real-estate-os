# Billing and Entitlement Contract

Updated: 2026-07-22
Status: Approved target contract; payment processing is deferred
Initial channel: institutional pilot with contract, tax invoice, and manual activation

## 1. Decision

REALYTICS will not add a payment gateway during the current institutional
pilot. It will, however, separate commercial entitlement from authentication
and define a provider-neutral billing model now.

Initial commercial model:

- tenant/workspace-level annual or fixed-term B2B contract;
- included users, active projects, document processing, and selected outputs;
- controlled overage or contract amendment rather than unexpected charges;
- tax invoice/bank transfer and reviewed manual activation;
- future monthly/annual self-service payment through a provider adapter.

## 2. Product being sold

Primary paid customer:

> A real-estate development, PF, credit, or investment organization that must
> reproduce feasibility, debt structure, risk, approval conditions, and review
> reports from approved source evidence.

The paid outcome is controlled decision throughput and auditability, not “AI
tokens” or access to a login page.

## 3. Billable account boundary

- A `billing_account` represents the contracting customer.
- A billing account may fund one or more tenants/workspaces if the contract says
  so.
- A user may belong to several tenants but is not the billing account.
- Authentication success does not grant a paid entitlement.
- Tenant administration does not automatically grant billing-owner authority.
- Billing contacts, legal contracting entity, tax information, and service
  users are separate records.

## 4. Recommended pricing structure

The preferred model is hybrid rather than purely seat-based or purely usage-based.

| Layer | Recommended basis |
|---|---|
| Platform access | Tenant/workspace fixed fee |
| Included capacity | Named/active users, active projects, storage, document pages/jobs |
| Premium capability | Advanced legal corpus, external data, specialized report packs, API/integration |
| Overage | Controlled document processing, report generation, external data or compute usage |
| Enterprise | Security, SSO, private deployment/network, retention, support and custom integration |

Exact price, included quantity, tax treatment, refund rule, and contractual SLA
are `NEEDS-DOMAIN-DECISION` items and must not be hard-coded before commercial
approval.

## 5. Authoritative entities

The target PostgreSQL model separates the following records.

### 5.1 `billing_accounts`

Key fields:

- `billing_account_id`
- legal customer name and business identifier reference
- billing country/currency/tax profile reference
- billing owner/contact IDs
- `status`
- created/updated timestamps and audit lineage

Sensitive tax documents and contracts reside in protected object storage; the
table stores controlled metadata and object references.

### 5.2 `billing_account_tenants`

Maps which tenants receive service under which billing account and period.
Mapping changes are versioned and audited.

### 5.3 `plan_versions`

Immutable commercial plan definition:

- plan code and version;
- effective window;
- currency and billing interval;
- included entitlements and usage allowances;
- overage policy;
- retention/support profile;
- hash of the reviewed commercial definition.

Published plan versions are immutable. Changes create a new version.

### 5.4 `subscription_contracts`

Provider-neutral service contract:

- billing account and plan version;
- contract number and external provider reference if any;
- start, renewal, end, cancellation, grace, and suspension dates;
- commercial state;
- signed contract/approval evidence reference;
- supersession lineage.

### 5.5 `entitlements`

Materialized, time-bounded rights such as:

- active users/projects limit;
- document page/job allowance;
- report/export types;
- legal/market data packs;
- API access;
- retention and support class;
- enterprise security capabilities.

Entitlements identify source contract/version, effective window, and reason.
They are evaluated through FastAPI from PostgreSQL, never from browser state or
JWT claims.

### 5.6 `usage_ledger`

Append-only usage facts:

- event ID and idempotency key;
- billing account, tenant, project where applicable;
- usage metric and quantity;
- occurred/recorded time;
- source run/event/object reference;
- reversal/supersession reference;
- rating status and plan version.

Corrections append a compensating entry; they do not rewrite history.

### 5.7 `billing_provider_events`

Stores the minimal, privacy-safe webhook/event envelope required for replay:

- provider and external event ID;
- event type and provider object ID;
- received time and payload hash;
- verification result;
- processing status/attempts/error category;
- resulting internal transition IDs.

The unique provider event ID and transition idempotency key prevent duplicate
webhooks from duplicating payments or entitlements.

### 5.8 Invoice/payment projections

Invoices, payments, refunds, credits, and settlements are separate projections.
A payment success does not directly mutate entitlement without the associated
contract and transition policy.

## 6. State models

### 6.1 Contract

```text
draft -> pending_approval -> active -> expiring -> expired
                              |  \\-> cancelled
                              \\----> suspended -> active
```

### 6.2 Invoice

```text
draft -> issued -> paid
             | -> partially_paid
             | -> overdue
             | -> void
```

### 6.3 Payment

```text
created -> pending -> succeeded
                   -> failed
                   -> cancelled
succeeded -> partially_refunded -> refunded
```

### 6.4 Entitlement

```text
scheduled -> active -> grace -> restricted -> expired
                       \\-----> active
```

These states are independent. For example, a payment may be `succeeded` while
contract activation is blocked by amount/currency/account mismatch.

## 7. Initial manual B2B flow

```text
Commercial approval
  -> reviewed contract record
  -> plan version selection
  -> tenant mapping
  -> maker-checker entitlement activation
  -> service start
  -> invoice/payment reconciliation
  -> renewal, amendment, suspension, or expiry
```

Manual does not mean unaudited. Activation requires:

- a unique contract and approval reference;
- effective dates;
- approved entitlement set;
- maker and checker separation;
- actor, timestamp, reason, and evidence;
- no direct edits to already effective history.

## 8. Future payment-provider adapter

When self-service billing is approved, the provider adapter owns transport and
signature verification only. FastAPI owns internal transitions.

Required sequence:

1. Create an internal checkout/subscription intent with idempotency key.
2. Send the provider a minimal provider-specific request.
3. Treat browser redirect/success UI as provisional.
4. Verify the signed server webhook.
5. Re-fetch/verify amount, currency, customer, and provider object where needed.
6. Record the provider event once.
7. Apply the internal payment/contract transition atomically.
8. Recalculate entitlement projection.
9. Emit billing and access audit events.

The adapter can be replaced without changing tenant, usage, contract, or
entitlement authority.

## 9. Entitlement enforcement

- FastAPI evaluates entitlement at the use-case boundary.
- RLS isolates tenant data but does not replace commercial checks.
- Read, create, execute, export, external-data, and administrative capabilities
  may have different entitlement decisions.
- A quota check and usage reservation occur atomically for costly operations.
- Worker completion settles actual usage; failed/retried work follows the metric
  policy without double charging.
- UI may explain availability but is not the enforcement point.
- Cached entitlement decisions have a short explicit TTL and are invalidated on
  contract/status change.

## 10. Failure and downgrade policy

- Payment or renewal failure never deletes tenant data automatically.
- During grace, existing data remains readable according to contract while new
  costly runs may be limited.
- Restriction, export availability, and deletion/retention clocks are separate
  decisions.
- Downgrade cannot silently remove evidence, audit history, approved reports, or
  legal-hold objects.
- Provider outage queues reconciliation; it does not fabricate success.
- Amount, currency, customer, plan, or contract mismatch blocks activation and
  creates an operations case.
- Refund failure, webhook duplication, network disconnect, and out-of-order
  events are explicit test cases.

## 11. Security and audit

- Provider secrets live in the production secret manager.
- Raw card/payment credentials never pass through REALYTICS servers unless a
  future audited architecture explicitly requires it.
- Webhook signatures and replay windows are verified.
- Billing APIs require dedicated permissions and step-up for material changes.
- Provider payload bodies are minimized/redacted; hashes and selected fields are
  retained for reconciliation.
- Billing support staff do not receive unrestricted deal-document access.
- Every entitlement grant/revoke identifies its contract, event, actor, and
  reason.

## 12. Non-goals for the current pilot

- Public checkout UI.
- Stored payment methods.
- Automatic recurring card charge.
- Proration and self-service plan change.
- Automated partial refunds.
- Marketplace settlement or revenue sharing.
- JWT-embedded billing authority.
- Free/Pro/Enterprise feature flags without a versioned contract.

## 13. Acceptance gates before payment implementation

1. Legal customer, billable unit, price, tax invoice, cancellation, refund, and
   SLA policies are approved.
2. Billing and user/tenant identities are explicitly mapped.
3. Contract, entitlement, usage, invoice, payment, refund, and provider-event
   state transitions are reviewed.
4. Idempotency, duplicate/out-of-order webhook, timeout, reconciliation, and
   recovery tests are specified.
5. Data retention and support responsibilities are approved.
6. Provider contract, production credentials, webhook origin, secret rotation,
   settlement, and sandbox/live test plan are ready.
7. Operations UI distinguishes provider state from internal authoritative
   state.

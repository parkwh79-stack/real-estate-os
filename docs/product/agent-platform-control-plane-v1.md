# Agent Platform Control Plane v1

Updated: 2026-07-14  
Project: REALYTICS x PLATFORM9  
Status: Controlled Implementation  
Implementation level: Level 2  

## 1. Decision

REALYTICS will adopt the modular Agent Platform specification as a control plane
over the existing real-estate domain system. It will not replace the Shared Deal
State Graph, deterministic calculation engines, Pod DAG, CalculationRun registry,
Evidence Graph, Risk Register, or existing repositories.

```text
Existing domain and data plane
  Shared Deal State + deterministic engines + Pod artifacts + evidence/risk records
                              |
                              v
New Agent Platform control plane
  Agent contracts + registries + Brain routing + runtime policy + learning gates
```

This avoids a second orchestration stack and permits gradual migration of the
existing Agents through `LegacyAgentAdapter`.

## 2. Non-negotiable invariants

1. LLMs may classify, interpret, review, challenge, and draft narrative output.
2. LLMs may not authoritatively calculate feasibility, Cash Flow, Max Minus,
   LTV/LTC/DSCR, Exit sales rate, Waterfall, or Tranche recovery.
3. Missing user evidence triggers authorized public/internal auto-supplement first.
   The system blocks only after attempted sources and remaining gaps are recorded.
4. Every report-grade claim must use a typed source locator: document page, Excel
   cell/range, API row, Evidence ID, or Agent artifact path.
5. Raw API keys never enter AgentRequest, AgentExecutionContext serialization,
   AgentResult, logs, artifacts, or business tables. Runtime access is ephemeral;
   production profiles store only Secret Manager references.
6. Learning creates a candidate. It never changes calculation, approval, evidence,
   RBAC, routing, or production prompt policy without evaluation and human promotion.
7. Coding Pod execution is isolated from the production deal-review runtime and
   production credentials.

## 3. Existing implementation mapping

| Capability | Current implementation | Action |
|---|---|---|
| Shared Deal State Graph | Active | Preserve as domain source of truth |
| Pod DAG | `PodExecutionCoordinator` plus common Pod/Domain Agent runtime | Complete remaining helper-Agent migration |
| Pod artifacts/handoffs/events | Typed persistence and transactional outbox exist | Keep as domain source of truth and link from AgentRun |
| AI provider calls | `AIConnection` behind central capability router | Keep direct provider SDK use prohibited outside this boundary |
| User BYOK/fallback | User profile, environment fallback, policy routing, provider fallback | Complete Secret Manager and production tenant policy deployment |
| AI usage audit | `ai_brain_used`, typed `brain_usage_events`, routing events, provider health | Add live provider-health probe worker and cost reconciliation |
| Common Agent contract | Missing before this change | Added in first vertical slice |
| Agent/Brain registry | Missing before this change | Added in first vertical slice |
| Generic AgentRun state machine | Durable v1 active for Pods and selected inner Domain Agents | Complete remaining helper-Agent migration and worker replay |
| Tool contract/registry | Manifest, registry, audited executor, read-only ledger tools active | Add approved side-effect tools only through isolated workers |
| Governed learning lifecycle | Candidate/evaluation/canary/promotion/rollback active | Add automated evaluation jobs and policy deployment adapters |
| Coding Pod | Durable queue, lease/recovery/cancel, remote sandbox adapter active | Deploy and attest the external isolated executor service |

## 4. First vertical slice implemented

The first slice is deliberately compatibility-first:

- `agent_platform/contracts`: Agent, execution, evidence, result, Brain, and routing contracts
- `agent_platform/registry`: version-aware Agent registry and Brain profile registry
- `agent_platform/brains`: current `AIConnection` adapter and deterministic capability router
- `agent_platform/runtime/legacy_adapter.py`: migration adapter for existing Agents
- `agent_platform/bootstrap.py`: registered Pod AI Reviewer and contract facade
- Chief Pod execution now invokes Pod AI Reviewer through the common contract
- Chief Pod review applies the capability/privacy Brain hard filter to the resolved
  user Brain before any LLM call; restricted cloud routes fall back to deterministic review
- Pod review persistence includes a secret-safe `agentPlatform` envelope
- Contract tests cover typed locators, authoritative-LLM prohibition, secret exclusion,
  registry conflicts, routing hard filters, AI adapter compatibility, and Pod integration
- `agent_platform/runtime/durable_runtime.py`: persisted state machine, idempotency,
  policy retry, cooperative cancellation, and secret-safe result storage
- typed `agent_runs`, `agent_run_steps`, and `brain_usage_events` tables for
  SQLite/PostgreSQL, with Chief Deal Room read/cancel APIs
- six Chief Pods run through the common durable Pod contract; principal inner
  Land, Legal, Market, Feasibility, Cash Flow, PF, Risk, Credit, and Public-policy
  Agents are executed through the common Domain Agent runtime
- AgentRun deadline checks, consumed cost/LLM/tool-call budgets, worker leases,
  heartbeat, expired-worker recovery, and recovery outbox events
- transactional AgentRun/outbox writes plus CalculationRun and handoff outbox
  events, a leased dispatcher, retry backoff, dead-letter state, and manual requeue
- versioned tenant/user Brain routing policy, persisted provider health and routing
  decisions, capability/privacy/budget hard filters, and multi-provider fallback
- `ToolManifest`, versioned `ToolRegistry`, audited/idempotent `ToolExecutor`, and
  real read-only Deal State and CalculationRun tools
- governed learning candidates from Excel and glossary discovery, with evaluation,
  canary, human promotion, and rollback records
- an isolated Coding Pod control plane with durable jobs, exclusive leases,
  heartbeat/recovery/cancellation, and a remote HTTPS sandbox executor adapter

### Controlled-implementation limitations

This slice is not the final distributed production runtime:

- All six Chief Pods and their principal Domain Agents use the common runtime, but
  several report, visualization, optimization, and export helper Agents in the full
  PF-review path still use direct compatibility calls and must be migrated.
- Deadlines and budgets are enforced at runtime boundaries. Python cannot preempt a
  hung synchronous legacy callable safely; hard termination requires process or
  container workers. Expired leases are failed and emit recovery events, but replay
  still requires the orchestrator to reconstruct a new request.
- The transactional outbox, retry, dead-letter, and requeue paths are active. A
  production message broker, multi-worker delivery deployment, and PostgreSQL
  concurrency CI remain deployment gates.
- Central multi-profile routing is active for `AIConnection`, including provider
  health, policy versions, routing events, and fallback. Production Secret Manager,
  scheduled health probes, and tenant policy administration remain open.
- The ToolRegistry currently exposes real read-only ledger tools. Mutation,
  approval, waiver, export publication, messaging, and Git tools remain unavailable
  until each has an explicit approval resolver and isolated execution policy.
- Learning governance is persisted, but automated benchmark/canary workers and
  deployment of an approved policy version remain open.
- Coding Pod queueing and the remote sandbox adapter are real; the isolated executor
  service itself is an external deployment dependency and is not simulated in the
  API process.

These limitations are visible in the manifest and backlog. The feature must not be
described as a completed production Agent Platform until the P1 gates pass.

## 4.1 Stub registry

| Stub ID | Location | Lane | Production blocker | Resolution |
|---|---|---|---|---|
| TEST-BRAIN-001 | `agent_platform/brains/ai_connection_adapter.py::MockBrain` | Prototype/test only | No | It is never registered by production bootstrap and exists only for deterministic contract tests. |

## 5. Contract corrections adopted

The source specification is applied with these changes:

1. `SourceLocator` is a typed object, not `list[str]`.
2. `LLM_ONLY + deterministicAuthorityRequired` fails schema validation.
3. Learning event creation belongs to a learning service, not every domain Agent.
4. Recalculation events and AgentRun states remain separate linked state machines.
5. Historical routing success is neutral until a minimum sample threshold is met.
6. Asset site address/PNU is valid project evidence; personal address/identity data
   remains subject to privacy policy and redaction.
7. Existing FastAPI routes and repositories are extended rather than replaced by a
   duplicate `/api` stack or a second JSON state store.

## 6. Target runtime sequence

```text
AgentRequest
  -> Registry/version/input check
  -> Capability/evidence/privacy check
  -> ExecutionPlan
  -> Brain routing when required
  -> deterministic/tool/LLM execution
  -> schema/evidence/consistency validation
  -> AgentResult
  -> AgentRun + steps + tool calls + Brain usage persistence
  -> existing Pod artifact/handoff/deal-state materialization
```

## 7. Next implementation stages

### P1-A: Durable Agent runtime

- Typed `agent_runs`, `agent_run_steps`, and `brain_usage_events` tables are active.
- State transitions, idempotency uniqueness, policy retry, cooperative cancellation,
  deadline/budget guards, worker lease/recovery, and transactional outbox delivery
  are active.
- Add isolated process workers for hard timeout, automatic request reconstruction
  after worker loss, and PostgreSQL multi-worker concurrency CI.
- Link an AgentRun to existing project, Pod artifact, CalculationRun, handoff,
  evidence, assumption, metric, and RiskIssue IDs.

### P1-B: Central Brain routing

- User/tenant/system fallback selection uses versioned policy records and Brain profiles.
- Apply hard filters for data classification, provider policy, local-only, context,
  cost, capability, health, and prohibited task.
- Candidates, rejection reasons, score components, selection, fallback, policy
  version, provider health, usage, and cost are persisted.
- Require tenant-approved policy versions and minimum-sample/decay rules before
  historical-success scoring affects routing.

### P1-C: Domain Agent migration

Migration order:

1. Pod AI Reviewer (active)
2. Six Chief Pod runtime envelopes (active)
3. Principal Land/Legal/Market/Feasibility/Cash Flow/PF/Credit Agents (active)
4. Remaining report, visualization, optimization, and export helper Agents (open)

Deterministic engines remain services/tools and are never converted into LLM Agents.

### P1-D: Tool control

- `ToolManifest`, `ToolRegistry`, and an audited/idempotent executor are active.
- Real read-only Deal State and CalculationRun tools are registered.
- Role, tenant/project scope, data classification, idempotency, and human-approval
  contracts are enforced.
- Add isolated implementations for approved calculation and side-effect tools.
- Git, file deletion, external messaging, DB mutation, report publication, approval,
  waiver, and override are side-effect tools.

### P2: Learning and Coding Pod

- Learning candidates, evaluations, canary results, promotion decisions, and
  rollback records are persisted; Excel and glossary discovery create governed
  candidates without automatic promotion.
- Coding jobs, leases, heartbeat, recovery, cancellation, and the remote sandbox
  contract are active and isolated from production deal-review execution.
- Deploy benchmark/canary workers and the external Coding sandbox service.
- Human approval remains mandatory for production prompt/routing/policy promotion.

## 8. Acceptance gates

The Agent Platform may be marked production-ready only when:

- all AgentRun transitions are durably persisted;
- idempotency and cancellation are tested;
- confidential/restricted routing fails closed;
- no raw secret is found in payloads, logs, artifacts, or DB rows;
- evidence/source-locator validation is enforced for report-grade output;
- deterministic financial outputs reference CalculationRun and engine versions;
- Brain usage, tokens, cost, and fallback reason are auditable;
- PostgreSQL isolation and RBAC tests pass;
- replay and provider-failure tests pass;
- learning promotion and rollback require an approved policy version.

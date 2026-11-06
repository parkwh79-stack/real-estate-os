# Pod / Agent control audit

Updated: 2026-09-15

The user excluded paid infrastructure and services from this continuation.
This audit used source inspection, isolated local tests, injected AI responses,
and in-memory failure simulation. It did not call external LLMs, execute a
remote coding job, provision infrastructure, or modify business records.

## Coverage

Reviewed `agents/ai_router.py`, `agent_policy.py`, `pod_ai_reviewer_agent.py`,
`consensus_engine.py`, domain Agent/Pod runtime adapters, the Agent/Tool/Brain
registries, operational repository/outbox/worker, Coding Pod service/worker/
remote executor, and the direct Coding Worker entrypoint. Root orchestration,
handoff and artifact persistence changes are recorded separately.

The financial engines remain the numeric authority. Consensus model output
only chooses existing conflict keys and supplies advisory rationale; actual
patch values still come from deterministic resolution rules. The loop has a
round limit and stops when a round applies no resolution. Central AIConnection
routing supplies provider privacy/local-only controls and bounded fallback;
this audit does not certify an unconfigured provider or paid service as tested.

## Fixed defects

| Finding | Resulting behavior |
|---|---|
| Domain adapters converted `failed`, `error`, cancelled, blocked or empty results into completion | Shared outcome mapping preserves execution failures/cancellation/blocks; empty output fails. Completed risk, data-quality and legal analyses with business `blocking`/`blocking_issue` findings remain completed with review. Failed results no longer claim deterministic checks passed. |
| Durable runtime promoted an explicit FAILED AgentResult back to completed | FAILED is an accepted terminal outcome and remains failed in the stored run. |
| AI tenant context could override the active worker/request tenant | Conflicting active scope, options and AI context tenants are rejected before Agent execution/persistence. |
| A model's repeated consensus key applied one conflict more than once in a round | Model order is deduplicated into a permutation of existing conflict keys. A failed round clears old model rationale. |
| Model review findings replaced deterministic PF blockers in the displayed review status | Deterministic findings are always retained, so a model's positive opinion cannot turn a senior-shortfall blocker into a passing review. |
| Pod review routing discarded enclosing local-only/provider exclusion/cost controls | Those controls now reach the actual AIConnection request. This does not activate any provider. |
| Explicit Agent version lookup bypassed an inactive manifest | Inactive Agents are rejected for explicit and automatic selection. |
| Tool versions were sorted lexically (`1.9` above `1.10`) | Tool selection uses the registry's numeric version-component ordering. |
| Direct Coding Worker invocation could not import the application and lacked a hardened deployment gate | Direct `--help` works; `--once` is available for local tests. Production/prod/staging exits before repository or executor setup. |
| Remote Coding executor followed redirects, accepted malformed artifact IDs and repeated remote error text | Redirects are rejected; ambiguous/credential-bearing endpoint URLs and non-list/blank artifact IDs fail; transport and remote error messages use stable codes without raw provider text. |

Initial validation: **108 tests passed** across the new control-boundary cases
plus existing domain/control-plane, durable runtime, operational runtime,
consensus, Pod reviewer, routing and policy suites. Follow-up validation:
**45 domain control tests passed**, covering business versus execution status
and explicit `completed_with_review`/`completed_with_warnings` preservation;
**12 Chief orchestration/recalculation integration tests passed** after their
normal fixture inputs were completed. Full-suite integration is recorded in
the parent audit report.

Execution completion does not grant approval readiness. In particular, Risk
Register `needs_review` caused by missing financial artifacts remains an
approval-readiness blocker, although the risk analysis itself completed. Known
`high_risk` findings retain their existing nonblocking readiness treatment.

The Chief orchestration and recalculation integration fixtures now explicitly
provide their mock product revenues, land/construction cost breakdown and
construction dates. Their prior total-only or incomplete inputs relied on
errors being swallowed. Land evidence uses the existing dictionary contract;
the sparse ledger keeps owners and registry evidence missing while providing
the required empty owners list. Event lifecycle and Pod assertions are retained;
no production allocation fallback or relaxed runtime-success check was added.

Final endpoint fixture corrections use the same explicit cost/date and land
evidence contracts. The isolated recent-work test now stores its prior completed
analysis with warnings before expecting `resume_open_reviews`; the readiness
flag alone did not establish a resumable run. The empty cost-schedule Agent
regression now requires an execution failure and unchanged project data instead
of accepting an empty successful artifact. Runtime guards were not relaxed.
The endpoint fixture isolates its in-memory orchestration store as well as its
SQLite file. Final focused endpoint and cost-schedule verification: **7 passed**
(existing framework deprecation warnings only).

## Remaining activation requirements

**TODO-SDD — Coding job atomic transitions.** `CodingPodService` currently reads
lease ownership and then uses an unconditional operational upsert. An offline
in-memory reproduction inserted cancellation between those steps: the final
status became `succeeded`, overwriting `cancelled`. Completion also lacks an
attempt-number fence when a worker ID is reused. Correct repair requires an
atomic compare-and-swap on job ID, tenant, lease owner, attempt, state version,
nonterminal status and live lease. The new hardened entrypoint gate prevents
this unqualified path from being activated in staging/production; local
compatibility is not a substitute for that repair.

**TODO-SDD — Sandbox proof.** No remote sandbox was deployed or exercised.
`allowedPaths`, branch names and returned artifact IDs form a request contract;
actual filesystem containment, network denial, artifact lineage, cancellation
and remote-process termination still require executor-side verification.
Rejecting redirects does not prove that an arbitrary configured server is an
isolated sandbox.

**TODO-SDD — Semantic output change detection.** Chief Pod `outputHash` hashes
the full artifact, including workflow and Agent run IDs. An offline pure-function
reproduction used equal business metrics with two different run IDs and
produced different hashes. A re-execution can therefore raise `output_changed`
without a changed financial result. A stable business-output contract must be
specified before excluding provenance fields; this audit does not invent that
policy or remove provenance from persisted artifacts.

The shared orchestration/proposal JSON fields that also contain human review
state remain subject to the separate runtime activation gate. No worker
received human approval authority, and no LLM response was used to authorize
production deployment or change financial calculation rules.

# Workspace Page Data Contract

Updated: 2026-07-03

Project: REALYTICS x PLATFORM9

This document fixes the contract between project workspace pages and the Shared Deal State Graph.

The purpose is simple: every page must show data from the same project memory, not its own mock state or stale local assumptions.

## Source Endpoint

All project workspace pages should start from:

```text
GET /projects/{project_id}/workspace/context?page={page}&track={workflow_track}
```

The response contains:

```text
projectMemory
dealStateGraph
evidenceVault
assumptionBook
missingInputRegister
taskGraph
agentPods
podRuns
decisionGate
pageData
pageDataContract
```

`pageDataContract` is returned by the API so that frontend, tests, and future agents can verify what each screen is allowed to depend on.

## Contract Rules

1. A page must not invent project name, PF amount, parcel count, evidence count, risk status, or financial metrics.
2. A page must read the same `projectMemory.projectName` and `projectId` as every other page.
3. A page may call its specialized API after loading workspace context, but the context remains the page's source-of-truth entry point.
4. If a field is missing, the UI must show an honest empty or needs-review state.
5. Page-specific calculations should be stored as artifacts or first-class records before being presented as real results.

## Page Contracts

| Page | Normalized Page | Source of Truth | Required `pageData` Fields |
|---|---|---|---|
| Project Overview | `overview` | `projects`, `chief_orchestration_state`, `chief_pod_artifacts` | `sourceDocumentSummary`, `landLedgerSummary`, `landEvidenceSummary`, `chiefArtifactSummary`, `agentPods`, `podRuns`, `taskGraph` |
| Input Package | `input_package` | `source_documents`, `projects.landLedger`, `land_evidence`, `assumption_overrides`, `chief_orchestration_state` | `sourceDocumentSummary`, `sourceDocuments`, `landLedgerSummary`, `landEvidenceSummary`, `landEvidenceRecords`, `assumptionOverrides`, `missingInputRegister`, `recommendedActions` |
| Land Review | `land` | `projects.land`, `projects.landLedger`, `land_evidence`, `chief_pod_artifacts.land_legal` | `land`, `landLedger`, `landLedgerSummary`, `landEvidenceSummary`, `landEvidenceRecords`, `pnuValidation` |
| Map | `map` | `projects.land`, `projects.landLedger`, `land_evidence`, `map_intelligence` | `land`, `landLedger`, `landLedgerSummary`, `landEvidenceSummary`, `landEvidenceRecords`, `mapDataEndpoint` |
| Marketability | `marketability` | `projects.market`, `projects.marketability`, `projects.marketEvidence`, `chief_pod_artifacts.market_product` | `market`, `marketability`, `marketEvidence`, `sourceDocumentSummary`, `chiefArtifactSummary` |
| Feasibility | `feasibility` | `projects.feasibility`, `projects.developmentFeasibility`, `projects.productMix`, `chief_pod_artifacts.feasibility_cashflow` | `feasibility`, `developmentFeasibility`, `cashFlow`, `productMix`, `chiefArtifactSummary` |
| Cash Flow | `cashflow` | `projects.cashFlow`, `projects.cashFlowRunResult`, `chief_pod_artifacts.feasibility_cashflow` | `feasibility`, `developmentFeasibility`, `cashFlow`, `productMix`, `chiefArtifactSummary` |
| PF Structure | `pf` | `projects.pfStructure`, `projects.riskRegister`, `projects.creditReviewDecision`, `chief_pod_artifacts.pf_credit` | `pfStructure`, `riskRegister`, `creditReviewDecision`, `latestPfCreditArtifact`, `chiefArtifacts` |
| Risk Register | `risk` | `projects.riskRegister`, `projects.creditReviewDecision`, `chief_pod_artifacts.pf_credit`, `evidence_graphs` | `riskRegister`, `creditReviewDecision`, `latestPfCreditArtifact`, `chiefArtifacts` |
| Evidence Auditor | `evidence` | `evidence_graphs`, `source_documents`, `land_evidence`, `chief_pod_artifacts` | `sourceDocumentSummary`, `landEvidenceSummary`, `chiefArtifactSummary`, `evidenceGraphEndpoint`, `sourceDocuments`, `landEvidenceRecords` |

## Verification

The API test `test_workspace_context_page_data_contracts_for_core_pages` verifies:

- every core page returns a `pageDataContract`;
- `normalizedPage` equals the contract page;
- all required `pageData` fields are present;
- every contract documents its DB/repository source of truth.

This is now the guardrail against returning a polished page backed by incomplete or unrelated state.

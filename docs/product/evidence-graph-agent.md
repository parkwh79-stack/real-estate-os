# Evidence Graph Agent

Updated: 2026-05-25

## Purpose

Evidence Graph Agent links report sections, claims, calculated numbers, agent artifacts, handoffs, evidence IDs, source documents, and project inputs into an auditable graph.

Its purpose is not to write another report. Its purpose is to answer:

> Where did this sentence, number, judgement, or calculation come from?

This is important for PF review, internal control, audit response, investment committee review, and future similar-case learning.

## Current Schema

Current graph schema:

```text
realytics-evidence-graph-v1
```

Current graph output:

| Field | Description |
|---|---|
| `nodes` | Source documents, evidence IDs, agent artifacts, handoffs, claims, risk rows, source locators, report sections, and agents |
| `edges` | Relationships such as `supports`, `derived_claim`, `used_in_report`, `created`, and `sent_to` |
| `summary` | Node/edge counts, claim count, evidence count, report section count, unlinked section count |
| `unlinkedReportSections` | Report sections without mapped supporting artifacts |
| `auditWarnings` | Warnings for missing source documents, evidence IDs, or report-section mappings |

## Node Types

| Node Type | Example |
|---|---|
| `project` | Current project input |
| `source_document` | Uploaded business plan, Excel file, or parser source |
| `evidence` | Evidence ID, API record, Excel locator, document page/table row |
| `agent_artifact` | Land Agent, Marketability Agent, PF structure, Scenario Engine output |
| `agent_handoff` | Typed cross-agent handoff envelope |
| `agent` | Upstream or downstream agent |
| `claim` | A calculated or judged value such as LTV, Exit sale rate, marketability score |
| `risk` | Risk Register row such as `R-MKT-001` or `R-LAND-001` |
| `source_locator` | Excel cell, document page/table row, API record path, or agent artifact path |
| `report_section` | Phase 11 report section |

## Risk Register Links

Evidence Graph now creates one `risk:{riskId}` node for each Risk Register row.

Each risk row is linked to:

- its source agent artifact, for example `artifact:marketabilityAnalysis`,
- explicit evidence IDs when available,
- source document nodes when the evidence ID matches an archived document,
- source locator nodes such as Excel cells, parser field paths, API records, or agent artifact paths,
- claim nodes derived from the same source artifact.

The Risk Register row is also updated with `evidenceGraphLinks`:

```json
{
  "riskNodeId": "risk:R-MKT-001",
  "sourceArtifactNodeId": "artifact:marketabilityAnalysis",
  "evidenceNodeIds": ["evidence:comp-1"],
  "claimNodeIds": ["claim:marketabilityAnalysis:score"],
  "sourceDocumentNodeIds": ["source:archive-1"],
  "sourceLocatorNodeIds": ["locator:R-MKT-001:market-sheet:B12"]
}
```

This makes the audit path bidirectional: reviewers can start from a risk row and trace back to the document, Excel cell, API record, agent output, or claim that produced it.

## Current Claim Coverage

Baseline claim extraction includes:

- Developer profit margin
- PF readiness status
- Development profit
- Profit margin
- LTV
- LTC
- Exit sale rate
- Land readiness status
- PNU coverage ratio
- Official land price coverage ratio
- Registry evidence coverage ratio
- Marketability score
- Marketability grade
- Pricing judgement
- Cross-agent reconciliation status
- Legal and permit review status
- Quality gate pass/fail

## Phase 11 Section Mapping

The graph maps the 20 Phase 11 report sections to supporting artifacts. Examples:

| Report Section | Supporting Artifacts |
|---|---|
| Land securing | `landAgentReview` |
| Market analysis | `marketEvidenceReview`, `marketabilityAnalysis` |
| Business feasibility | `developerFeasibility`, `feasibility`, `marketabilityAnalysis` |
| Exit strategy | `marketabilityAnalysis`, `scenariosEvaluation`, `pfStructure` |
| Drawdown conditions | `legalPermitReview`, `landAgentReview`, `pfStructure` |
| Fund execution order | `pfStructure`, `scenariosEvaluation` |

## Implementation

Implemented files:

- `services/api-server/agents/evidence_graph_agent.py`
- `services/api-server/agents/orchestration_agent.py`
- `services/api-server/db/repositories/project_snapshot_repository.py`
- `services/api-server/tests/test_evidence_graph_agent.py`

The PF review orchestration now runs `evidence_graph_generation` after Phase 11 report generation and before the final quality check. Saved project snapshots include `evidenceGraph` as a captured artifact.

## Current Limits

- The graph is deterministic and rule-based.
- It maps report sections to artifact-level support, not exact sentence offsets yet.
- It captures evidence IDs, source documents, source locators, and risk-row links when present, but it cannot invent missing evidence.
- Excel cell-level and PDF page/table/row locators depend on upstream parsers producing field locators.

## Next Steps

1. Add field-level parser locators for land, regulation, cost, schedule, PF, and covenant fields.
2. Add report sentence IDs so each sentence can link to claims and evidence.
3. Persist graph records separately for querying across runs.
4. Connect graph claims to snapshot diff and similar-case search.
5. Add UI graph view for reviewers and auditors.

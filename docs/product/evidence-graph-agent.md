# Evidence Graph Agent

Updated: 2026-07-02

## Purpose

Evidence Graph Agent links report sections, claims, calculated numbers, agent artifacts, handoffs, evidence IDs, source documents, standardized term surfaces, and project inputs into an auditable graph.

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
| `nodes` | Source documents, evidence IDs, agent artifacts, handoffs, claims, risk rows, source locators, term surfaces, glossary terms, canonical fields, report sections, and agents |
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
| `term_surface` | Raw/normalized business term surface from Parser, Data Quality, or Pod artifact metadata |
| `glossary_term` | Approved standard term such as `total_sales_revenue` or `senior_tranche` |
| `canonical_field` | Standard operational field such as `feasibility_input.total_revenue` |
| `report_section` | Phase 11 report section |

## Term Surface Links

Evidence Graph now promotes DB-to-Pod term-surface payloads into first-class graph nodes.

Inputs:

- `project_data.parser_term_surfaces`
- `_meta.parserFieldProvenance.termSurfaces`
- artifact-level `termSurfaceMetadata`
- nested `termSurface` / `termSurfaces` emitted by Data Quality, Parser provenance, Term Annotation, and Chief Pod artifacts

Each term surface node preserves:

```json
{
  "rawText": "총분양수입",
  "normalizedSurface": "총분양수입",
  "textQualityFlags": {"isUnicodeNormalized": true},
  "termId": "total_sales_revenue",
  "canonicalField": "feasibility_input.total_revenue",
  "sourceField": "feasibility_input.total_revenue",
  "sourceLocator": {"pageNumber": 3, "sourceType": "document_page"},
  "matchType": "parser_field_map",
  "confidence": 0.7
}
```

Relationships:

| Edge | Meaning |
|---|---|
| `project:current -> term_surface` | Parser field provenance produced a term surface. |
| `artifact:{key} -> term_surface` | Data Quality or a Chief Pod artifact produced a term surface. |
| `term_surface -> glossary_term` | The raw/normalized surface matched an approved standard term. |
| `term_surface -> canonical_field` | The surface maps to an operational canonical field. |
| `term_surface -> source_locator` | The parser, Excel, API, evidence, or artifact locator for this surface. |
| `glossary_term -> canonical_field` | The approved term maps to the canonical field. |

Important rule:

If a parser field has no page, Excel cell, API row, or evidence locator, Evidence Graph keeps the term surface with `sourceType: parsed_field_path`. It does not fabricate document evidence.

Unmapped surface handling:

- If a `term_surface` node has no approved `termId` and no `canonicalField`, orchestration calls the glossary normalization layer after graph generation.
- Exact approved aliases are matched automatically and do not create duplicate candidates.
- New or low-confidence surfaces are saved as `glossary_term_candidates` with project ID, run ID, source field, source locator, and text-quality flags.
- The PF review run keeps the capture result in `artifacts.glossaryCandidateCapture` so reviewers can see how many unmapped terms were routed to the review queue.

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
- `services/api-server/storage.py`
- `services/api-server/tests/test_evidence_graph_agent.py`

The PF review orchestration now runs `evidence_graph_generation` after Phase 11 report generation and before the final quality check. Saved project snapshots include `evidenceGraph` as a captured artifact.

Evidence Graph persistence is implemented through JSON/SQLite `evidence_graphs` records keyed by `projectId + runId`.

APIs:

- `GET /projects/{project_id}/evidence-graphs`
- `GET /projects/{project_id}/orchestrations/{run_id}/evidence-graph`

Frontend:

- `/project/{id}/evidence` lists persisted graph records for a project.
- Reviewers can filter graph nodes by risk, report section, claim/calculation, source document, source locator, or evidence node.
- Selecting a node shows its metadata, connected graph edges, connected nodes, and any source locator nodes that identify document pages, Excel cells, API rows, evidence IDs, or agent artifact paths.
- If a material risk has only artifact-level support and no direct source locator, the UI explicitly flags that the Risk Register row needs evidence/source-locator hardening before audit reliance.

## Current Limits

- The graph is deterministic and rule-based.
- It maps report sections to artifact-level support, not exact sentence offsets yet.
- It captures evidence IDs, source documents, source locators, term surfaces, canonical field links, and risk-row links when present, but it cannot invent missing evidence.
- Excel cell-level and PDF page/table/row locators depend on upstream parsers producing field locators.

## Next Steps

1. Add report sentence IDs so each sentence can link to claims, term surfaces, and evidence.
2. Add graph query APIs for term surface, canonical field, source locator, risk node, report section, and claim lookups.
3. Connect graph claims and term surfaces to snapshot diff and similar-case search.
4. Add visual graph layout and source-locator query APIs for larger audits.
5. Add UI deep links from `term_surface` nodes to the glossary candidate review screen.

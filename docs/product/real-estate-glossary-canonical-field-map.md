# Real Estate Glossary and Canonical Field Map

Updated: 2026-06-22

## Purpose

REALYTICS uses many terms that can appear differently across business plans, Excel files, PF review reports, registry evidence, market evidence, legal documents, trust agreements, loan agreements, and agent outputs. The glossary standardizes those terms so every agent can map aliases to the same canonical data field.

This is a control layer, not just a UI label dictionary.

```text
new term or alias
  -> glossary candidate
  -> reviewer maps it to a canonical field
  -> parser / data quality / report / cash flow agents reuse the approved mapping
  -> source examples and review history remain auditable
```

## Current Implementation

Backend:

- Seed fixture: `services/api-server/data/fixtures/real_estate_glossary_seed.json`
- Use case: `services/api-server/use_cases/glossary.py`
- Router: `services/api-server/routers/glossary.py`
- Storage: `glossary_terms` in JSON runtime state and SQLite table
- Term-standard v3 import: `POST /glossary/standard-pack/import`
- Raw term normalization and new-term queue: `POST /glossary/normalize`
- CLI import: `services/api-server/scripts/import_term_standard_seed.py`

Frontend:

- Korean financial UI term map: `apps/web/lib/financial-terms.ts`
- Candidate review queues: `/glossary` and `/glossary/candidates`

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/glossary/terms` | List glossary terms. Supports `q`, `category`, `canonical_field`, and `review_status`. |
| POST | `/glossary/terms` | Create or update a glossary term with review history. |
| GET | `/glossary/terms/{term_id}` | Get one glossary term. |
| GET | `/glossary/resolve?term=...` | Resolve a term or alias to a reviewed glossary term. |
| POST | `/glossary/normalize` | Batch normalize uploaded/raw terms; exact aliases map automatically and unresolved terms are stored as review candidates. |
| GET | `/glossary/canonical-field-map` | Return canonical field to approved terms and aliases. |
| POST | `/glossary/standard-pack/import` | Import a REALYTICS term-standard v3 compatible seed payload. |

## Term Standard v3 DB Direction

The external REALYTICS AI LAB term-standard package is now treated as the preferred DB seed source for production standardization.

Reference assets:

| Asset | Purpose |
|---|---|
| `realytics_term_seed_v3.json` | 450-term seed pack with operational metadata. |
| `realytics_term_standard_v3.sqlite` | Reference SQLite database for terms, synonyms, new-term queue, calculation registry, agent routing, report templates, and prompt/RAG policy. |
| `realytics_term_standard_v3_postgres_ddl.sql` | Production PostgreSQL DDL reference. |

Runtime import:

```powershell
cd "services/api-server"
python scripts\import_term_standard_seed.py "C:\Users\User\Desktop\REALYTICS AI LAB 데이터베이스\realytics_term_seed_v3.json" --replace-existing
```

See also: `docs/product/term-standard-database-operating-model.md`.

## Term Schema

Each glossary term stores:

| Field | Description |
|---|---|
| `termId` | Stable term ID. |
| `preferredTerm` | Machine-friendly preferred term. |
| `displayNameKo` | Korean business display label. |
| `displayNameEn` | English reference label. |
| `aliases` | Korean, English, spreadsheet, report, and source-document variants. |
| `definitionKo` | Korean definition. |
| `canonicalField` | Canonical automation field, such as `feasibility_input.total_revenue`. |
| `canonicalPath` | Frontend/API path where useful, such as `feasibilityInput.totalRevenue`. |
| `category` | `land`, `feasibility`, `cash_flow`, `pf_structure`, `pf_metric`, `scenario`, etc. |
| `dataType` | `money`, `ratio`, `area`, `identifier`, `list`, `account`, `text`, etc. |
| `unit` | `KRW`, `sqm`, `ratio`, `KRW/sqm`, or null. |
| `sourceExamples` | Example source text and locator examples. |
| `reviewStatus` | `approved`, `pending_review`, `needs_revision`, `rejected`, `free_text`, or `deprecated`. |
| `reviewHistory` | Audit trail for mapping changes. |

## Baseline Terms

The current seed includes high-impact Korean real estate development finance, real asset finance, and legal/security terms:

| Term | Canonical Field |
|---|---|
| 총분양수입 | `feasibility_input.total_revenue` |
| 총사업비 | `feasibility_input.total_cost` |
| 대지면적 | `land.area_sqm` |
| 연면적 | `architectural_plan.gross_floor_area_sqm` |
| 세대수·호실수 | `architectural_plan.unit_count` |
| PF 대출금 | `pf_structure.amount.sum` |
| LTV | `pf_metrics.ltv` |
| LTC | `pf_metrics.ltc` |
| Exit 분양률 | `scenario.exitSalesRate` |
| Max Minus | `cashFlow.maxMinus` |
| 상환적립계좌 | `cashFlow.accounts.repaymentReserveAccount` |
| 개별공시지가 | `landEvidence.officialLandPrice.pricePerSqm` |
| PNU | `land.pnu` |
| 제한물권 | `landEvidence.registry.encumbrances` |
| 선순위 Tranche | `pf_structure.tranches.senior.amount` |
| 중순위 Tranche | `pf_structure.tranches.mezzanine.amount` |
| 후순위 Tranche | `pf_structure.tranches.junior.amount` |
| All-in Cost | `pf_structure.tranches.allInCost` |
| DSCR | `pf_metrics.dscr` |
| 원리금상환준비계좌 | `pf_structure.dsra.amount` |
| Cash Sweep | `cashFlow.waterfall.cashSweep` |
| 책임준공 | `credit_enhancement.responsible_completion` |
| 채무인수 | `credit_enhancement.debt_assumption` |
| 관리형토지신탁 | `legal.trust.managementLandTrust` |
| 우선수익권증서 | `legal.trust.beneficialCertificate` |
| Covenant | `legal.covenants` |
| 기한이익상실 사유 | `legal.eodTriggers` |
| 건축허가 | `legal.permits.buildingPermit` |
| 토지확보율 | `landAcquisition.acquisitionAreaRatio` |
| NOI | `real_asset.noi` |
| Cap Rate | `real_asset.capRate` |
| 선매각 | `real_asset.forwardSale.price` |

## Standardization Domains

The glossary now covers three practical domains.

| Domain | Purpose | Examples |
|---|---|---|
| Development finance | Standardize feasibility, product mix, PF metrics, monthly cash flow, and scenario variables. | 총분양수입, 총사업비, Max Minus, Exit 분양률, LTV, LTC, DSCR |
| Real asset finance | Standardize income-producing and exit-value assumptions for logistics, office, REIT, and refinancing workflows. | NOI, Cap Rate, 선매각, 감정평가액, 담보가치 |
| Legal/security | Standardize Korean legal and collateral terms used in CP, covenant, EOD, trust, registry, and land evidence review. | 관리형토지신탁, 우선수익권증서, 책임준공, 채무인수, 제한물권, 건축허가 |

## Operating Rules

1. A term that changes a number, legal status, risk grade, approval condition, or report conclusion must not remain a free-text label.
2. Parser Agent may extract source text, but automation must use the approved canonical field.
3. Data Quality Agent must flag unresolved high-impact terms as glossary review items.
4. Evidence Graph nodes should carry `termId` and `canonicalField` when a claim depends on a glossary-controlled term.
5. Organization-specific wording can be added as aliases, but it must map to a canonical field or be explicitly marked `free_text`.
6. Legal terms must preserve source locators, because the system cannot treat a legal/security condition as complete without evidence.

## Agent Rule

If a new term affects numbers, legal status, land evidence, cash flow, PF conditions, risk grading, approval readiness, or report conclusions, it must be mapped to a canonical field before it is used for automated decision logic.

If an alias cannot be resolved:

```text
Parser Agent extracts the term
  -> Data Quality Agent creates a glossary candidate
  -> Orchestration Agent flags glossary review
  -> Reviewer approves canonical field, marks free text, or rejects
  -> Future runs reuse the approved mapping
```

## Near-Term Backlog

- Add UI review screen for glossary candidates.
- Use glossary resolution in Parser Agent non-market fields: land, regulation, cost, schedule, PF, and covenants.
- Attach glossary term IDs to Evidence Graph claim nodes.
- Add import/export for organization-specific glossary packs.
- Add tenant-level override policy once real tenant isolation is enforced.

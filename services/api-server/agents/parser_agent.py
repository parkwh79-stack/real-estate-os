from typing import Any, Dict

import fitz  # PyMuPDF

from agents.ai_connection import AIConnection, resolve_ai_context


PARSER_AGENT_VERSION = "realytics-parser-v2.4"


class ParserAgent:
    def __init__(self, ai_context: Dict[str, Any] | None = None):
        self.ai_context = resolve_ai_context(ai_context)
        self.ai = AIConnection()

    def parse_document(self, file_path: str) -> str:
        """Extract text from a PDF document."""
        text = ""
        try:
            doc = fitz.open(file_path)
            for page_index, page in enumerate(doc, start=1):
                text += f"\n\n--- PAGE {page_index} ---\n"
                text += page.get_text()
            doc.close()
        except Exception as e:
            print(f"Error reading PDF {file_path}: {e}")
            return ""
        return text

    def extract_project_data(self, text: str, ai_context: Dict[str, Any] | None = None) -> dict:
        """Use the configured AI provider to extract structured project data from raw text."""
        max_chars = 30000
        if len(text) > max_chars:
            text = text[:max_chars]

        prompt = self.build_extraction_prompt(text)
        context = resolve_ai_context(ai_context or self.ai_context)
        if context["runtime_provider"] == "mcp_host_llm":
            return self._get_host_llm_extraction_request(text, prompt, context)

        result = self.ai.extract_json(
            prompt,
            system="You are a precise data extraction assistant. Output only JSON.",
            ai_context=context,
        )
        if result.data is not None:
            result.data["_meta"] = {
                **result.data.get("_meta", {}),
                "parsing_source": "ai",
                "confidence": 0.75,
                "ai_connection": result.meta,
            }
            return result.data

        print(f"Warning: AI extraction fallback used. {result.error}")
        return self._get_mock_parsed_data(result.error or "AI extraction unavailable", result.meta)

    def build_extraction_prompt(self, text: str) -> str:
        return """
        You are a Real Estate PF (Project Financing) expert. Your task is to extract structured information from the following business plan text.
        Output ONLY valid JSON matching the following structure. Do not include any markdown formatting (like ```json).

        {
          "project": {
            "name": "Project Name",
            "type": "residential|commercial|mixed_use|logistics",
            "review_date": "YYYY-MM-DD",
            "financial_institution": "Institution Name"
          },
          "land": {
            "address": "Address",
            "pnu": "PNU if available",
            "area_sqm": 0.0
          },
          "regulation": {
            "zoning": "Zoning Type",
            "legal_far": 0.0,
            "legal_bcr": 0.0,
            "applied_far": 0.0,
            "applied_bcr": 0.0
          },
          "architectural_plan": {
            "gross_floor_area_sqm": 0.0,
            "saleable_area_sqm": 0.0,
            "building_coverage_sqm": 0.0,
            "unit_count": 0,
            "parking_count": 0
          },
          "sales_assumptions": {
            "avg_price_per_sqm": 0,
            "other_revenue": 0,
            "product_mix": [
              {
                "type": "Unit Type",
                "productType": "apartment|officetel_residential|officetel_office|mixed_use_residential|commercial_retail|knowledge_industry_center|section_office|serviced_residence|other_saleable",
                "exclusiveAreaPy": 0,
                "contractAreaPy": 0,
                "units": 0,
                "pricePerPy": 0,
                "totalRevenue": 0,
                "evidenceIds": ["source-page-or-document-id"],
                "sourceLocator": {
                  "pageNumber": 0,
                  "table": "Table name or index",
                  "row": "Row label or index",
                  "column": "Column label",
                  "fieldName": "pricePerPy|totalRevenue|units|contractAreaPy",
                  "textExcerpt": "Short source excerpt",
                  "confidence": 0.0
                }
              }
            ]
          },
          "market": {
            "proposedPricePerPy": 0,
            "demandLevel": "strong|moderate|weak|unknown",
            "upcomingSupplyUnits": 0,
            "comparables": [
              {
                "caseId": "COMP-001",
                "name": "Comparable Project Name",
                "productType": "apartment|officetel_residential|commercial_retail|knowledge_industry_center|serviced_residence|other_saleable",
                "distanceM": 0,
                "pricePerPy": 0,
                "sourceName": "Document or data source name",
                "sourceType": "manual_upload|public_api|partner_api|internal_db|web|estimate",
                "sourceUrlOrEvidenceId": "document-page-or-evidence-id",
                "baseDate": "YYYY-MM-DD",
                "confidence": 0.0,
                "sourceLocator": {
                  "pageNumber": 0,
                  "table": "Table name or index",
                  "row": "Row label or index",
                  "column": "Column label",
                  "fieldName": "pricePerPy",
                  "textExcerpt": "Short source excerpt",
                  "confidence": 0.0
                }
              }
            ],
            "rentComparables": [
              {
                "caseId": "RENT-001",
                "name": "Rent Comparable Name",
                "productType": "officetel_residential|commercial_retail|knowledge_industry_center|section_office|serviced_residence",
                "distanceM": 0,
                "monthlyRentPerPy": 0,
                "sourceName": "Document or data source name",
                "sourceType": "manual_upload|public_api|partner_api|internal_db|web|estimate",
                "sourceUrlOrEvidenceId": "document-page-or-evidence-id",
                "baseDate": "YYYY-MM-DD",
                "confidence": 0.0,
                "sourceLocator": {
                  "pageNumber": 0,
                  "table": "Table name or index",
                  "row": "Row label or index",
                  "column": "Column label",
                  "fieldName": "monthlyRentPerPy",
                  "textExcerpt": "Short source excerpt",
                  "confidence": 0.0
                }
              }
            ],
            "rentalAssumptions": {
              "monthlyRentPerPy": 0,
              "requiredYield": 0.0,
              "sourceUrlOrEvidenceId": "document-page-or-evidence-id",
              "sourceLocator": {
                "pageNumber": 0,
                "table": "Table name or index",
                "row": "Row label or index",
                "column": "Column label",
                "fieldName": "monthlyRentPerPy|requiredYield",
                "textExcerpt": "Short source excerpt",
                "confidence": 0.0
              }
            },
            "data_sources": [
              {
                "sourceName": "Source name",
                "sourceType": "manual_upload|public_api|partner_api|internal_db|web|estimate",
                "sourceUrlOrEvidenceId": "document-page-or-evidence-id",
                "baseDate": "YYYY-MM-DD",
                "licenseOrUsageNote": "Usage note",
                "confidence": 0.0,
                "sourceLocator": {
                  "pageNumber": 0,
                  "table": "Table name or index",
                  "row": "Row label or index",
                  "column": "Column label",
                  "fieldName": "sourceName|baseDate|licenseOrUsageNote",
                  "textExcerpt": "Short source excerpt",
                  "confidence": 0.0
                }
              }
            ]
          },
          "document_market_evidence": {
            "extractionNote": "Market, sale price, rent, and comparable data extracted from uploaded documents. Include only values found in the document; do not invent market evidence.",
            "sourceLocatorPolicy": "Use the PAGE markers in the document text and include pageNumber, table, row, column, fieldName, short textExcerpt, and confidence whenever a value is extracted from a document table or paragraph.",
            "proposedPricePerPy": 0,
            "demandLevel": "strong|moderate|weak|unknown",
            "upcomingSupplyUnits": 0,
            "productMix": [
              {
                "type": "Unit Type",
                "productType": "apartment|officetel_residential|officetel_office|mixed_use_residential|commercial_retail|knowledge_industry_center|section_office|serviced_residence|other_saleable",
                "exclusiveAreaPy": 0,
                "contractAreaPy": 0,
                "units": 0,
                "pricePerPy": 0,
                "totalRevenue": 0,
                "evidenceIds": ["document-page-or-evidence-id"],
                "sourceLocator": {
                  "pageNumber": 0,
                  "table": "Table name or index",
                  "row": "Row label or index",
                  "column": "Column label",
                  "fieldName": "pricePerPy|totalRevenue|units|contractAreaPy",
                  "textExcerpt": "Short source excerpt",
                  "confidence": 0.0
                }
              }
            ],
            "comparables": [],
            "manualComparables": [],
            "transactionComparables": [],
            "presaleComparables": [],
            "listingComparables": [],
            "rentComparables": [],
            "rentalAssumptions": {
              "monthlyRentPerPy": 0,
              "requiredYield": 0.0,
              "sourceUrlOrEvidenceId": "document-page-or-evidence-id",
              "sourceLocator": {
                "pageNumber": 0,
                "table": "Table name or index",
                "row": "Row label or index",
                "column": "Column label",
                "fieldName": "monthlyRentPerPy|requiredYield",
                "textExcerpt": "Short source excerpt",
                "confidence": 0.0
              }
            },
            "data_sources": []
          },
          "cost_assumptions": {
            "construction_cost_per_sqm": 0
          },
          "feasibility_input": {
            "total_revenue": 0,
            "total_cost": 0,
            "land_cost": 0,
            "construction_cost": 0,
            "finance_cost": 0,
            "other_cost": 0
          },
          "pf_structure": [
            {
              "tranche": "Tranche Name (e.g., Tranche A)",
              "amount": 0,
              "interest_rate": 0.0,
              "fee_rate": 0.0,
              "priority": 1,
              "all_in_cost": 0.0
            }
          ],
          "developer_info": {
            "name": "Developer Name",
            "credit_rating": "Rating",
            "financials": "Summary"
          },
          "contractor_info": {
            "name": "Contractor Name",
            "credit_rating": "Rating",
            "order_backlog": "Backlog",
            "financials": "Summary"
          },
          "schedule": {
            "land_purchase": "YYYY-MM",
            "permit_approval": "YYYY-MM",
            "construction_start": "YYYY-MM",
            "completion": "YYYY-MM"
          },
          "covenants": {
            "claim_preservation": ["Item 1", "Item 2"],
            "eod": ["EOD 1", "EOD 2"],
            "drawdown_conditions": ["Cond 1", "Cond 2"],
            "execution_order": ["1. Item", "2. Item"]
          },
          "source_locators": {
            "project.name": {
              "pageNumber": 0,
              "table": "Table name or index",
              "row": "Row label or index",
              "column": "Column label",
              "fieldName": "project.name",
              "textExcerpt": "Short source excerpt",
              "confidence": 0.0
            },
            "land.address": {},
            "land.area_sqm": {},
            "regulation.applied_far": {},
            "architectural_plan.gross_floor_area_sqm": {},
            "sales_assumptions.avg_price_per_sqm": {},
            "feasibility_input.total_revenue": {},
            "pf_structure[0].amount": {},
            "schedule.completion": {},
            "covenants.drawdown_conditions": {}
          }
        }

        Source locator rule:
        - For every extracted project, land, regulation, architectural, sales, cost, feasibility, PF, schedule, covenant, and market field, include pageNumber, table, row, column, fieldName, textExcerpt, and confidence whenever the source text supports it.
        - If a value is inferred and no document location exists, leave the locator empty and keep confidence conservative.

        Business Plan Text:
        ---
        """ + text + "\n---"

    def _get_host_llm_extraction_request(
        self,
        text: str,
        prompt: str,
        ai_context: Dict[str, Any],
    ) -> dict:
        extraction_instructions = prompt.rsplit("Business Plan Text:", 1)[0].strip()
        return {
            "_meta": {
                "parsing_source": "host_llm_required",
                "confidence": None,
                "warning": (
                    "Claude Desktop is the MCP host LLM. The MCP server extracted PDF text "
                    "and is returning instructions for the host LLM to perform the JSON extraction."
                ),
                "ai_connection": ai_context,
            },
            "host_llm_extraction": {
                "instruction": (
                    "Use your Claude Desktop host LLM reasoning to extract project_data JSON "
                    "from document_text according to extraction_prompt. After extraction, call "
                    "orchestrate_pf_review or generate_pf_report with that project_data."
                ),
                "expected_output": "project_data_json",
                "document_text": text,
                "extraction_prompt": extraction_instructions,
            },
        }

    def _get_mock_parsed_data(
        self,
        reason: str = "mock fallback",
        ai_context: Dict[str, Any] | None = None,
    ) -> dict:
        """Return explicit mock data when real AI extraction is unavailable."""
        resolved_context = resolve_ai_context(ai_context or self.ai_context)
        return {
          "_meta": {
            "parsing_source": "mock",
            "confidence": 0.0,
            "warning": "AI parsing did not use real source extraction.",
            "reason": reason,
            "ai_connection": resolved_context,
          },
          "project": {
            "name": "Parsed AI Project",
            "type": "mixed_use",
            "review_date": "2026-06-01",
            "financial_institution": "Mock Bank"
          },
          "land": {
            "address": "Mock Address 123",
            "area_sqm": 10000.0
          },
          "regulation": {
            "applied_far": 700.0,
            "applied_bcr": 60.0
          },
          "architectural_plan": {
            "gross_floor_area_sqm": 70000.0,
            "saleable_area_sqm": 50400.0,
            "building_coverage_sqm": 6000.0,
            "unit_count": 500,
            "parking_count": 650
          },
          "sales_assumptions": {
            "avg_price_per_sqm": 3968254,
            "other_revenue": 0
          },
          "cost_assumptions": {
            "construction_cost_per_sqm": 1142857
          },
          "feasibility_input": {
            "total_revenue": 200000000000,
            "total_cost": 150000000000,
            "land_cost": 50000000000,
            "construction_cost": 80000000000,
            "finance_cost": 10000000000,
            "other_cost": 10000000000
          },
          "pf_structure": [
            {
              "tranche": "Tranche A",
              "amount": 100000000000,
              "interest_rate": 0.05,
              "fee_rate": 0.01,
              "priority": 1,
              "all_in_cost": 0.06
            }
          ],
          "developer_info": {
            "name": "AI Developer Corp",
            "credit_rating": "A-",
            "financials": "Assets 100B"
          },
          "contractor_info": {
            "name": "AI Construction",
            "credit_rating": "AA-",
            "order_backlog": "10T",
            "financials": "Debt Ratio 100%"
          },
          "schedule": {
            "construction_start": "2026-09",
            "completion": "2029-09"
          },
          "covenants": {
            "claim_preservation": ["Trust first-priority beneficial interest"],
            "eod": ["Failure to satisfy responsibility completion obligation"],
            "drawdown_conditions": ["Permit approval completed"],
            "execution_order": ["1. Taxes", "2. Essential project costs"]
          }
        }

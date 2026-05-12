import os
import fitz  # PyMuPDF
import json
from openai import OpenAI

class ParserAgent:
    def __init__(self):
        self.client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", "dummy-key-for-test"))

    def parse_document(self, file_path: str) -> str:
        """Extracts text from a PDF document."""
        text = ""
        try:
            doc = fitz.open(file_path)
            for page in doc:
                text += page.get_text()
            doc.close()
        except Exception as e:
            print(f"Error reading PDF {file_path}: {e}")
            return ""
        return text

    def extract_project_data(self, text: str) -> dict:
        """Uses OpenAI to extract structured project data from the raw text."""
        # Truncate text if it's too long for the context window
        max_chars = 30000 
        if len(text) > max_chars:
            text = text[:max_chars]

        prompt = """
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
            "other_revenue": 0
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
          }
        }

        Business Plan Text:
        ---
        """ + text + "\n---"

        try:
            # If dummy key is used (e.g. testing without real API), return mock parsed data
            if self.client.api_key == "dummy-key-for-test":
                print("Warning: OPENAI_API_KEY not found. Using mock parsing.")
                return self._get_mock_parsed_data("OPENAI_API_KEY not configured")

            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a precise data extraction assistant. Output only JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content
            return json.loads(content)
        except Exception as e:
            print(f"Error during OpenAI extraction: {e}")
            return self._get_mock_parsed_data(str(e))

    def _get_mock_parsed_data(self, reason: str = "mock fallback") -> dict:
        """Returns mock data in case of OpenAI failure or missing key."""
        return {
          "_meta": {
            "parsing_source": "mock",
            "confidence": 0.0,
            "warning": "AI parsing did not use real source extraction.",
            "reason": reason
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
            "claim_preservation": ["신탁 1순위"],
            "eod": ["책임준공 미이행"],
            "drawdown_conditions": ["인허가 완료"],
            "execution_order": ["1. 조세", "2. 필수사업비"]
          }
        }

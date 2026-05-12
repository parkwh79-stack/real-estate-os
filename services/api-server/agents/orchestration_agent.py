from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from agents.development_feasibility_agent import DevelopmentFeasibilityAgent
from agents.legal_review_agent import LegalReviewAgent
from agents.marketability_agent import MarketabilityAgent
from agents.report_agent import ReportAgent
from core.calculations import (
    calculate_development_profit,
    calculate_exit_sale_rate,
    calculate_ltc,
    calculate_ltv,
    calculate_profit_margin,
)
from core.risk_assessment import assess_risk_item, calculate_overall_risk
from schemas.orchestration import PF_REVIEW_STEPS, PF_REVIEW_WORKFLOW


class OrchestrationAgent:
    def run_pf_review(
        self,
        project_id: str,
        project_data: Dict[str, Any],
        options: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        options = options or {}
        now = self._now()
        run = {
            "run_id": str(uuid4()),
            "project_id": project_id,
            "workflow": PF_REVIEW_WORKFLOW,
            "status": "running",
            "started_at": now,
            "completed_at": None,
            "options": options,
            "steps": [],
            "artifacts": {},
            "errors": [],
            "approval": None,
        }

        try:
            self._complete_step(run, "project_load", {
                "project": project_data.get("project", {}),
                "source": "PROJECTS_DB",
            })

            developer_feasibility = DevelopmentFeasibilityAgent().analyze(project_data)
            run["artifacts"]["developerFeasibility"] = developer_feasibility
            self._complete_step(run, "development_feasibility_analysis", {
                "summary": developer_feasibility["summary"],
                "profitMargin": developer_feasibility["developerReturns"]["profitMargin"],
                "profitOnCost": developer_feasibility["developerReturns"]["profitOnCost"],
                "pfStatus": developer_feasibility["pfReadiness"]["status"],
                "confidence": developer_feasibility["dataQuality"]["confidence"],
            })

            feasibility = self._calculate_feasibility(project_data)
            run["artifacts"]["feasibility"] = feasibility
            self._complete_step(run, "feasibility_calculation", feasibility)

            pf_structure = self._calculate_pf_structure(project_data)
            run["artifacts"]["pfStructure"] = pf_structure
            self._complete_step(run, "pf_structure_calculation", pf_structure)

            marketability = MarketabilityAgent().analyze(project_data)
            run["artifacts"]["marketabilityAnalysis"] = marketability
            self._complete_step(run, "marketability_analysis", marketability)

            legal_review = LegalReviewAgent().review(
                project_data,
                options.get("law_context"),
            )
            run["artifacts"]["legalPermitReview"] = legal_review
            self._complete_step(run, "legal_permit_review", {
                "status": legal_review["status"],
                "mcp": legal_review["mcp"],
                "requiredFollowUps": legal_review["requiredFollowUps"],
            })

            risk = self._assess_risk(project_data)
            run["artifacts"]["riskAssessment"] = risk
            self._complete_step(run, "risk_assessment", risk)

            report_agent = ReportAgent()
            structured_report = report_agent.generate_structured_report(project_data)
            report_markdown = report_agent.generate_report(project_data)
            report_artifact = {
                "report": structured_report,
                "report_markdown": report_markdown,
            }
            run["artifacts"]["phase11Report"] = report_artifact
            self._complete_step(run, "phase11_report_generation", {
                "template": structured_report["template"],
                "sectionCount": structured_report["qualityChecks"]["actualSectionCount"],
            })

            quality = self._quality_check(run)
            run["artifacts"]["qualityCheck"] = quality
            self._complete_step(run, "quality_check", quality)

            run["status"] = "completed" if quality["pass"] else "completed_with_warnings"
        except Exception as exc:
            run["status"] = "failed"
            run["errors"].append({"message": str(exc), "type": exc.__class__.__name__})

        run["completed_at"] = self._now()
        return run

    def _calculate_feasibility(self, data: Dict[str, Any]) -> Dict[str, Any]:
        feasibility = data.get("feasibility_input", {})
        total_revenue = feasibility.get("total_revenue", 0)
        total_cost = feasibility.get("total_cost", 0)
        development_profit = calculate_development_profit(total_revenue, total_cost)
        return {
            "total_revenue": total_revenue,
            "total_cost": total_cost,
            "development_profit": development_profit,
            "profit_margin": calculate_profit_margin(development_profit, total_revenue),
        }

    def _calculate_pf_structure(self, data: Dict[str, Any]) -> Dict[str, Any]:
        feasibility = data.get("feasibility_input", {})
        tranches = data.get("pf_structure", [])
        total_pf = sum(t.get("amount", 0) for t in tranches)
        total_revenue = feasibility.get("total_revenue", 0)
        total_cost = feasibility.get("total_cost", 0)
        finance_cost = feasibility.get("finance_cost", 0)
        return {
            "total_pf_amount": total_pf,
            "ltv": calculate_ltv(total_pf, total_revenue),
            "ltc": calculate_ltc(total_pf, total_cost),
            "exit_sale_rate": calculate_exit_sale_rate(total_pf, finance_cost, total_revenue),
            "tranche_count": len(tranches),
        }

    def _assess_risk(self, data: Dict[str, Any]) -> Dict[str, Any]:
        assessed_items = [assess_risk_item(item) for item in data.get("risk_items", [])]
        overall = calculate_overall_risk(assessed_items)
        return {"overall": overall, "items": assessed_items}

    def _quality_check(self, run: Dict[str, Any]) -> Dict[str, Any]:
        report = run["artifacts"]["phase11Report"]["report"]
        report_quality = report["qualityChecks"]
        required_artifacts = [
            "developerFeasibility",
            "feasibility",
            "pfStructure",
            "marketabilityAnalysis",
            "legalPermitReview",
            "riskAssessment",
            "phase11Report",
        ]
        missing_artifacts = [
            artifact for artifact in required_artifacts if artifact not in run["artifacts"]
        ]
        legal_status = run["artifacts"]["legalPermitReview"]["status"]
        return {
            "pass": report_quality["isComplete"] and not missing_artifacts and legal_status != "blocking_issue",
            "reportComplete": report_quality["isComplete"],
            "legalReviewStatus": legal_status,
            "missingReportSections": report_quality["missingSections"],
            "missingArtifacts": missing_artifacts,
        }

    def _complete_step(self, run: Dict[str, Any], key: str, output: Dict[str, Any]) -> None:
        title = self._step_title(key)
        run["steps"].append({
            "key": key,
            "title": title,
            "status": "completed",
            "started_at": self._now(),
            "completed_at": self._now(),
            "output": output,
        })

    def _step_title(self, key: str) -> str:
        titles = dict(PF_REVIEW_STEPS)
        return titles.get(key, key)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

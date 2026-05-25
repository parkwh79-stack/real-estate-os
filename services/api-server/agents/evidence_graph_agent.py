from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


EVIDENCE_GRAPH_SCHEMA_VERSION = "realytics-evidence-graph-v1"


SECTION_ARTIFACT_MAP = {
    "cover": ["project"],
    "financialTerms": ["pfStructure"],
    "claimPreservation": ["legalPermitReview", "riskAssessment", "riskRegister", "creditReviewDecision"],
    "discountTrigger": ["marketabilityAnalysis", "pfStructure"],
    "fundingStructure": ["developerFeasibility", "feasibility", "pfStructure"],
    "useOfFunds": ["developerFeasibility", "feasibility"],
    "landSecuring": ["landAgentReview"],
    "projectSchedule": ["developerFeasibility"],
    "financialStructureDiagram": ["pfStructure"],
    "salesPaymentSchedule": ["marketabilityAnalysis", "developerFeasibility"],
    "salesRevenueDistribution": ["scenariosEvaluation", "pfStructure"],
    "summaryCashFlow": ["scenariosEvaluation", "developerFeasibility"],
    "borrowerAnalysis": ["riskAssessment", "riskRegister", "creditReviewDecision"],
    "contractorAnalysis": ["riskAssessment", "riskRegister", "creditReviewDecision"],
    "marketAnalysis": ["marketEvidenceReview", "marketabilityAnalysis"],
    "businessFeasibility": ["developerFeasibility", "feasibility", "marketabilityAnalysis", "dataQualityReview"],
    "exitStrategy": ["marketabilityAnalysis", "scenariosEvaluation", "pfStructure"],
    "drawdownConditions": ["legalPermitReview", "landAgentReview", "pfStructure"],
    "eodTriggers": ["legalPermitReview", "riskAssessment", "riskRegister", "creditReviewDecision"],
    "fundExecutionOrder": ["pfStructure", "scenariosEvaluation"],
}


CLAIM_PATHS = [
    ("developerFeasibility", "developerReturns.profitMargin", "Developer profit margin"),
    ("developerFeasibility", "pfReadiness.status", "PF readiness status"),
    ("feasibility", "development_profit", "Calculated development profit"),
    ("feasibility", "profit_margin", "Calculated profit margin"),
    ("pfStructure", "ltv", "PF LTV"),
    ("pfStructure", "ltc", "PF LTC"),
    ("pfStructure", "exit_sale_rate", "Exit sale rate"),
    ("landAgentReview", "status", "Land readiness status"),
    ("landAgentReview", "summary.pnuCoverageRatio", "PNU coverage ratio"),
    ("landAgentReview", "summary.officialLandPriceCoverageRatio", "Official land price coverage ratio"),
    ("landAgentReview", "summary.registryEvidenceCoverageRatio", "Registry evidence coverage ratio"),
    ("marketabilityAnalysis", "score", "Marketability score"),
    ("marketabilityAnalysis", "grade", "Marketability grade"),
    ("marketabilityAnalysis", "pricing.judgement", "Pricing judgement"),
    ("reconciliationGate", "status", "Cross-agent reconciliation status"),
    ("dataQualityReview", "status", "Data quality review status"),
    ("riskRegister", "status", "Risk register status"),
    ("creditReviewDecision", "decisionLabel", "Credit review decision"),
    ("legalPermitReview", "status", "Legal and permit review status"),
    ("qualityCheck", "pass", "Orchestration quality gate pass"),
]


class EvidenceGraphAgent:
    """Build a deterministic claim/source graph for PF review auditability."""

    def build(self, project_data: Dict[str, Any], run: Dict[str, Any]) -> Dict[str, Any]:
        builder = _GraphBuilder()
        artifacts = run.get("artifacts", {})

        builder.add_node(
            "project:current",
            "project",
            "Current project input",
            {"projectId": run.get("project_id"), "project": project_data.get("project", {})},
        )

        for source in self._source_documents(project_data):
            source_id = source.get("archiveId") or source.get("originalFileHash") or source.get("sourceUrlOrEvidenceId")
            if not source_id:
                continue
            node_id = f"source:{source_id}"
            builder.add_node(node_id, "source_document", source.get("originalFileName") or str(source_id), source)
            builder.add_edge(node_id, "project:current", "loaded_into", "Source document is attached to project input.")

        for artifact_key, artifact in artifacts.items():
            if artifact_key in {"evidenceGraph"}:
                continue
            artifact_id = f"artifact:{artifact_key}"
            builder.add_node(
                artifact_id,
                "agent_artifact",
                artifact_key,
                {
                    "artifactKey": artifact_key,
                    "analysisType": artifact.get("analysisType") if isinstance(artifact, dict) else None,
                    "status": artifact.get("status") if isinstance(artifact, dict) else None,
                },
            )
            builder.add_edge("project:current", artifact_id, "produced", f"Project input produced {artifact_key}.")
            for evidence_id in self._collect_evidence_ids(artifact):
                evidence_node = f"evidence:{evidence_id}"
                builder.add_node(evidence_node, "evidence", evidence_id, {"evidenceId": evidence_id})
                builder.add_edge(evidence_node, artifact_id, "supports", f"Evidence {evidence_id} supports {artifact_key}.")

        for handoff in run.get("handoffs", []):
            handoff_id = handoff.get("handoffId")
            if not handoff_id:
                continue
            node_id = f"handoff:{handoff_id}"
            builder.add_node(
                node_id,
                "agent_handoff",
                handoff.get("handoffType", handoff_id),
                {
                    "handoffId": handoff_id,
                    "handoffType": handoff.get("handoffType"),
                    "fromAgent": handoff.get("fromAgent"),
                    "toAgent": handoff.get("toAgent"),
                    "status": handoff.get("status"),
                    "reviewStatus": handoff.get("reviewStatus"),
                    "requiresHumanReview": handoff.get("requiresHumanReview"),
                },
            )
            from_node = f"agent:{handoff.get('fromAgent')}"
            to_node = f"agent:{handoff.get('toAgent')}"
            builder.add_node(from_node, "agent", handoff.get("fromAgent", "Unknown agent"), {})
            builder.add_node(to_node, "agent", handoff.get("toAgent", "Unknown agent"), {})
            builder.add_edge(from_node, node_id, "created", "Upstream agent created handoff.")
            builder.add_edge(node_id, to_node, "sent_to", "Handoff was sent to downstream agent.")
            for evidence_id in handoff.get("evidenceIds", []):
                evidence_node = f"evidence:{evidence_id}"
                builder.add_node(evidence_node, "evidence", evidence_id, {"evidenceId": evidence_id})
                builder.add_edge(evidence_node, node_id, "supports", f"Evidence {evidence_id} supports handoff.")

        self._add_claim_nodes(builder, artifacts)
        self._add_risk_nodes(builder, artifacts)
        self._add_report_section_nodes(builder, artifacts)

        nodes = list(builder.nodes.values())
        edges = builder.edges
        section_nodes = [node for node in nodes if node["type"] == "report_section"]
        risk_nodes = [node for node in nodes if node["type"] == "risk"]
        risks_without_explicit_evidence = self._risks_without_explicit_evidence(artifacts)
        unlinked_sections = [
            node["metadata"]["sectionKey"]
            for node in section_nodes
            if not any(edge["to"] == node["id"] for edge in edges)
        ]
        return {
            "schemaVersion": EVIDENCE_GRAPH_SCHEMA_VERSION,
            "analysisType": "evidence_graph",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "projectId": run.get("project_id"),
            "runId": run.get("run_id"),
            "summary": {
                "nodeCount": len(nodes),
                "edgeCount": len(edges),
                "sourceDocumentCount": sum(1 for node in nodes if node["type"] == "source_document"),
                "evidenceCount": sum(1 for node in nodes if node["type"] == "evidence"),
                "claimCount": sum(1 for node in nodes if node["type"] == "claim"),
                "riskCount": len(risk_nodes),
                "reportSectionCount": len(section_nodes),
                "unlinkedReportSectionCount": len(unlinked_sections),
                "riskRowsWithoutExplicitEvidenceCount": len(risks_without_explicit_evidence),
            },
            "nodes": nodes,
            "edges": edges,
            "unlinkedReportSections": unlinked_sections,
            "auditWarnings": self._audit_warnings(unlinked_sections, nodes, risks_without_explicit_evidence),
        }

    def _add_claim_nodes(self, builder: "_GraphBuilder", artifacts: Dict[str, Any]) -> None:
        for artifact_key, path, label in CLAIM_PATHS:
            artifact = artifacts.get(artifact_key)
            if not isinstance(artifact, dict):
                continue
            value = self._get_path(artifact, path)
            if value is None:
                continue
            claim_id = f"claim:{artifact_key}:{path}"
            builder.add_node(
                claim_id,
                "claim",
                label,
                {
                    "artifactKey": artifact_key,
                    "path": path,
                    "value": value,
                },
            )
            builder.add_edge(f"artifact:{artifact_key}", claim_id, "derived_claim", f"{label} is derived from {artifact_key}.")

    def _add_risk_nodes(self, builder: "_GraphBuilder", artifacts: Dict[str, Any]) -> None:
        risk_register = artifacts.get("riskRegister") or {}
        risks = risk_register.get("risks") or []
        if not isinstance(risks, list):
            return
        for risk in risks:
            if not isinstance(risk, dict) or not risk.get("riskId"):
                continue
            risk_id = str(risk["riskId"])
            risk_node = f"risk:{risk_id}"
            source_artifact_key = self._artifact_base_key(risk.get("sourceArtifact"))
            source_artifact_node = f"artifact:{source_artifact_key}" if source_artifact_key in artifacts else None
            evidence_node_ids = self._risk_evidence_node_ids(builder, risk)
            source_document_node_ids = self._risk_source_document_node_ids(builder, risk)
            source_locator_node_ids = self._risk_source_locator_node_ids(builder, risk)
            claim_node_ids = self._claim_node_ids_for_artifact(builder, source_artifact_key)

            builder.add_node(
                risk_node,
                "risk",
                risk.get("title") or risk_id,
                {
                    "riskId": risk_id,
                    "code": risk.get("code"),
                    "category": risk.get("category"),
                    "severity": risk.get("severity"),
                    "probability": risk.get("probability"),
                    "grade": risk.get("grade"),
                    "score": risk.get("score"),
                    "sourceAgent": risk.get("sourceAgent"),
                    "sourceArtifact": risk.get("sourceArtifact"),
                    "reviewStatus": risk.get("reviewStatus"),
                },
            )

            if source_artifact_node:
                builder.add_edge(source_artifact_node, risk_node, "produced_risk", f"{source_artifact_key} produced risk {risk_id}.")
                builder.add_edge(risk_node, source_artifact_node, "derived_from_artifact", f"Risk {risk_id} is derived from {source_artifact_key}.")

            for evidence_node in evidence_node_ids:
                builder.add_edge(evidence_node, risk_node, "supports_risk", f"{evidence_node} supports risk {risk_id}.")
                builder.add_edge(risk_node, evidence_node, "supported_by_evidence", f"Risk {risk_id} is supported by {evidence_node}.")

            for source_document_node in source_document_node_ids:
                builder.add_edge(source_document_node, risk_node, "source_for_risk", f"{source_document_node} is a source document for risk {risk_id}.")
                builder.add_edge(risk_node, source_document_node, "derived_from_source_document", f"Risk {risk_id} is derived from {source_document_node}.")

            for source_locator_node in source_locator_node_ids:
                builder.add_edge(source_locator_node, risk_node, "locator_for_risk", f"{source_locator_node} locates source data for risk {risk_id}.")
                builder.add_edge(risk_node, source_locator_node, "derived_from_locator", f"Risk {risk_id} is derived from {source_locator_node}.")

            for claim_node in claim_node_ids:
                builder.add_edge(claim_node, risk_node, "supports_risk", f"{claim_node} supports risk {risk_id}.")
                builder.add_edge(risk_node, claim_node, "derived_from_claim", f"Risk {risk_id} is derived from {claim_node}.")

            risk["evidenceGraphLinks"] = {
                "riskNodeId": risk_node,
                "sourceArtifactNodeId": source_artifact_node,
                "evidenceNodeIds": evidence_node_ids,
                "claimNodeIds": claim_node_ids,
                "sourceDocumentNodeIds": source_document_node_ids,
                "sourceLocatorNodeIds": source_locator_node_ids,
            }

    def _add_report_section_nodes(self, builder: "_GraphBuilder", artifacts: Dict[str, Any]) -> None:
        report = artifacts.get("phase11Report", {}).get("report", {})
        sections = report.get("sections") or {}
        section_order = report.get("sectionOrder") or []
        title_by_key = {
            item.get("key"): item.get("title")
            for item in section_order
            if isinstance(item, dict)
        }
        for section_key in sections:
            section_id = f"report_section:{section_key}"
            builder.add_node(
                section_id,
                "report_section",
                title_by_key.get(section_key) or section_key,
                {"sectionKey": section_key},
            )
            for artifact_key in SECTION_ARTIFACT_MAP.get(section_key, []):
                if artifact_key == "project":
                    builder.add_edge("project:current", section_id, "used_in_report", f"Project input supports report section {section_key}.")
                elif artifact_key in artifacts:
                    builder.add_edge(f"artifact:{artifact_key}", section_id, "used_in_report", f"{artifact_key} supports report section {section_key}.")

    def _source_documents(self, project_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        documents = []
        meta_upload = project_data.get("_meta", {}).get("upload")
        if isinstance(meta_upload, dict):
            documents.append(meta_upload)
        source_documents = project_data.get("source_documents") or project_data.get("sourceDocuments") or []
        if isinstance(source_documents, list):
            documents.extend(item for item in source_documents if isinstance(item, dict))
        document_market_evidence = project_data.get("document_market_evidence") or {}
        source_document = document_market_evidence.get("sourceDocument") if isinstance(document_market_evidence, dict) else None
        if isinstance(source_document, dict):
            documents.append(source_document)
        return documents

    def _collect_evidence_ids(self, value: Any) -> List[str]:
        evidence_ids: List[str] = []

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                for key, item in node.items():
                    if key in {"evidenceId", "evidence_id", "sourceUrlOrEvidenceId"} and isinstance(item, str):
                        evidence_ids.append(item)
                    elif key in {"evidenceIds", "evidence_ids"} and isinstance(item, list):
                        evidence_ids.extend(str(entry) for entry in item if entry)
                    else:
                        visit(item)
            elif isinstance(node, list):
                for item in node:
                    visit(item)

        visit(value)
        return sorted(set(evidence_ids))

    def _risk_evidence_node_ids(self, builder: "_GraphBuilder", risk: Dict[str, Any]) -> List[str]:
        evidence_node_ids = []
        for evidence_id in risk.get("evidenceIds") or []:
            if not evidence_id:
                continue
            evidence_node = f"evidence:{evidence_id}"
            builder.add_node(evidence_node, "evidence", str(evidence_id), {"evidenceId": str(evidence_id)})
            evidence_node_ids.append(evidence_node)
        return sorted(set(evidence_node_ids))

    def _risk_source_document_node_ids(self, builder: "_GraphBuilder", risk: Dict[str, Any]) -> List[str]:
        source_ids = {str(item) for item in risk.get("sourceDocumentIds") or [] if item}
        source_ids.update(str(item) for item in risk.get("evidenceIds") or [] if item)
        node_ids = []
        for node_id, node in builder.nodes.items():
            if node.get("type") != "source_document":
                continue
            metadata = node.get("metadata") or {}
            candidates = {
                metadata.get("archiveId"),
                metadata.get("originalFileHash"),
                metadata.get("sourceUrlOrEvidenceId"),
                metadata.get("evidenceId"),
            }
            if source_ids.intersection(str(item) for item in candidates if item):
                node_ids.append(node_id)
        return sorted(set(node_ids))

    def _risk_source_locator_node_ids(self, builder: "_GraphBuilder", risk: Dict[str, Any]) -> List[str]:
        locator_node_ids = []
        for locator in risk.get("sourceLocators") or []:
            if not isinstance(locator, dict):
                continue
            locator_id = locator.get("locatorId") or locator.get("source") or locator.get("path")
            if not locator_id:
                continue
            node_id = f"locator:{risk.get('riskId')}:{self._safe_id(str(locator_id))}"
            builder.add_node(node_id, "source_locator", locator.get("label") or str(locator_id), locator)
            locator_node_ids.append(node_id)
        return sorted(set(locator_node_ids))

    def _claim_node_ids_for_artifact(self, builder: "_GraphBuilder", artifact_key: str | None) -> List[str]:
        if not artifact_key:
            return []
        return sorted(
            node_id
            for node_id, node in builder.nodes.items()
            if node.get("type") == "claim"
            and (node.get("metadata") or {}).get("artifactKey") == artifact_key
        )

    def _artifact_base_key(self, source_artifact: Any) -> str | None:
        if not source_artifact:
            return None
        return str(source_artifact).split(".", 1)[0]

    def _risks_without_explicit_evidence(self, artifacts: Dict[str, Any]) -> List[str]:
        risk_register = artifacts.get("riskRegister") or {}
        risks = risk_register.get("risks") or []
        if not isinstance(risks, list):
            return []
        missing = []
        for risk in risks:
            if not isinstance(risk, dict):
                continue
            if not risk.get("evidenceIds") and not risk.get("sourceLocators") and not risk.get("sourceDocumentIds"):
                missing.append(str(risk.get("riskId") or risk.get("code") or "unknown"))
        return missing

    def _safe_id(self, value: str) -> str:
        cleaned = []
        for char in value:
            if char.isalnum() or char in {"-", "_", ".", ":"}:
                cleaned.append(char)
            else:
                cleaned.append("_")
        return "".join(cleaned).strip("_") or "locator"

    def _get_path(self, value: Dict[str, Any], path: str) -> Any:
        current: Any = value
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        if isinstance(current, (str, int, float, bool)) or current is None:
            return current
        return str(current)

    def _audit_warnings(
        self,
        unlinked_sections: List[str],
        nodes: List[Dict[str, Any]],
        risks_without_explicit_evidence: List[str],
    ) -> List[str]:
        warnings = []
        if unlinked_sections:
            warnings.append("Some report sections do not have mapped supporting artifacts.")
        if not any(node["type"] == "source_document" for node in nodes):
            warnings.append("No source document node is available; graph relies on project and agent artifacts.")
        if not any(node["type"] == "evidence" for node in nodes):
            warnings.append("No explicit evidence ID node is available; attach source evidence for audit-grade traceability.")
        if risks_without_explicit_evidence:
            warnings.append("Some risk rows only link to agent artifacts/claims and do not have explicit evidence IDs or source locators.")
        return warnings


class _GraphBuilder:
    def __init__(self) -> None:
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: List[Dict[str, Any]] = []
        self.edge_keys: set[tuple[str, str, str]] = set()

    def add_node(self, node_id: str, node_type: str, label: str, metadata: Dict[str, Any]) -> None:
        if not node_id or node_id in self.nodes:
            return
        self.nodes[node_id] = {
            "id": node_id,
            "type": node_type,
            "label": label,
            "metadata": metadata,
        }

    def add_edge(self, source: str, target: str, relation: str, explanation: str) -> None:
        if not source or not target:
            return
        edge_key = (source, target, relation)
        if edge_key in self.edge_keys:
            return
        self.edge_keys.add(edge_key)
        self.edges.append({
            "from": source,
            "to": target,
            "relation": relation,
            "explanation": explanation,
        })

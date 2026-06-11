from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


DATA_QUALITY_SCHEMA_VERSION = "realytics-data-quality-v1"
SQM_TO_PY = 0.3025


class DataQualityAgent:
    """Detect cross-document and cross-agent numeric conflicts before credit review."""

    def review(
        self,
        project_data: Dict[str, Any],
        artifacts: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        artifacts = artifacts or {}
        checks = [
            self._project_name_check(project_data),
            self._land_area_check(project_data, artifacts),
            self._gfa_check(project_data),
            self._sales_revenue_check(project_data, artifacts),
            self._pf_amount_check(project_data, artifacts),
            self._unit_count_check(project_data),
            self._area_conversion_check(project_data),
            self._vat_consistency_check(project_data),
            self._date_schedule_check(project_data),
            self._tranche_total_check(project_data),
        ]
        checks = [check for check in checks if check is not None]
        checks, applied_overrides = self._apply_approved_overrides(
            checks,
            project_data.get("approvedAssumptionOverrides")
            or project_data.get("approved_assumption_overrides")
            or [],
        )
        status = self._status(checks)
        return {
            "schemaVersion": DATA_QUALITY_SCHEMA_VERSION,
            "analysisType": "data_quality_review",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "summary": {
                "checkCount": len(checks),
                "clearCount": sum(1 for check in checks if check["status"] == "clear"),
                "overriddenCount": sum(1 for check in checks if check["status"] == "overridden"),
                "warningCount": sum(1 for check in checks if check["status"] == "warning"),
                "blockingCount": sum(1 for check in checks if check["status"] == "blocking"),
                "missingCount": sum(1 for check in checks if check["status"] == "missing_data"),
            },
            "checks": checks,
            "appliedOverrides": applied_overrides,
            "blockingIssues": [
                check["message"]
                for check in checks
                if check["status"] == "blocking"
            ],
            "dataLimitations": [
                check["message"]
                for check in checks
                if check["status"] in {"warning", "missing_data"}
            ],
            "nextActions": self._next_actions(checks),
        }

    def _project_name_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        names = []
        project_name = (data.get("project") or {}).get("name")
        if project_name:
            names.append(("project.name", project_name))
        for source in self._source_documents(data):
            extracted_name = source.get("projectName") or source.get("project_name")
            if extracted_name:
                names.append(("sourceDocument.projectName", extracted_name))
        unique = {str(value).strip().lower() for _, value in names if value}
        if len(unique) <= 1:
            return self._clear("project_name_consistency", "Project name consistency", names)
        return self._warning(
            "project_name_consistency",
            "Project name consistency",
            "Project name differs across source records.",
            names,
        )

    def _land_area_check(self, data: Dict[str, Any], artifacts: Dict[str, Any]) -> Dict[str, Any]:
        values = []
        project_land_area = self._project_land_area(data)
        if project_land_area is not None:
            values.append(("land.area_sqm", project_land_area))
        architecture_land_area = self._first_number(data.get("architectural_plan") or {}, ["land_area_sqm", "landAreaSqm", "siteAreaSqm"])
        if architecture_land_area is not None:
            values.append(("architectural_plan.land_area_sqm", architecture_land_area))
        ledger_area = ((data.get("landLedger") or {}).get("summary") or {}).get("totalAreaSqm")
        if isinstance(ledger_area, (int, float)):
            values.append(("landLedger.summary.totalAreaSqm", ledger_area))
        artifact_area = ((artifacts.get("landAgentReview") or {}).get("summary") or {}).get("totalAreaSqm")
        if isinstance(artifact_area, (int, float)):
            values.append(("landAgentReview.summary.totalAreaSqm", artifact_area))
        return self._numeric_consistency(
            key="land_area_consistency",
            label="Land area consistency",
            values=values,
            unit="sqm",
            warning_threshold=0.01,
            blocking_threshold=0.03,
            missing_message="Land area has fewer than two comparable sources.",
        )

    def _gfa_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        values = []
        architectural_plan = data.get("architectural_plan") or {}
        program = data.get("program") or {}
        for source, container in [
            ("architectural_plan.gross_floor_area_sqm", architectural_plan),
            ("program.grossFloorAreaSqm", program),
        ]:
            value = self._first_number(container, ["gross_floor_area_sqm", "grossFloorAreaSqm", "gfaSqm"])
            if value is not None:
                values.append((source, value))
        return self._numeric_consistency(
            key="gross_floor_area_consistency",
            label="Gross floor area consistency",
            values=values,
            unit="sqm",
            warning_threshold=0.01,
            blocking_threshold=0.03,
            missing_message="GFA has fewer than two comparable sources.",
        )

    def _sales_revenue_check(self, data: Dict[str, Any], artifacts: Dict[str, Any]) -> Dict[str, Any]:
        values = []
        feasibility_revenue = (data.get("feasibility_input") or {}).get("total_revenue")
        if isinstance(feasibility_revenue, (int, float)):
            values.append(("feasibility_input.total_revenue", feasibility_revenue))
        developer_revenue = ((artifacts.get("developerFeasibility") or {}).get("revenue") or {}).get("totalRevenue")
        if isinstance(developer_revenue, (int, float)):
            values.append(("developerFeasibility.revenue.totalRevenue", developer_revenue))
        market_revenue = self._marketability_revenue(artifacts.get("marketabilityAnalysis") or {})
        if isinstance(market_revenue, (int, float)):
            values.append(("marketability.totalSalesRevenue", market_revenue))
        product_mix_total = self._product_mix_total(data)
        if isinstance(product_mix_total, (int, float)) and product_mix_total > 0:
            values.append(("product_mix.totalRevenue.sum", product_mix_total))
        document_mix_total = self._document_product_mix_total(data)
        if isinstance(document_mix_total, (int, float)) and document_mix_total > 0:
            values.append(("document_market_evidence.productMix.totalRevenue.sum", document_mix_total))
        return self._numeric_consistency(
            key="sales_revenue_consistency",
            label="Total sales revenue consistency",
            values=values,
            unit="KRW",
            warning_threshold=0.03,
            blocking_threshold=0.10,
            missing_message="Sales revenue has fewer than two comparable sources.",
        )

    def _pf_amount_check(self, data: Dict[str, Any], artifacts: Dict[str, Any]) -> Dict[str, Any]:
        values = []
        tranche_sum = self._pf_amount(data)
        if tranche_sum:
            values.append(("pf_structure.amount.sum", tranche_sum))
        expected_pf = (data.get("expected_results") or {}).get("total_pf_amount")
        if isinstance(expected_pf, (int, float)):
            values.append(("expected_results.total_pf_amount", expected_pf))
        developer_pf = ((artifacts.get("developerFeasibility") or {}).get("pfReadiness") or {}).get("totalPfAmount")
        if isinstance(developer_pf, (int, float)):
            values.append(("developerFeasibility.pfReadiness.totalPfAmount", developer_pf))
        report_pf = (((artifacts.get("phase11Report") or {}).get("report") or {}).get("sections") or {}).get("financialTerms", {}).get("totalAmount")
        if isinstance(report_pf, (int, float)):
            values.append(("phase11Report.financialTerms.totalAmount", report_pf))
        return self._numeric_consistency(
            key="pf_amount_consistency",
            label="PF amount consistency",
            values=values,
            unit="KRW",
            warning_threshold=0.01,
            blocking_threshold=0.03,
            missing_message="PF amount has fewer than two comparable sources.",
        )

    def _unit_count_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        values = []
        architecture_units = self._first_number(data.get("architectural_plan") or {}, ["unit_count", "unitCount", "units"])
        if architecture_units is not None:
            values.append(("architectural_plan.unit_count", architecture_units))
        product_units = self._product_mix_units(data)
        if product_units:
            values.append(("product_mix.units.sum", product_units))
        document_units = self._document_product_mix_units(data)
        if document_units:
            values.append(("document_market_evidence.productMix.units.sum", document_units))
        return self._numeric_consistency(
            key="unit_count_consistency",
            label="Unit count consistency",
            values=values,
            unit="units",
            warning_threshold=0.01,
            blocking_threshold=0.05,
            missing_message="Unit count has fewer than two comparable sources.",
        )

    def _area_conversion_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        issues = []
        for source_name, item in self._iter_product_mix_items(data):
            for sqm_key, py_key in [("contractAreaSqm", "contractAreaPy"), ("exclusiveAreaSqm", "exclusiveAreaPy"), ("areaSqm", "areaPy")]:
                sqm = self._to_float(item.get(sqm_key) or item.get(self._snake(sqm_key)))
                py = self._to_float(item.get(py_key) or item.get(self._snake(py_key)))
                if sqm is None or py is None:
                    continue
                expected_py = sqm * SQM_TO_PY
                if abs(py - expected_py) > 0.5:
                    issues.append({
                        "source": source_name,
                        "sqmField": sqm_key,
                        "pyField": py_key,
                        "sqm": sqm,
                        "py": py,
                        "expectedPy": expected_py,
                        "diff": py - expected_py,
                    })
        if issues:
            return self._blocking(
                "sqm_py_conversion_consistency",
                "SQM/PY conversion consistency",
                "At least one product mix area has inconsistent sqm to py conversion.",
                issues,
            )
        return self._clear("sqm_py_conversion_consistency", "SQM/PY conversion consistency", [])

    def _vat_consistency_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        vat_values = []
        for source_name, item in self._iter_product_mix_items(data):
            if "vatIncluded" in item:
                vat_values.append((f"{source_name}.vatIncluded", bool(item.get("vatIncluded"))))
            elif "vat_included" in item:
                vat_values.append((f"{source_name}.vat_included", bool(item.get("vat_included"))))
        revenue_before = (data.get("feasibility_input") or {}).get("total_revenue")
        revenue_after_vat = (data.get("feasibility_input") or {}).get("total_revenue_after_vat")
        if isinstance(revenue_before, (int, float)) and isinstance(revenue_after_vat, (int, float)) and revenue_before != revenue_after_vat:
            vat_values.append(("feasibility_input.total_revenue_after_vat_differs", True))
        if not vat_values:
            return self._warning(
                "vat_basis_consistency",
                "VAT basis consistency",
                "VAT included/excluded basis is not explicit in product mix or feasibility input.",
                [],
            )
        unique = {value for _, value in vat_values}
        if len(unique) > 1:
            return self._warning(
                "vat_basis_consistency",
                "VAT basis consistency",
                "VAT basis appears mixed or inconsistent across revenue records.",
                vat_values,
            )
        return self._clear("vat_basis_consistency", "VAT basis consistency", vat_values)

    def _date_schedule_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        schedule = data.get("schedule") or {}
        ordered_keys = ["land_purchase", "permit_approval", "construction_start", "presale_start", "completion"]
        values = [(key, schedule.get(key)) for key in ordered_keys if schedule.get(key)]
        if len(values) < 2:
            return self._missing("schedule_sequence_consistency", "Schedule sequence consistency", "Schedule has fewer than two comparable dates.", values)
        # YYYY-MM strings sort safely for the current MVP date format.
        invalid = []
        previous_key, previous_value = values[0]
        for key, value in values[1:]:
            if str(value) < str(previous_value):
                invalid.append({"previous": previous_key, "previousValue": previous_value, "current": key, "currentValue": value})
            previous_key, previous_value = key, value
        if invalid:
            return self._warning("schedule_sequence_consistency", "Schedule sequence consistency", "Project schedule dates appear out of order.", invalid)
        return self._clear("schedule_sequence_consistency", "Schedule sequence consistency", values)

    def _tranche_total_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        tranches = [item for item in data.get("pf_structure", []) if isinstance(item, dict)]
        missing = [item for item in tranches if not isinstance(item.get("amount"), (int, float))]
        duplicate_names = self._duplicates([str(item.get("tranche") or item.get("name") or "") for item in tranches if item.get("tranche") or item.get("name")])
        if missing or duplicate_names:
            return self._warning(
                "tranche_structure_quality",
                "Tranche structure quality",
                "PF tranche structure has missing amounts or duplicate tranche names.",
                {"missingAmountRows": missing, "duplicateNames": duplicate_names},
            )
        return self._clear("tranche_structure_quality", "Tranche structure quality", {"trancheCount": len(tranches)})

    def _numeric_consistency(
        self,
        *,
        key: str,
        label: str,
        values: List[tuple[str, float]],
        unit: str,
        warning_threshold: float,
        blocking_threshold: float,
        missing_message: str,
    ) -> Dict[str, Any]:
        values = [(source, float(value)) for source, value in values if isinstance(value, (int, float))]
        if len(values) < 2:
            return self._missing(key, label, missing_message, values)
        numeric_values = [value for _, value in values]
        max_value = max(numeric_values)
        min_value = min(numeric_values)
        denominator = max(abs(max_value), 1.0)
        diff_ratio = abs(max_value - min_value) / denominator
        metadata = {
            "values": [{"source": source, "value": value} for source, value in values],
            "unit": unit,
            "minValue": min_value,
            "maxValue": max_value,
            "diff": max_value - min_value,
            "diffRatio": diff_ratio,
            "warningThreshold": warning_threshold,
            "blockingThreshold": blocking_threshold,
        }
        if diff_ratio >= blocking_threshold:
            return self._blocking(key, label, f"{label} differs by {diff_ratio:.2%}, exceeding blocking tolerance.", metadata)
        if diff_ratio >= warning_threshold:
            return self._warning(key, label, f"{label} differs by {diff_ratio:.2%}, exceeding warning tolerance.", metadata)
        return self._clear(key, label, metadata)

    def _status(self, checks: List[Dict[str, Any]]) -> str:
        if any(check["status"] == "blocking" for check in checks):
            return "blocking"
        if any(check["status"] in {"warning", "missing_data"} for check in checks):
            return "needs_review"
        return "clear"

    def _apply_approved_overrides(
        self,
        checks: List[Dict[str, Any]],
        overrides: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        if not isinstance(overrides, list) or not overrides:
            return checks, []
        active_overrides = {
            str(item.get("checkKey")): item
            for item in overrides
            if isinstance(item, dict)
            and item.get("status", "active") == "active"
            and item.get("effectiveStatus", item.get("status", "active")) == "active"
            and item.get("checkKey")
        }
        if not active_overrides:
            return checks, []

        applied: List[Dict[str, Any]] = []
        updated_checks: List[Dict[str, Any]] = []
        for check in checks:
            override = active_overrides.get(str(check.get("key")))
            if not override or check.get("status") not in {"blocking", "warning", "missing_data"}:
                updated_checks.append(check)
                continue
            override_summary = {
                "overrideId": override.get("overrideId"),
                "checkKey": override.get("checkKey"),
                "canonicalField": override.get("canonicalField"),
                "approvedValue": override.get("approvedValue"),
                "unit": override.get("unit"),
                "approvedBy": override.get("approvedBy"),
                "approvedAt": override.get("approvedAt"),
                "reason": override.get("reason"),
                "evidenceIds": override.get("evidenceIds") or [],
                "sourceLocators": override.get("sourceLocators") or [],
            }
            resolved_check = {
                **check,
                "originalStatus": check.get("status"),
                "originalSeverity": check.get("severity"),
                "status": "overridden",
                "severity": "clear",
                "message": (
                    f"{check.get('label')} was resolved by approved assumption override "
                    f"{override.get('overrideId')}."
                ),
                "approvedAssumptionOverride": override_summary,
            }
            metadata = resolved_check.get("metadata")
            if isinstance(metadata, dict):
                resolved_check["metadata"] = {**metadata, "approvedAssumptionOverride": override_summary}
            applied.append(override_summary)
            updated_checks.append(resolved_check)
        return updated_checks, applied

    def _next_actions(self, checks: List[Dict[str, Any]]) -> List[str]:
        if any(check["status"] == "blocking" for check in checks):
            return [
                "Resolve blocking data quality conflicts before final PF approval.",
                "Select an authoritative source or approve an explicit assumption override.",
            ]
        if any(check["status"] in {"warning", "missing_data"} for check in checks):
            return [
                "Review data quality warnings and keep accepted assumptions visible in the report.",
                "Attach missing document, Excel, or API evidence for unresolved numeric fields.",
            ]
        return ["No data quality action required."]

    def _clear(self, key: str, label: str, metadata: Any) -> Dict[str, Any]:
        return {"key": key, "label": label, "category": self._category(key), "status": "clear", "severity": "clear", "message": f"{label} passed.", "metadata": metadata}

    def _warning(self, key: str, label: str, message: str, metadata: Any) -> Dict[str, Any]:
        return {"key": key, "label": label, "category": self._category(key), "status": "warning", "severity": "medium", "message": message, "metadata": metadata}

    def _blocking(self, key: str, label: str, message: str, metadata: Any) -> Dict[str, Any]:
        return {"key": key, "label": label, "category": self._category(key), "status": "blocking", "severity": "high", "message": message, "metadata": metadata}

    def _missing(self, key: str, label: str, message: str, metadata: Any) -> Dict[str, Any]:
        return {"key": key, "label": label, "category": self._category(key), "status": "missing_data", "severity": "low", "message": message, "metadata": metadata}

    def _category(self, key: str) -> str:
        if "land" in key:
            return "land"
        if "revenue" in key or "vat" in key:
            return "revenue"
        if "pf" in key or "tranche" in key:
            return "pf_structure"
        if "area" in key or "unit" in key:
            return "program"
        if "schedule" in key:
            return "schedule"
        return "general"

    def _project_land_area(self, data: Dict[str, Any]) -> float | None:
        land = data.get("land") or {}
        return self._first_number(land, ["area_sqm", "land_area_sqm", "landAreaSqm", "siteAreaSqm"])

    def _marketability_revenue(self, marketability: Dict[str, Any]) -> float | None:
        product_mix = marketability.get("productMix")
        if isinstance(product_mix, dict):
            value = product_mix.get("totalSalesRevenue")
            if isinstance(value, (int, float)):
                return float(value)
        project_summary = marketability.get("projectSummary") or {}
        value = project_summary.get("totalSalesRevenue")
        return float(value) if isinstance(value, (int, float)) else None

    def _product_mix_total(self, data: Dict[str, Any]) -> float:
        return sum(self._to_float(item.get("totalRevenue") or item.get("total_revenue")) or 0 for item in self._product_mix(data))

    def _product_mix_units(self, data: Dict[str, Any]) -> float:
        return sum(self._to_float(item.get("units") or item.get("unit_count")) or 0 for item in self._product_mix(data))

    def _document_product_mix_total(self, data: Dict[str, Any]) -> float:
        return sum(self._to_float(item.get("totalRevenue") or item.get("total_revenue")) or 0 for item in self._document_product_mix(data))

    def _document_product_mix_units(self, data: Dict[str, Any]) -> float:
        return sum(self._to_float(item.get("units") or item.get("unit_count")) or 0 for item in self._document_product_mix(data))

    def _pf_amount(self, data: Dict[str, Any]) -> float:
        return sum(self._to_float(item.get("amount")) or 0 for item in data.get("pf_structure", []) if isinstance(item, dict))

    def _iter_product_mix_items(self, data: Dict[str, Any]) -> List[tuple[str, Dict[str, Any]]]:
        items = [(f"product_mix[{index}]", item) for index, item in enumerate(self._product_mix(data))]
        items.extend((f"document_market_evidence.productMix[{index}]", item) for index, item in enumerate(self._document_product_mix(data)))
        return items

    def _product_mix(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw = data.get("product_mix") or data.get("productMix") or []
        return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    def _document_product_mix(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        document_evidence = data.get("document_market_evidence") or data.get("market_evidence") or {}
        if not isinstance(document_evidence, dict):
            return []
        raw = document_evidence.get("productMix") or document_evidence.get("product_mix") or []
        return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    def _source_documents(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        documents = []
        upload = data.get("_meta", {}).get("upload")
        if isinstance(upload, dict):
            documents.append(upload)
        source_documents = data.get("source_documents") or data.get("sourceDocuments") or []
        if isinstance(source_documents, list):
            documents.extend(item for item in source_documents if isinstance(item, dict))
        return documents

    def _first_number(self, container: Dict[str, Any], keys: List[str]) -> float | None:
        for key in keys:
            value = self._to_float(container.get(key))
            if value is not None:
                return value
        return None

    def _to_float(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).replace(",", "").strip())
        except ValueError:
            return None

    def _snake(self, name: str) -> str:
        result = ""
        for char in name:
            if char.isupper():
                result += "_" + char.lower()
            else:
                result += char
        return result.lstrip("_")

    def _duplicates(self, values: List[str]) -> List[str]:
        seen = set()
        duplicates = set()
        for value in values:
            if value in seen:
                duplicates.add(value)
            seen.add(value)
        return sorted(duplicates)

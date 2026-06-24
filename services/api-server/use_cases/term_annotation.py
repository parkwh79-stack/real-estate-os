from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from agents.source_attribution import attribute_source
from agents.term_annotation_agent import TermAnnotationAgent
from use_cases.glossary import capture_glossary_candidates_use_case, list_glossary_terms_use_case


def _resolve_value(data: Any, path: Optional[str]) -> Any:
    """Resolve a dotted/indexed path (e.g. pf_structure[0].tranche) in project_data."""
    if not path or data is None:
        return None
    node = data
    for token in re.findall(r"[^.\[\]]+", str(path)):
        if isinstance(node, dict):
            node = node.get(token)
        elif isinstance(node, list):
            try:
                node = node[int(token)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if node is None:
            return None
    return node if not isinstance(node, (dict, list)) else None


def _enrich_annotation(annotation: Dict[str, Any], project_data: Dict[str, Any], project_id: Optional[str]) -> Dict[str, Any]:
    attribution = attribute_source(
        annotation.get("canonicalField"),
        annotation.get("sourceField"),
        annotation.get("sourceLocator"),
        project_id=project_id,
    )
    value = _resolve_value(project_data, annotation.get("canonicalField")) or _resolve_value(
        project_data, annotation.get("sourceField")
    )
    annotation["sourceLabel"] = attribution["sourceLabel"]
    annotation["evidenceRef"] = attribution["evidenceRef"]
    annotation["sourceUrl"] = attribution["sourceUrl"]
    annotation["evidenceValue"] = value
    return annotation


def annotate_terms_use_case(
    project_data: Dict[str, Any],
    glossary_terms: Optional[List[Dict[str, Any]]] = None,
    *,
    project_id: str | None = None,
    run_id: str | None = None,
    persist_candidates: bool = True,
) -> Dict[str, Any]:
    """Attach termId + canonicalField + sourceLocator to extracted terms.

    Usable from the API, the PF review orchestration, or batch jobs. When
    glossary_terms is not supplied it loads the approved glossary vocabulary.
    """
    terms = glossary_terms if glossary_terms is not None else list_glossary_terms_use_case()["terms"]
    result = TermAnnotationAgent().annotate(project_data, terms)
    for annotation in result.get("annotations") or []:
        _enrich_annotation(annotation, project_data, project_id)
    if persist_candidates:
        result["candidateCapture"] = capture_glossary_candidates_use_case(
            result.get("annotations") or [],
            project_id=project_id,
            run_id=run_id,
        )
    return result

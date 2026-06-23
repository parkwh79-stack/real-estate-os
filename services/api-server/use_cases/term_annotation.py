from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents.term_annotation_agent import TermAnnotationAgent
from use_cases.glossary import capture_glossary_candidates_use_case, list_glossary_terms_use_case


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
    if persist_candidates:
        result["candidateCapture"] = capture_glossary_candidates_use_case(
            result.get("annotations") or [],
            project_id=project_id,
            run_id=run_id,
        )
    return result

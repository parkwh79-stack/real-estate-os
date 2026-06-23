from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents.term_annotation_agent import TermAnnotationAgent
from use_cases.glossary import list_glossary_terms_use_case


def annotate_terms_use_case(
    project_data: Dict[str, Any],
    glossary_terms: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Attach termId + canonicalField + sourceLocator to extracted terms.

    Usable from the API, the PF review orchestration, or batch jobs. When
    glossary_terms is not supplied it loads the approved glossary vocabulary.
    """
    terms = glossary_terms if glossary_terms is not None else list_glossary_terms_use_case()["terms"]
    return TermAnnotationAgent().annotate(project_data, terms)

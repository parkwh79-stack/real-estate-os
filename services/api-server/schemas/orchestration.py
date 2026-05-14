PF_REVIEW_WORKFLOW = "pf-review"

PF_REVIEW_STEPS = [
    ("project_load", "Project data load"),
    ("development_feasibility_analysis", "Developer feasibility analysis"),
    ("feasibility_calculation", "Feasibility calculation"),
    ("pf_structure_calculation", "PF structure calculation"),
    ("market_evidence_reconciliation", "Market evidence reconciliation"),
    ("marketability_analysis", "Marketability analysis"),
    ("legal_permit_review", "Legal and permit pre-review"),
    ("risk_assessment", "Risk assessment"),
    ("phase11_report_generation", "Phase 11 report generation"),
    ("quality_check", "Quality check"),
]

TERMINAL_RUN_STATUSES = {"completed", "failed", "approved"}

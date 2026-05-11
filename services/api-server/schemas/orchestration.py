PF_REVIEW_WORKFLOW = "pf-review"

PF_REVIEW_STEPS = [
    ("project_load", "프로젝트 데이터 로드"),
    ("feasibility_calculation", "사업성 계산"),
    ("pf_structure_calculation", "PF 구조 계산"),
    ("marketability_analysis", "분양성 검증"),
    ("legal_permit_review", "법률/인허가 예비 검토"),
    ("risk_assessment", "리스크 평가"),
    ("phase11_report_generation", "Phase 11 보고서 생성"),
    ("quality_check", "품질 점검"),
]

TERMINAL_RUN_STATUSES = {"completed", "failed", "approved"}

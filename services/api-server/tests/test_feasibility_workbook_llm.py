from types import SimpleNamespace

import openpyxl
import pytest

from use_cases.feasibility_workbook_llm import (
    FeasibilityReviewConflict,
    apply_feasibility_input_use_case,
    feasibility_review_state,
    dump_workbook_cells,
    extract_feasibility_fields_via_llm,
)


def _make_workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "손익"
    ws["A1"] = "매출액 합계"
    ws["I1"] = 853951767.7  # 천원
    ws["A2"] = "총사업비"
    ws["I2"] = 779350253.5
    wb.save(str(path))


class _FakeAI:
    def __init__(self, data):
        self._data = data

    def extract_json(self, prompt, system=None, ai_context=None):
        return SimpleNamespace(data=self._data, error=None, meta={"provider": "fake"})


class _FakeRepo:
    def __init__(self, project):
        self.project = project

    def get(self, pid):
        return self.project

    def update(self, pid, payload):
        # shallow-merge feasibility_input like the real deep_merge for these keys
        fi = {**(self.project.get("feasibility_input") or {}), **(payload.get("feasibility_input") or {})}
        self.project = {**self.project, **payload, "feasibility_input": fi}
        return self.project


def test_dump_workbook_cells_includes_sheet_and_coords(tmp_path):
    p = tmp_path / "wb.xlsx"
    _make_workbook(p)
    dump = dump_workbook_cells(str(p))
    assert "### SHEET: 손익" in dump
    assert "손익!I1=853951767.7" in dump


def test_dump_workbook_cells_keeps_content_past_legacy_24000_character_limit(tmp_path):
    p = tmp_path / "large.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Large"
    for row in range(1, 400):
        sheet.cell(row, 1, f"row-{row}-" + ("x" * 80))
    sheet["B400"] = "TAIL_CELL_MUST_SURVIVE"
    workbook.save(str(p))

    dump = dump_workbook_cells(str(p))

    assert len(dump) > 24_000
    assert "Large!B400=TAIL_CELL_MUST_SURVIVE" in dump
    assert "truncated" not in dump


def test_extract_builds_candidate_and_drops_nulls(tmp_path):
    p = tmp_path / "wb.xlsx"
    _make_workbook(p)
    fake = _FakeAI(
        {
            "fields": {
                "total_revenue": {"value_krw": 853951767700, "source": "손익!I1", "unit": "천원", "raw": 853951767.7},
                "total_cost": {"value_krw": 779350253500, "source": "손익!I2", "unit": "천원", "raw": 779350253.5},
                "land_cost": {"value_krw": None, "source": None, "unit": None, "raw": None},
            }
        }
    )
    draft = extract_feasibility_fields_via_llm(str(p), {}, ai=fake)
    assert draft["parsingSource"] == "llm"
    assert draft["reviewRequired"] is True
    # evidence preserved for found fields
    assert draft["fields"]["total_revenue"]["source"] == "손익!I1"
    # candidate excludes null fields (non-fabricating)
    assert draft["feasibilityInputCandidate"] == {
        "total_revenue": 853951767700,
        "total_cost": 779350253500,
    }
    assert "land_cost" not in draft["feasibilityInputCandidate"]


def test_extract_handles_unavailable_llm(tmp_path):
    p = tmp_path / "wb.xlsx"
    _make_workbook(p)

    class _NoData:
        def extract_json(self, prompt, system=None, ai_context=None):
            return SimpleNamespace(data=None, error="quota exceeded", meta={})

    draft = extract_feasibility_fields_via_llm(str(p), {}, ai=_NoData())
    assert draft["parsingSource"] == "llm_unavailable"
    assert draft["feasibilityInputCandidate"] == {}


def test_chunked_workbook_extraction_flags_conflicting_values(tmp_path):
    p = tmp_path / "chunked.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Large"
    for row in range(1, 40):
        sheet.cell(row, 1, f"row-{row}-" + ("x" * 40))
    workbook.save(str(p))

    class ConflictingAI:
        def __init__(self):
            self.calls = 0

        def extract_json(self, prompt, system=None, ai_context=None):
            self.calls += 1
            value = 100 if self.calls == 1 else 200
            return SimpleNamespace(
                data={
                    "fields": {
                        "total_revenue": {
                            "value_krw": value,
                            "source": f"Large!A{self.calls}",
                            "unit": "KRW",
                            "raw": value,
                        }
                    }
                },
                error=None,
                meta={"provider": "fake"},
            )

    fake = ConflictingAI()
    draft = extract_feasibility_fields_via_llm(
        str(p),
        {},
        ai=fake,
        chunk_chars=256,
    )

    assert fake.calls > 1
    assert draft["inputCoverage"] == "complete"
    assert draft["analysisOnly"] is True
    assert draft["reportEligible"] is False
    assert draft["fields"]["total_revenue"]["status"] == "conflict"
    assert "total_revenue" not in draft["feasibilityInputCandidate"]
    assert draft["sourceConflicts"][0]["fieldKey"] == "total_revenue"


def test_apply_persists_cleaned_input():
    repo = _FakeRepo({"feasibility_input": {"total_revenue": 1, "stale": 9}})
    result = apply_feasibility_input_use_case(
        repo,
        "585",
        {"total_revenue": 853951767700, "total_cost": 779350253500, "bogus": "x"},
        applied_by="reviewer",
    )
    fi = result["feasibility_input"]
    assert fi["total_revenue"] == 853951767700
    assert fi["total_cost"] == 779350253500
    assert "bogus" not in fi  # non-numeric dropped


def test_apply_rejects_empty():
    repo = _FakeRepo({"feasibility_input": {}})
    with pytest.raises(ValueError):
        apply_feasibility_input_use_case(repo, "585", {"x": "not-a-number"}, applied_by="reviewer")


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1, 1.5, "100", 10**18, 10**400])
def test_invalid_review_money_does_not_mutate_project(value):
    repo = _FakeRepo({"feasibility_input": {"total_revenue": 101}})
    with pytest.raises(ValueError):
        apply_feasibility_input_use_case(repo, "585", {"total_revenue": value}, applied_by="reviewer")
    assert repo.project == {"feasibility_input": {"total_revenue": 101}}


def test_review_preserves_assumption_and_rejects_stale_browser():
    from use_cases.feasibility_input_builder import build_feasibility_input_draft

    repo = _FakeRepo({"feasibility_input": {"total_revenue": 101}, "_meta": {"tenantId": "tenant-1"}})
    original_hash = feasibility_review_state(repo.project)["reviewHash"]
    result = apply_feasibility_input_use_case(
        repo, "585", {"finance_cost": 123456789}, applied_by="reviewer",
        field_reviews={"finance_cost": {"origin": "user_assumption", "reason": "임시 금융비용 가정"}},
        expected_review_hash=original_hash,
    )
    assert result["reviewState"]["values"]["finance_cost"] == 123456789
    review = result["reviewState"]["fieldReviews"]["finance_cost"]
    assert review["needsReview"] is True
    assert review["approvalStatus"] == "not_requested"
    assert review["sourceDocument"] is None
    assert repo.project["_meta"]["tenantId"] == "tenant-1"
    draft = build_feasibility_input_draft(repo.project)
    assert draft["feasibilityInput"]["finance_cost"] == 123456789
    assert draft["fieldSources"]["finance_cost"]["source"] == "user_assumption"
    assert draft["fieldSources"]["finance_cost"]["needsReview"] is True
    with pytest.raises(FeasibilityReviewConflict):
        apply_feasibility_input_use_case(repo, "585", {"finance_cost": 2}, applied_by="other", expected_review_hash=original_hash)
    assert repo.project["feasibility_input"]["finance_cost"] == 123456789


def test_opening_workspace_does_not_invalidate_the_current_review():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    project_id = client.post("/projects/", json={"project": {"name": "Workspace read regression"}}).json()["id"]
    root = f"/projects/{project_id}"
    review_before = client.get(root + "/feasibility-workbook/review").json()
    context = client.get(root + "/workspace/context?page=input_package")
    assert context.status_code == 200
    assert context.json()["dealStateGraph"]["updatedBy"] == "workspace_context"
    response = client.post(root + "/feasibility-workbook/apply-input", json={
        "feasibility_input": {"total_revenue": 123},
        "expectedReviewHash": review_before["reviewHash"],
        "fieldReviews": {"total_revenue": {"origin": "user_assumption", "reason": "Fixture budget"}},
    })
    assert response.status_code == 200, response.text
    assert response.json()["reviewState"]["values"]["total_revenue"] == 123
    assert client.get(root + "/feasibility-workbook/review").json()["reviewHash"] != review_before["reviewHash"]


def test_review_preserves_zero_and_negative_document_profit():
    repo = _FakeRepo({"feasibility_input": {"total_revenue": 100}, "expected_results": {"development_profit": 10, "profit_margin": 0.1}})
    result = apply_feasibility_input_use_case(repo, "585", {"total_revenue": 0, "development_profit": -10}, applied_by="reviewer")
    assert result["feasibility_input"]["total_revenue"] == 0
    assert result["expected_results"]["development_profit"] == -10
    assert result["expected_results"]["profit_margin"] is None

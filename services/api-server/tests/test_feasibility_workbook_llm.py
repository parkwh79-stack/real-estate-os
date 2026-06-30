from types import SimpleNamespace

import openpyxl
import pytest

from use_cases.feasibility_workbook_llm import (
    apply_feasibility_input_use_case,
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

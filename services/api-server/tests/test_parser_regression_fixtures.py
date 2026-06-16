import json
from pathlib import Path

from agents.parser_agent import ParserAgent
from agents.parser_provenance import attach_parser_field_provenance


FIXTURE_PATH = Path(__file__).parents[1] / "data" / "fixtures" / "parser_regression_cases.json"


def test_parser_regression_fixture_cases_are_anonymized_and_source_located():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert fixture["schemaVersion"] == "realytics-parser-regression-fixtures-v1"
    assert fixture["fixtureMeta"]["anonymized"] is True
    assert len(fixture["cases"]) >= 2

    for case in fixture["cases"]:
        expected = dict(case["expectedProjectData"])
        result = attach_parser_field_provenance(
            expected,
            upload_meta={"originalFileName": f"{case['caseId']}.txt"},
            parser_version="parser-regression-fixture",
        )
        located = result["parser_field_source_locators"]
        for path in case["requiredSourceLocatorPaths"]:
            assert path in located, f"{case['caseId']} missing locator for {path}"
        assert result["parser_locator_coverage"]["locatedFieldCount"] >= len(case["requiredSourceLocatorPaths"])


def test_parser_prompt_contains_non_market_source_locator_contract():
    prompt = ParserAgent().build_extraction_prompt("Anonymized text")

    assert "source_locators" in prompt
    assert "land, regulation, architectural, sales, cost, feasibility, PF, schedule, covenant" in prompt
    assert "If a value is inferred and no document location exists" in prompt

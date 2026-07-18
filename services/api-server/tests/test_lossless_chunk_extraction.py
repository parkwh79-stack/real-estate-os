from types import SimpleNamespace

import openpyxl
import pytest

from agents.parser_agent import ParserAgent, _merge_chunk_extractions
from core.text_chunking import chunk_text_preserving_context
from use_cases.feasibility_workbook_llm import extract_feasibility_fields_via_llm


def test_chunking_preserves_every_source_character_and_line_ending():
    text = ("first\r\nsecond\nthird\r\n" * 20) + "final\r\n"

    chunks = chunk_text_preserving_context(text, max_chars=256)

    assert all(len(chunk) <= 256 for chunk in chunks)
    assert "".join(chunks) == text


def test_chunking_repeats_context_without_dropping_crlf_source():
    marker = "--- PAGE 1 ---"
    synthetic_prefix = f"{marker}\n"
    text = f"{marker}\r\n" + ("x" * 700) + "\r\nTAIL\r\n"

    chunks = chunk_text_preserving_context(
        text,
        max_chars=256,
        is_context_line=lambda line: line == marker,
    )
    source_only_chunks = [
        chunk.removeprefix(synthetic_prefix) if index else chunk
        for index, chunk in enumerate(chunks)
    ]

    assert len(chunks) > 1
    assert all(len(chunk) <= 256 for chunk in chunks)
    assert all(chunk.startswith(synthetic_prefix) for chunk in chunks[1:])
    assert "".join(source_only_chunks) == text


def test_nested_scalar_conflict_preserves_all_chunk_provenance():
    merged, conflicts = _merge_chunk_extractions(
        [
            (0, {"project": {"name": "Alpha"}}, {}),
            (1, {"project": {"name": "Alpha"}}, {}),
            (2, {"project": {"name": "Beta"}}, {}),
        ]
    )

    assert merged["project"]["name"] is None
    assert conflicts[0]["fieldPath"] == "project.name"
    assert [
        (candidate["chunkIndex"], candidate["value"])
        for candidate in conflicts[0]["candidates"]
    ] == [(0, "Alpha"), (1, "Alpha"), (2, "Beta")]


def test_product_mix_entities_merge_recursively_and_preserve_provenance():
    base_item = {
        "type": "84A",
        "units": 100,
        "pricePerPy": 20_000_000,
        "evidenceIds": ["EV-1"],
        "sourceLocator": {"pageNumber": 10, "confidence": 0.9},
    }
    merged, conflicts = _merge_chunk_extractions(
        [
            (0, {"sales_assumptions": {"product_mix": [base_item]}}, {}),
            (
                1,
                {
                    "sales_assumptions": {
                        "product_mix": [
                            {
                                **base_item,
                                "evidenceIds": ["EV-2"],
                            }
                        ]
                    }
                },
                {},
            ),
            (
                2,
                {
                    "sales_assumptions": {
                        "product_mix": [
                            {
                                **base_item,
                                "pricePerPy": 21_000_000,
                                "evidenceIds": ["EV-3"],
                                "sourceLocator": {
                                    "pageNumber": 10,
                                    "confidence": 0.8,
                                },
                            }
                        ]
                    }
                },
                {},
            ),
        ]
    )

    items = merged["sales_assumptions"]["product_mix"]
    assert len(items) == 1
    assert items[0]["pricePerPy"] is None
    assert items[0]["sourceLocator"]["confidence"] is None
    assert items[0]["evidenceIds"] == ["EV-1", "EV-2", "EV-3"]
    price_conflict = next(
        conflict for conflict in conflicts if conflict["fieldPath"].endswith(".pricePerPy")
    )
    assert [
        candidate["chunkIndex"] for candidate in price_conflict["candidates"]
    ] == [0, 1, 2]


def test_document_market_product_mix_uses_type_identity():
    merged, conflicts = _merge_chunk_extractions(
        [
            (
                0,
                {
                    "document_market_evidence": {
                        "productMix": [{"type": "59A", "totalRevenue": 100}]
                    }
                },
                {},
            ),
            (
                1,
                {
                    "document_market_evidence": {
                        "productMix": [{"type": "59A", "totalRevenue": 120}]
                    }
                },
                {},
            ),
        ]
    )

    assert merged["document_market_evidence"]["productMix"] == [
        {"type": "59A", "totalRevenue": None}
    ]
    assert conflicts[0]["fieldPath"] == (
        'document_market_evidence.productMix[type="59A"].totalRevenue'
    )


@pytest.mark.parametrize(
    ("parent_key", "array_key"),
    [
        ("market", "comparables"),
        ("market", "rentComparables"),
        ("document_market_evidence", "comparables"),
        ("document_market_evidence", "manualComparables"),
        ("document_market_evidence", "transactionComparables"),
        ("document_market_evidence", "presaleComparables"),
        ("document_market_evidence", "listingComparables"),
        ("document_market_evidence", "rentComparables"),
    ],
)
def test_case_id_registry_merges_only_matching_comparable_entity(
    parent_key,
    array_key,
):
    merged, conflicts = _merge_chunk_extractions(
        [
            (
                0,
                {
                    parent_key: {
                        array_key: [
                            {"caseId": "COMP-1", "pricePerPy": 10},
                            {"caseId": "COMP-2", "pricePerPy": 30},
                        ]
                    }
                },
                {},
            ),
            (
                1,
                {
                    parent_key: {
                        array_key: [
                            {"caseId": "COMP-1", "pricePerPy": 20},
                            {"caseId": "COMP-3", "pricePerPy": 40},
                        ]
                    }
                },
                {},
            ),
        ]
    )

    items = merged[parent_key][array_key]
    assert [item["caseId"] for item in items] == ["COMP-1", "COMP-2", "COMP-3"]
    assert items[0]["pricePerPy"] is None
    assert len(conflicts) == 1
    assert conflicts[0]["fieldPath"].endswith(
        f'{array_key}[caseId="COMP-1"].pricePerPy'
    )


def test_pf_tranche_registry_merges_matching_tranche_only():
    merged, conflicts = _merge_chunk_extractions(
        [
            (
                0,
                {
                    "pf_structure": [
                        {"tranche": "senior", "amount": 100, "interest_rate": 0.05}
                    ]
                },
                {},
            ),
            (
                1,
                {
                    "pf_structure": [
                        {"tranche": "senior", "amount": 110, "fee_rate": 0.01},
                        {"tranche": "mezzanine", "amount": 20},
                    ]
                },
                {},
            ),
        ]
    )

    assert len(merged["pf_structure"]) == 2
    assert merged["pf_structure"][0] == {
        "tranche": "senior",
        "amount": None,
        "interest_rate": 0.05,
        "fee_rate": 0.01,
    }
    assert conflicts[0]["fieldPath"] == 'pf_structure[tranche="senior"].amount'


def test_duplicate_identity_inside_one_chunk_is_not_left_as_two_entities():
    merged, conflicts = _merge_chunk_extractions(
        [
            (
                0,
                {
                    "pf_structure": [
                        {"tranche": "senior", "amount": 100},
                        {"tranche": "senior", "amount": 110},
                    ]
                },
                {},
            )
        ]
    )

    assert merged["pf_structure"] == [{"tranche": "senior", "amount": None}]
    assert conflicts[0]["fieldPath"] == 'pf_structure[tranche="senior"].amount'
    assert [item["value"] for item in conflicts[0]["candidates"]] == [100, 110]


def test_data_source_identity_uses_url_first_and_name_only_as_fallback():
    merged, conflicts = _merge_chunk_extractions(
        [
            (
                0,
                {
                    "market": {
                        "data_sources": [
                            {
                                "sourceUrlOrEvidenceId": "URL-A",
                                "sourceName": "Registry A",
                            },
                            {"sourceName": "Name Only", "baseDate": "2025-01-01"},
                            {
                                "sourceUrlOrEvidenceId": "URL-X",
                                "sourceName": "Shared Name",
                            },
                        ]
                    }
                },
                {},
            ),
            (
                1,
                {
                    "market": {
                        "data_sources": [
                            {
                                "sourceUrlOrEvidenceId": "URL-A",
                                "sourceName": "Registry A Revised",
                            },
                            {"sourceName": "Name Only", "baseDate": "2025-02-01"},
                            {
                                "sourceUrlOrEvidenceId": "URL-Y",
                                "sourceName": "Shared Name",
                            },
                        ]
                    }
                },
                {},
            ),
        ]
    )

    items = merged["market"]["data_sources"]
    assert len(items) == 4
    assert items[0]["sourceName"] is None
    assert items[1]["baseDate"] is None
    assert {item.get("sourceUrlOrEvidenceId") for item in items[2:]} == {
        "URL-X",
        "URL-Y",
    }
    assert {
        conflict["fieldPath"] for conflict in conflicts
    } == {
        'market.data_sources[sourceName="Name Only"].baseDate',
        'market.data_sources[sourceUrlOrEvidenceId="URL-A"].sourceName',
    }


def test_identityless_array_keeps_exact_union_without_guessed_merge():
    merged, conflicts = _merge_chunk_extractions(
        [
            (0, {"custom": {"items": [{"name": "same", "value": 1}]}}, {}),
            (1, {"custom": {"items": [{"name": "same", "value": 2}]}}, {}),
        ]
    )

    assert merged["custom"]["items"] == [
        {"name": "same", "value": 1},
        {"name": "same", "value": 2},
    ]
    assert conflicts == []


def test_parser_empty_success_is_analysis_only_and_not_report_eligible():
    class EmptyAI:
        def extract_json(self, prompt, system=None, ai_context=None):
            return SimpleNamespace(data={}, error=None, meta={"provider": "fake"})

    parser = ParserAgent({"provider": "groq", "api_key": "test-only"})
    parser.ai = EmptyAI()

    result = parser.extract_project_data("No supported facts")

    assert result["_meta"]["extractionEmpty"] is True
    assert result["_meta"]["analysisOnly"] is True
    assert result["_meta"]["reportEligible"] is False


def test_parser_partial_chunk_failure_keeps_draft_but_blocks_reporting():
    class PartiallyAvailableAI:
        def __init__(self):
            self.calls = 0

        def extract_json(self, prompt, system=None, ai_context=None):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    data={"project": {"name": "Partial Project"}},
                    error=None,
                    meta={"provider": "fake"},
                )
            return SimpleNamespace(data=None, error="chunk unavailable", meta={})

    parser = ParserAgent({"provider": "groq", "api_key": "test-only"})
    parser.ai = PartiallyAvailableAI()

    result = parser.extract_project_data("x" * 30_100)

    assert result["project"]["name"] == "Partial Project"
    assert result["_meta"]["inputCoverage"] == "partial"
    assert result["_meta"]["completedChunkCount"] == 1
    assert result["_meta"]["failedChunks"]
    assert result["_meta"]["analysisOnly"] is True
    assert result["_meta"]["reportEligible"] is False


def test_parser_entity_conflict_blocks_report_eligibility():
    class ConflictingEntityAI:
        def __init__(self):
            self.calls = 0

        def extract_json(self, prompt, system=None, ai_context=None):
            self.calls += 1
            return SimpleNamespace(
                data={
                    "pf_structure": [
                        {
                            "tranche": "senior",
                            "amount": 100 if self.calls == 1 else 110,
                        }
                    ]
                },
                error=None,
                meta={"provider": "fake"},
            )

    parser = ParserAgent({"provider": "groq", "api_key": "test-only"})
    parser.ai = ConflictingEntityAI()

    result = parser.extract_project_data("x" * 30_100)

    assert len(result["pf_structure"]) == 1
    assert result["pf_structure"][0]["amount"] is None
    assert result["_meta"]["extractionConflicts"][0]["fieldPath"] == (
        'pf_structure[tranche="senior"].amount'
    )
    assert result["_meta"]["analysisOnly"] is True
    assert result["_meta"]["reportEligible"] is False


def test_parser_unavailable_and_host_handoff_are_not_report_eligible(monkeypatch):
    class UnavailableAI:
        def extract_json(self, prompt, system=None, ai_context=None):
            return SimpleNamespace(data=None, error="unavailable", meta={})

    parser = ParserAgent({"provider": "groq", "api_key": "test-only"})
    parser.ai = UnavailableAI()
    unavailable = parser.extract_project_data("No extraction")
    host_handoff = ParserAgent(
        {"provider": "claude_desktop", "host": "claude_desktop"}
    ).extract_project_data("Host must extract this")

    assert unavailable["_meta"]["analysisOnly"] is True
    assert unavailable["_meta"]["reportEligible"] is False
    assert host_handoff["_meta"]["analysisOnly"] is True
    assert host_handoff["_meta"]["reportEligible"] is False


def test_workbook_empty_success_is_analysis_only_and_not_report_eligible(tmp_path):
    path = tmp_path / "empty-result.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active["A1"] = "No recognized feasibility total"
    workbook.save(path)

    class EmptyAI:
        def extract_json(self, prompt, system=None, ai_context=None):
            return SimpleNamespace(
                data={"fields": {}},
                error=None,
                meta={"provider": "fake"},
            )

    result = extract_feasibility_fields_via_llm(str(path), {}, ai=EmptyAI())

    assert result["extractionEmpty"] is True
    assert result["analysisOnly"] is True
    assert result["reportEligible"] is False


def test_workbook_partial_chunk_failure_keeps_candidate_but_blocks_reporting(tmp_path):
    path = tmp_path / "partial-result.xlsx"
    workbook = openpyxl.Workbook()
    for row in range(1, 40):
        workbook.active.cell(row, 1, f"row-{row}-" + ("x" * 40))
    workbook.save(path)

    class PartiallyAvailableAI:
        def __init__(self):
            self.calls = 0

        def extract_json(self, prompt, system=None, ai_context=None):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    data={
                        "fields": {
                            "total_revenue": {
                                "value_krw": 100,
                                "source": "Sheet!A1",
                                "unit": "KRW",
                                "raw": 100,
                            }
                        }
                    },
                    error=None,
                    meta={"provider": "fake"},
                )
            return SimpleNamespace(data=None, error="chunk unavailable", meta={})

    result = extract_feasibility_fields_via_llm(
        str(path),
        {},
        ai=PartiallyAvailableAI(),
        chunk_chars=256,
    )

    assert result["feasibilityInputCandidate"]["total_revenue"] == 100
    assert result["inputCoverage"] == "partial"
    assert result["failedChunks"]
    assert result["analysisOnly"] is True
    assert result["reportEligible"] is False

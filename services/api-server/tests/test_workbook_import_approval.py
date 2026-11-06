from use_cases.workbook_import_approval import _feasibility_input_from_study


def test_feasibility_input_counts_mezzanine_as_pf_debt():
    result = _feasibility_input_from_study({
        "version": "feasibility-study-v2",
        "metrics": {},
        "financing": [
            {"priority": "senior", "amount": 100},
            {"priority": "mezzanine", "amount": 40},
            {"priority": "equity", "amount": 20},
        ],
    })

    assert result["pf_amount"] == 140

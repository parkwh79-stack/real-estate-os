from agents.agent_policy import system_prompt_for, _load_sections, reload_policies


def test_policy_sections_parse():
    reload_policies()
    sections = _load_sections()
    assert "common" in sections
    assert "parser" in sections
    assert "feasibility_workbook" in sections


def test_common_invariants_present():
    prompt = system_prompt_for("feasibility_workbook")
    # Core AGENTS.md invariants must be injected.
    assert "NO fabrication" in prompt or "fabrication" in prompt
    assert "KRW" in prompt
    # Agent-specific guidance appended.
    assert "total_revenue" in prompt


def test_base_is_prepended():
    prompt = system_prompt_for("parser", base="BASE-MARKER")
    assert prompt.startswith("BASE-MARKER")
    assert "coordinator/extractor" in prompt or "coordinator" in prompt


def test_unknown_agent_falls_back_to_common_plus_base():
    prompt = system_prompt_for("does_not_exist", base="ONLY-BASE")
    assert "ONLY-BASE" in prompt
    # common still included
    assert "REALYTICS" in prompt

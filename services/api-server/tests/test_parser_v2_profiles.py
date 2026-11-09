from parsing_platform.default_modules import build_default_intake_orchestrator, intake_core_profile
from use_cases.parser_v2_profiles import (
    ProjectParsingProfileConflict,
    get_project_parsing_profile,
    list_project_parsing_profiles,
    save_project_parsing_profile,
)


class MemoryRecords:
    def __init__(self):
        self.records = {}

    def upsert(self, project_id, record):
        self.records[(project_id, record["recordId"])] = dict(record)
        return dict(record)

    def list(self, project_id, *, record_type=None, limit=1000, **_kwargs):
        rows = [
            dict(record)
            for (stored_project_id, _), record in self.records.items()
            if stored_project_id == project_id
            and (record_type is None or record.get("recordType") == record_type)
        ]
        return rows[:limit]


def test_project_profile_is_versioned_immutable_and_resolves_execution_plan():
    memory = MemoryRecords()
    orchestrator = build_default_intake_orchestrator()
    profile = intake_core_profile(project_id="project-1")

    first = save_project_parsing_profile(
        "project-1",
        "tenant-1",
        "admin-1",
        profile,
        orchestrator,
        upsert_fn=memory.upsert,
        list_fn=memory.list,
    )
    repeated = save_project_parsing_profile(
        "project-1",
        "tenant-1",
        "admin-1",
        profile,
        orchestrator,
        upsert_fn=memory.upsert,
        list_fn=memory.list,
    )

    assert repeated == first
    assert first["profileHash"]
    assert len(first["executionPlan"]["stages"]) == 3
    assert len(list_project_parsing_profiles("project-1", "tenant-1", list_fn=memory.list)) == 1
    loaded = get_project_parsing_profile(
        "project-1", "tenant-1", "realytics.intake_core", "1.0.0", list_fn=memory.list
    )
    assert loaded.project_id == "project-1"


def test_same_profile_version_cannot_be_changed_or_cross_tenant_reused():
    memory = MemoryRecords()
    orchestrator = build_default_intake_orchestrator()
    profile = intake_core_profile(project_id="project-1")
    save_project_parsing_profile(
        "project-1", "tenant-1", "admin-1", profile, orchestrator,
        upsert_fn=memory.upsert, list_fn=memory.list,
    )

    try:
        save_project_parsing_profile(
            "project-1", "tenant-2", "admin-2", profile, orchestrator,
            upsert_fn=memory.upsert, list_fn=memory.list,
        )
    except ProjectParsingProfileConflict:
        pass
    else:
        raise AssertionError("An immutable project profile version cannot cross tenant boundaries")

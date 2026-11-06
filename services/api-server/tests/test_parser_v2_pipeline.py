import hashlib

import fitz

from parsing_platform.adapters import ArchivedDocument
from parsing_platform.default_modules import build_default_intake_orchestrator, intake_core_profile
from use_cases.parser_v2_pipeline import run_structural_parsing_use_case
from use_cases.parser_v2_records import ParserV2RecordStore


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


def _pdf_document() -> ArchivedDocument:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "사업계획서 프로젝트 알파")
    content = pdf.tobytes()
    pdf.close()
    return ArchivedDocument(
        tenant_id="tenant-1",
        project_id="project-1",
        archive_id=hashlib.sha256(content).hexdigest(),
        filename="사업계획서.pdf",
        content_type="application/pdf",
        content=content,
    )


def test_structural_pipeline_persists_append_only_lifecycle_without_raw_text_or_project_merge():
    memory = MemoryRecords()
    store = ParserV2RecordStore(upsert_fn=memory.upsert, list_fn=memory.list)

    result = run_structural_parsing_use_case(
        document=_pdf_document(),
        actor_id="analyst-1",
        profile=intake_core_profile(project_id="project-1"),
        orchestrator=build_default_intake_orchestrator(),
        record_store=store,
        parsing_run_id="parsing-run:test-1",
    )

    assert result["candidateOnly"] is True
    assert result["authoritative"] is False
    assert result["parsingRun"]["executionStatus"] == "completed"
    assert result["parsingRun"]["approvalStatus"] == "not_requested"
    assert result["orchestration"]["rawDocumentTextPersisted"] is False
    chunks = result["orchestration"]["results"]["structural_extraction"]["output"]["artifact"]["chunks"]
    assert "text" not in chunks[0]
    assert chunks[0]["textSha256"]

    run_records = [
        item for item in memory.list("project-1", record_type="parser_v2_parsing_run")
        if item.get("parsingRunId") == "parsing-run:test-1"
    ]
    assert len(run_records) == 3  # creation, running event, completed event


def test_same_run_id_is_idempotently_replayed_without_reexecuting_terminal_run():
    memory = MemoryRecords()
    store = ParserV2RecordStore(upsert_fn=memory.upsert, list_fn=memory.list)
    kwargs = {
        "document": _pdf_document(),
        "actor_id": "analyst-1",
        "profile": intake_core_profile(project_id="project-1"),
        "orchestrator": build_default_intake_orchestrator(),
        "record_store": store,
        "parsing_run_id": "parsing-run:idempotent",
    }

    first = run_structural_parsing_use_case(**kwargs)
    second = run_structural_parsing_use_case(**kwargs)

    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["orchestration"]["rawDocumentTextPersisted"] is False
    assert second["orchestration"] == first["orchestration"]
    assert len(memory.records) == 4  # document version + run + two lifecycle events


def test_default_idempotency_key_reuses_run_even_when_caller_omits_run_id():
    memory = MemoryRecords()
    store = ParserV2RecordStore(upsert_fn=memory.upsert, list_fn=memory.list)
    kwargs = {
        "document": _pdf_document(),
        "actor_id": "analyst-1",
        "profile": intake_core_profile(project_id="project-1"),
        "orchestrator": build_default_intake_orchestrator(),
        "record_store": store,
    }

    first = run_structural_parsing_use_case(**kwargs)
    second = run_structural_parsing_use_case(**kwargs)

    assert second["replayed"] is True
    assert second["parsingRun"]["parsingRunId"] == first["parsingRun"]["parsingRunId"]
    assert len(memory.records) == 4


def test_archive_hash_mismatch_fails_before_any_record_is_written():
    valid = _pdf_document()
    mismatched = ArchivedDocument(
        tenant_id=valid.tenant_id,
        project_id=valid.project_id,
        archive_id="f" * 64,
        filename=valid.filename,
        content_type=valid.content_type,
        content=valid.content,
    )
    memory = MemoryRecords()
    store = ParserV2RecordStore(upsert_fn=memory.upsert, list_fn=memory.list)

    import pytest

    with pytest.raises(ValueError, match="no Parser V2 records"):
        run_structural_parsing_use_case(
            document=mismatched,
            actor_id="analyst-1",
            profile=intake_core_profile(project_id="project-1"),
            orchestrator=build_default_intake_orchestrator(),
            record_store=store,
        )

    assert memory.records == {}

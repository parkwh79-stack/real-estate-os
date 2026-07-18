import fitz

from db.repositories.upload_archive_repository import UploadArchiveRepository
from use_cases.address_mining import mine_addresses_from_text, mine_project_address


class _RemoteObjectStorage:
    backend = "s3"

    def __init__(self):
        self.objects = {}

    def put(self, key, content, *, content_type=None):
        from db.object_storage import sha256_bytes

        self.objects[key] = bytes(content)
        return {"key": key, "size": len(content), "sha256": sha256_bytes(content)}

    def get(self, key):
        return self.objects[key]

    def exists(self, key):
        return key in self.objects

    def delete_prefix(self, prefix):
        return False

    def local_path(self, key):
        return None


def test_mines_no_space_cover_address():
    text = "사업개요\n위치 : 경기도용인시기흥구상갈동13번지일대\n면적 80,721㎡"
    hits = mine_addresses_from_text(text)
    assert hits[0]["address"] == "경기도 용인시 기흥구 상갈동 13"


def test_mines_spaced_address_with_san_and_subjibun():
    text = "소재지: 대구광역시 달서구 본동 산 743-2번지 일원"
    hits = mine_addresses_from_text(text)
    assert hits[0]["address"] == "대구광역시 달서구 본동 산743-2"


def test_no_address_returns_empty_not_invented():
    assert mine_addresses_from_text("총사업비 1,500억원, 세대수 1,048세대") == []
    assert mine_addresses_from_text("") == []


def test_mine_project_address_reads_archived_pdf(tmp_path, monkeypatch):
    # Writing Hangul into a synthetic PDF is font-dependent, so stub the text
    # layer; this still exercises the archive-path/provenance plumbing.
    import use_cases.address_mining as mod

    project_dir = tmp_path / "77"
    project_dir.mkdir()
    doc = fitz.open()
    doc.new_page()
    doc.save(str(project_dir / "abc123.pdf"))
    doc.close()
    monkeypatch.setattr(mod, "_pdf_text", lambda path: "위치: 경기도용인시기흥구상갈동13번지일대")

    project = {
        "source_documents": [
            {"documentType": "business_plan", "archiveId": "abc123", "originalFileName": "plan.pdf"}
        ]
    }
    mined = mine_project_address("77", project, archive_root=tmp_path)
    assert mined is not None
    assert mined["address"] == "경기도 용인시 기흥구 상갈동 13"
    assert mined["provenance"]["documentFileName"] == "plan.pdf"


def test_mine_project_address_missing_archive_returns_none(tmp_path):
    assert mine_project_address("999", {"source_documents": []}, archive_root=tmp_path) is None


def test_mine_project_address_reads_remote_object_storage(tmp_path, monkeypatch):
    import use_cases.address_mining as mod

    object_storage = _RemoteObjectStorage()
    repository = UploadArchiveRepository(api_server_root=tmp_path, object_storage=object_storage)
    archive = repository.archive_upload("p-remote", b"pdf-bytes", "원본사업계획서.pdf", "application/pdf")
    monkeypatch.setattr(
        mod,
        "_pdf_text_bytes",
        lambda content: "위치: 경기도용인시기흥구상갈동13번지일대",
    )
    project = {
        "source_documents": [
            {
                **archive,
                "documentType": "business_plan",
                "originalFileName": "원본사업계획서.pdf",
            }
        ]
    }

    mined = mine_project_address(
        "p-remote",
        project,
        upload_archive_repository=repository,
    )

    assert mined is not None
    assert mined["address"] == "경기도 용인시 기흥구 상갈동 13"
    assert mined["provenance"]["documentFileName"] == "원본사업계획서.pdf"

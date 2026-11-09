import ast
import os
import pytest
from pathlib import Path

from core.runtime_paths import runtime_data_dir
from db.repositories.upload_archive_repository import UploadArchiveRepository
from storage import DEFAULT_SQLITE_FILE, DEFAULT_STATE_FILE, sqlite_file_path


def test_explicit_live_sqlite_override_is_rejected_before_open(monkeypatch):
    import storage
    production = Path(__file__).resolve().parents[1] / "data" / "runtime" / "realytics.sqlite3"
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(production))
    with pytest.raises(RuntimeError, match="must not open"):
        storage._connect_sqlite()


def test_collection_uses_disposable_runtime_before_app_import():
    root = runtime_data_dir()
    production = Path(__file__).resolve().parents[1] / "data" / "runtime"
    assert root != production
    assert root.name.startswith("realytics-pytest-")
    assert DEFAULT_SQLITE_FILE.parent == root
    assert DEFAULT_STATE_FILE.parent == root
    assert sqlite_file_path().parent == root
    assert os.environ["REALYTICS_LOAD_LOCAL_ENV"] == "false"
    assert not os.environ.get("REALYTICS_DATABASE_URL")


def test_env_loader_does_not_read_developer_file_when_disabled():
    path = Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    loader = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_load_local_env")

    class ForbiddenPath:
        def __init__(self, *_args):
            raise AssertionError("Developer .env must not be accessed")

    namespace = {"os": os, "Path": ForbiddenPath, "__file__": str(path)}
    exec(compile(ast.Module(body=[loader], type_ignores=[]), str(path), "exec"), namespace)
    namespace["_load_local_env"]()


def test_default_upload_bytes_and_download_stay_in_test_runtime():
    archive = UploadArchiveRepository()
    assert archive.archive_root == runtime_data_dir() / "uploads"
    metadata = archive.archive_upload("isolation", b"test-only", "test.txt", "text/plain")
    metadata["originalFileName"] = "test.txt"
    resolved = archive.resolve_download_metadata("isolation", metadata)
    assert resolved is not None
    assert resolved["path"].is_relative_to(runtime_data_dir())
    assert resolved["path"].read_bytes() == b"test-only"

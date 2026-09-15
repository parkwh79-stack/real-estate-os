"""Standalone safety tests: no application imports, live DB, or cloud calls."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "cleanup-generated.ps1"
SHELL = shutil.which("pwsh") or shutil.which("powershell")


@unittest.skipUnless(SHELL and shutil.which("git"), "PowerShell and git required")
class CleanupSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project with spaces"
        self.root.mkdir()
        subprocess.run(["git", "init", str(self.root)], check=True, capture_output=True)
        self.make("AGENTS.md")

    def make(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
        return path

    def age(self):
        old = time.time() - 30 * 86400
        for path in sorted(self.root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if not path.is_symlink():
                os.utime(path, (old, old))

    def run_cleanup(self, apply=False, project_root=None):
        args = [SHELL, "-NoProfile", "-File", str(SCRIPT), "-ProjectRoot", str(project_root or self.root)]
        if apply:
            args.append("-Apply")
        result = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    @unittest.skipUnless(os.name == "nt", "Windows short path contract")
    def test_short_windows_root_keeps_containment_checks(self):
        import ctypes
        output = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetShortPathNameW(str(self.root), output, len(output))
        self.assertGreater(length, 0)
        removable = self.make(".pytest_cache/cache")
        original = self.make("services/api-server/data/runtime/uploads/original.pdf")
        self.age()
        self.assertIn("Candidate", self.run_cleanup(project_root=output.value).stdout)
        self.assertTrue(removable.exists())
        self.run_cleanup(apply=True, project_root=output.value)
        self.assertFalse(removable.exists())
        self.assertTrue(original.exists())

    def test_dry_run_does_not_delete(self):
        cache = self.make(".pytest_cache/cache")
        self.age()
        self.assertIn("Candidate", self.run_cleanup().stdout)
        self.assertTrue(cache.exists())

    def test_only_generated_files_removed(self):
        removable = [self.make(name) for name in (
            ".pytest_cache/cache", "services/api-server/.pytest-old/data",
            ".runtime/pytest-tmp/run/artifact", ".runtime/final-regression-0.sqlite3",
            "services/api-server/data/runtime/pytest-orch-tail-0718.sqlite3",
        )]
        protected = [self.make(name) for name in (
            "services/api-server/data/runtime/realytics.sqlite3",
            "services/api-server/data/runtime/uploads/original.pdf",
            "services/api-server/data/runtime/uploads.deleted-full-reset/original.pdf",
            "services/api-server/data/runtime/realytics.sqlite3.bak-full-reset",
            "services/api-server/tests/test_core.py", ".env",
            "node_modules/.pytest_cache/dependency", "apps/web/.next/server/file",
        )]
        self.age()
        self.run_cleanup(apply=True)
        self.assertTrue(all(not p.exists() for p in removable))
        self.assertTrue(all(p.exists() for p in protected))

    def test_tracked_recent_and_wal_files_preserved(self):
        tracked = self.make(".pytest_cache/tracked")
        subprocess.run(["git", "-C", str(self.root), "add", ".pytest_cache/tracked"], check=True)
        database = self.make(".runtime/final-regression-0.sqlite3")
        self.make(".runtime/final-regression-0.sqlite3-wal")
        self.age()
        recent = self.make(".turbo/recent")
        self.run_cleanup(apply=True)
        self.assertTrue(all(p.exists() for p in (tracked, database, recent)))

    def test_link_inside_candidate_preserves_external_data(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        original = outside / "original"
        original.write_text("preserve", encoding="utf-8")
        cache = self.root / ".pytest_cache"
        cache.mkdir()
        try:
            (cache / "link").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation unavailable")
        self.age()
        self.run_cleanup(apply=True)
        self.assertTrue(original.exists())
        self.assertTrue(cache.exists())

    def test_api_basetemp_allowlist_preserves_other_runtime_and_open_databases(self):
        removable = self.make("services/api-server/.runtime/pytest-p0-api-focused/test_case/db.sqlite3")
        protected = [self.make(name) for name in (
            "services/api-server/.runtime/business-evidence/original.pdf",
            "services/api-server/.runtime/pytest-unknown/original.pdf",
            "services/api-server/.runtime/pytest-p0-regression/test_case/db.sqlite3",
            "services/api-server/.runtime/pytest-p0-regression/test_case/db.sqlite3-wal",
        )]
        self.age()
        self.run_cleanup(apply=True)
        self.assertFalse(removable.exists())
        self.assertTrue(all(p.exists() for p in protected))


if __name__ == "__main__":
    unittest.main()

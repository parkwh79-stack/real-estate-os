"""Public distribution safeguards; no credentials or hosted services needed."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class PublicRepositoryContractTests(unittest.TestCase):
    def test_complete_mit_license(self):
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Permission is hereby granted, free of charge", license_text)
        self.assertIn('THE SOFTWARE IS PROVIDED "AS IS"', license_text)
        self.assertNotIn("Standard MIT License Text", license_text)

    def test_workflows_declare_least_privilege_and_avoid_target_event(self):
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            with self.subTest(path=path.name):
                content = path.read_text(encoding="utf-8")
                self.assertRegex(content, r"(?m)^permissions:\n  contents: read\n")
                self.assertNotIn("pull_request_target:", content)
                self.assertNotIn("write-all", content)

    def test_secret_scan_covers_history_and_redacts_values(self):
        content = (ROOT / ".github/workflows/secrets.yml").read_text(encoding="utf-8")
        for required in ("fetch-depth: 0", "persist-credentials: false",
                         'log-opts="--all"', "--redact=100", "sha256sum --check --strict"):
            self.assertIn(required, content)
        self.assertNotIn("continue-on-error", content)
        self.assertNotIn("upload-artifact", content)

    def test_secret_exceptions_are_exact_historical_fingerprints(self):
        lines = (ROOT / ".gitleaksignore").read_text(encoding="utf-8").splitlines()
        entries = [line for line in lines if line and not line.startswith("#")]
        self.assertEqual(len(entries), len(set(entries)))
        for entry in entries:
            self.assertRegex(entry, r"^[0-9a-f]{40}:[^*]+:[a-z0-9-]+:[0-9]+$")

    def test_dependency_updates_cover_all_three_ecosystems(self):
        content = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")
        for ecosystem in ("npm", "pip", "github-actions"):
            self.assertIn(f"package-ecosystem: {ecosystem}", content)

    def test_codeql_covers_api_web_and_workflows(self):
        content = (ROOT / ".github/workflows/codeql.yml").read_text(encoding="utf-8")
        self.assertIn("language: [python, javascript-typescript, actions]", content)
        self.assertIn("security-events: write", content)

    def test_private_reporting_and_owner_are_documented(self):
        policy = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("/security/advisories/new", policy)
        self.assertIn("Revoke or rotate", policy)
        self.assertIn("@parkwh79-stack", (ROOT / ".github/CODEOWNERS").read_text())


if __name__ == "__main__":
    unittest.main()

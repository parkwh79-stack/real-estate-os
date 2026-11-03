from __future__ import annotations

from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]


def test_api_image_matches_runtime_and_does_not_run_as_root() -> None:
    dockerfile = (API_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert dockerfile.startswith("FROM python:3.12-slim\n")
    assert "COPY --chown=realytics:realytics . ." in dockerfile
    assert "USER realytics" in dockerfile
    assert '${PORT:-8000}' in dockerfile
    assert "/health/live" in dockerfile


def test_api_build_context_excludes_secrets_and_runtime_artifacts() -> None:
    dockerignore = (API_ROOT / ".dockerignore").read_text(encoding="utf-8")

    for required_pattern in (
        ".env",
        "**/.env.*",
        "*.pem",
        "*.key",
        "credentials*",
        "secrets*",
        "**/.runtime/",
        "data/runtime/",
        "tests/",
    ):
        assert required_pattern in dockerignore


def test_compose_uses_runtime_injection_and_canonical_environment_key() -> None:
    compose = (API_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "REALYTICS_ENVIRONMENT:" in compose
    assert "ENVIRONMENT=development" not in compose
    assert "- .:/app" not in compose
    assert "- .env" in compose
    assert "- .env.example" not in compose


def test_cloud_source_upload_reuses_secret_exclusions_before_docker_build() -> None:
    ignore = (API_ROOT / ".gcloudignore").read_text(encoding="utf-8")

    # Docker exclusions alone do not protect the source archive sent to Google.
    # gcloud's documented top-level include shares the existing secret policy.
    assert "#!include:.dockerignore" in ignore.splitlines()
    assert not any(line.startswith("!") for line in ignore.splitlines())
    for pattern in (
        ".gcloudignore",
        "**/.venv/",
        "**/venv/",
        "**/data/runtime/",
        "**/.pytest*.out",
        "**/.pytest*.err",
    ):
        assert pattern in ignore.splitlines()

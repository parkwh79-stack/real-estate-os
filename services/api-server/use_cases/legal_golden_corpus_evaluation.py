"""Load and evaluate externally governed legal golden-corpus artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from legal_intelligence.golden_corpus import (
    GoldenCorpusIntegrityError,
    LegalGoldenCorpusManifest,
    LegalGoldenEvaluationReport,
    LegalGoldenExpectedLabel,
    LegalGoldenPrediction,
    evaluate_legal_golden_corpus,
    parse_json_lines,
    sha256_bytes,
)


def evaluate_legal_golden_corpus_files(
    *,
    manifest_path: str | Path,
    predictions_path: str | Path,
) -> LegalGoldenEvaluationReport:
    """Verify pinned label bytes and evaluate one complete prediction run."""

    manifest_file = Path(manifest_path).resolve(strict=True)
    predictions_file = Path(predictions_path).resolve(strict=True)
    manifest = _load_manifest(manifest_file)
    labels_file = _resolve_labels_file(
        manifest_file=manifest_file,
        labels_path=manifest.labels_path,
    )
    labels_payload = labels_file.read_bytes()
    actual_labels_hash = sha256_bytes(labels_payload)
    if actual_labels_hash != manifest.labels_sha256:
        raise GoldenCorpusIntegrityError(
            "labels SHA-256 does not match the approved manifest"
        )

    expected_labels = cast(
        list[LegalGoldenExpectedLabel],
        parse_json_lines(
            labels_payload,
            model_type=LegalGoldenExpectedLabel,
            artifact_name="expected labels",
        ),
    )
    predictions_payload = predictions_file.read_bytes()
    predictions = cast(
        list[LegalGoldenPrediction],
        parse_json_lines(
            predictions_payload,
            model_type=LegalGoldenPrediction,
            artifact_name="predictions",
        ),
    )
    return evaluate_legal_golden_corpus(
        manifest,
        expected_labels,
        predictions,
        predictions_sha256=sha256_bytes(predictions_payload),
    )


def _load_manifest(path: Path) -> LegalGoldenCorpusManifest:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GoldenCorpusIntegrityError(
            "golden corpus manifest is not valid JSON"
        ) from exc
    if not isinstance(document, dict):
        raise GoldenCorpusIntegrityError(
            "golden corpus manifest must be a JSON object"
        )
    try:
        return LegalGoldenCorpusManifest.model_validate(document)
    except ValueError as exc:
        raise GoldenCorpusIntegrityError(
            f"golden corpus manifest is invalid: {exc}"
        ) from exc


def _resolve_labels_file(
    *,
    manifest_file: Path,
    labels_path: str,
) -> Path:
    root = manifest_file.parent.resolve(strict=True)
    candidate = (root / labels_path).resolve(strict=True)
    if candidate == root or root not in candidate.parents:
        raise GoldenCorpusIntegrityError(
            "labelsPath resolves outside the manifest directory"
        )
    if not candidate.is_file():
        raise GoldenCorpusIntegrityError(
            "labelsPath must resolve to a regular file"
        )
    return candidate


__all__ = ["evaluate_legal_golden_corpus_files"]

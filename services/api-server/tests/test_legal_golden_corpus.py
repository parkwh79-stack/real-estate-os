from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from legal_intelligence.golden_corpus import (
    GoldenCorpusIntegrityError,
    LegalGoldenCorpusManifest,
    LegalGoldenExpectedLabel,
    LegalGoldenPrediction,
    evaluate_legal_golden_corpus,
    sha256_bytes,
)
from scripts import evaluate_legal_golden_corpus as command
from use_cases.legal_golden_corpus_evaluation import (
    evaluate_legal_golden_corpus_files,
)


_HASH_A = "sha256:" + ("a" * 64)
_HASH_B = "sha256:" + ("b" * 64)
_HASH_C = "sha256:" + ("c" * 64)


def _locator(
    suffix: str,
    *,
    content_hash: str = _HASH_A,
) -> dict[str, str]:
    return {
        "corpusSnapshotId": "snapshot-1",
        "sourceFetchId": f"fetch-{suffix}",
        "officialResourceId": f"resource-{suffix}",
        "officialVersionId": f"version-{suffix}",
        "provisionId": f"provision-{suffix}",
        "locator": f"article:{suffix}",
        "contentSha256": content_hash,
    }


def _expected_documents() -> list[dict[str, object]]:
    common = {
        "schemaVersion": "realytics-legal-golden-label-v1",
        "corpusId": "legal-corpus",
        "corpusVersion": "1.0.0",
        "inputSha256": _HASH_C,
    }
    return [
        {
            **common,
            "caseId": "answer-1",
            "inputRef": "fixture://controlled/answer-1",
            "expectedAbstain": False,
            "expectedClaims": [
                {
                    "claimId": "claim-a",
                    "acceptableEvidenceLocators": [_locator("a")],
                }
            ],
        },
        {
            **common,
            "caseId": "answer-2",
            "inputRef": "fixture://controlled/answer-2",
            "expectedAbstain": False,
            "expectedClaims": [
                {
                    "claimId": "claim-b",
                    "acceptableEvidenceLocators": [
                        _locator("b", content_hash=_HASH_B)
                    ],
                }
            ],
        },
        {
            **common,
            "caseId": "abstain-1",
            "inputRef": "fixture://controlled/abstain-1",
            "expectedAbstain": True,
            "expectedClaims": [],
        },
    ]


def _prediction_documents(*, perfect: bool) -> list[dict[str, object]]:
    common = {
        "schemaVersion": "realytics-legal-golden-prediction-v1",
        "evaluationRunId": "run-1",
        "corpusId": "legal-corpus",
        "corpusVersion": "1.0.0",
    }
    if perfect:
        return [
            {
                **common,
                "caseId": "answer-1",
                "abstained": False,
                "predictedClaims": [
                    {
                        "claimId": "claim-a",
                        "evidenceLocators": [_locator("a")],
                    }
                ],
            },
            {
                **common,
                "caseId": "answer-2",
                "abstained": False,
                "predictedClaims": [
                    {
                        "claimId": "claim-b",
                        "evidenceLocators": [
                            _locator("b", content_hash=_HASH_B)
                        ],
                    }
                ],
            },
            {
                **common,
                "caseId": "abstain-1",
                "abstained": True,
                "predictedClaims": [],
            },
        ]
    return [
        {
            **common,
            "caseId": "answer-1",
            "abstained": False,
            "predictedClaims": [
                {
                    "claimId": "claim-a",
                    "evidenceLocators": [_locator("wrong")],
                },
                {
                    "claimId": "unexpected",
                    "evidenceLocators": [],
                },
            ],
        },
        {
            **common,
            "caseId": "answer-2",
            "abstained": True,
            "predictedClaims": [],
        },
        {
            **common,
            "caseId": "abstain-1",
            "abstained": False,
            "predictedClaims": [
                {
                    "claimId": "unsafe-claim",
                    "evidenceLocators": [],
                }
            ],
        },
    ]


def _manifest_document(labels_hash: str) -> dict[str, object]:
    return {
        "schemaVersion": "realytics-legal-golden-corpus-manifest-v1",
        "corpusId": "legal-corpus",
        "corpusVersion": "1.0.0",
        "labelSchemaVersion": "realytics-legal-golden-label-v1",
        "labelsPath": "labels.jsonl",
        "labelsSha256": labels_hash,
        "caseCount": 3,
        "thresholds": {
            "minimumCaseCount": 3,
            "minimumAnswerableCaseCount": 2,
            "minimumAbstentionCaseCount": 1,
            "minimumClaimPrecision": 0.9,
            "minimumClaimRecall": 0.9,
            "minimumAbstentionPrecision": 0.9,
            "minimumAbstentionRecall": 0.9,
            "minimumAbstentionAccuracy": 0.9,
            "minimumLocatorAccuracy": 0.9,
        },
        "approval": {
            "status": "approved",
            "preparedBy": "label-maker",
            "approvedBy": "legal-counsel-checker",
            "approvedAt": datetime(
                2026, 7, 20, tzinfo=timezone.utc
            ).isoformat(),
            "reviewPolicyVersion": "legal-golden-review-v1",
            "approvalRecordHash": _HASH_A,
        },
    }


def _jsonl(documents: list[dict[str, object]]) -> bytes:
    return (
        "\n".join(
            json.dumps(
                document,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            for document in documents
        )
        + "\n"
    ).encode("utf-8")


def _write_artifacts(
    root: Path,
    *,
    perfect: bool,
) -> tuple[Path, Path, Path]:
    labels_path = root / "labels.jsonl"
    labels_payload = _jsonl(_expected_documents())
    labels_path.write_bytes(labels_payload)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            _manifest_document(sha256_bytes(labels_payload)),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    predictions_path = root / "predictions.jsonl"
    predictions_path.write_bytes(
        _jsonl(_prediction_documents(perfect=perfect))
    )
    return manifest_path, labels_path, predictions_path


def test_manifest_requires_human_maker_checker():
    document = _manifest_document(_HASH_A)
    document["approval"]["approvedBy"] = "label-maker"  # type: ignore[index]

    with pytest.raises(
        ValidationError,
        match="preparer and approver must be different",
    ):
        LegalGoldenCorpusManifest.model_validate(document)


def test_label_and_prediction_require_explicit_answer_or_abstention():
    label = _expected_documents()[0]
    label["expectedClaims"] = []
    with pytest.raises(
        ValidationError,
        match="answerable case requires",
    ):
        LegalGoldenExpectedLabel.model_validate(label)

    prediction = _prediction_documents(perfect=True)[0]
    prediction["predictedClaims"] = []
    with pytest.raises(
        ValidationError,
        match="explicit abstention",
    ):
        LegalGoldenPrediction.model_validate(prediction)


def test_perfect_run_passes_every_metric(tmp_path: Path):
    manifest_path, _, predictions_path = _write_artifacts(
        tmp_path,
        perfect=True,
    )

    report = evaluate_legal_golden_corpus_files(
        manifest_path=manifest_path,
        predictions_path=predictions_path,
    )

    assert report.passed is True
    assert report.violations == ()
    assert report.unsafe_answer_count == 0
    assert report.inappropriate_abstention_count == 0
    assert {metric.value for metric in report.metrics} == {1.0}
    assert report.evaluation_sha256.startswith("sha256:")


def test_failed_run_scores_claim_abstention_and_exact_locator_metrics(
    tmp_path: Path,
):
    manifest_path, _, predictions_path = _write_artifacts(
        tmp_path,
        perfect=False,
    )

    report = evaluate_legal_golden_corpus_files(
        manifest_path=manifest_path,
        predictions_path=predictions_path,
    )
    metrics = {metric.metric: metric for metric in report.metrics}

    assert report.passed is False
    assert report.true_positive_claim_count == 1
    assert report.false_positive_claim_count == 2
    assert report.false_negative_claim_count == 1
    assert report.unsafe_answer_count == 1
    assert report.inappropriate_abstention_count == 1
    assert metrics["claim_precision"].value == pytest.approx(1 / 3)
    assert metrics["claim_recall"].value == 0.5
    assert metrics["abstention_precision"].value == 0.0
    assert metrics["abstention_recall"].value == 0.0
    assert metrics["abstention_accuracy"].value == pytest.approx(1 / 3)
    assert metrics["locator_accuracy"].value == 0.0
    assert "locator_accuracy_below_threshold" in report.violations


def test_prediction_set_must_exactly_cover_frozen_case_set():
    labels_payload = _jsonl(_expected_documents())
    manifest = LegalGoldenCorpusManifest.model_validate(
        _manifest_document(sha256_bytes(labels_payload))
    )
    labels = [
        LegalGoldenExpectedLabel.model_validate(document)
        for document in _expected_documents()
    ]
    predictions = [
        LegalGoldenPrediction.model_validate(document)
        for document in _prediction_documents(perfect=True)[:-1]
    ]

    with pytest.raises(
        GoldenCorpusIntegrityError,
        match="missing=.*abstain-1",
    ):
        evaluate_legal_golden_corpus(
            manifest,
            labels,
            predictions,
            predictions_sha256=_HASH_B,
        )


def test_labels_are_rejected_after_manifest_pinned_bytes_are_changed(
    tmp_path: Path,
):
    manifest_path, labels_path, predictions_path = _write_artifacts(
        tmp_path,
        perfect=True,
    )
    labels_path.write_bytes(labels_path.read_bytes() + b"\n")

    with pytest.raises(
        GoldenCorpusIntegrityError,
        match="SHA-256",
    ):
        evaluate_legal_golden_corpus_files(
            manifest_path=manifest_path,
            predictions_path=predictions_path,
        )


def test_cli_exit_code_distinguishes_threshold_failure(
    tmp_path: Path,
    capsys,
):
    manifest_path, _, predictions_path = _write_artifacts(
        tmp_path,
        perfect=False,
    )

    exit_code = command.main(
        [
            "--manifest",
            str(manifest_path),
            "--predictions",
            str(predictions_path),
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 3
    assert report["passed"] is False
    assert report["schemaVersion"] == (
        "realytics-legal-golden-evaluation-v1"
    )


def test_cli_returns_integrity_error_for_tampered_labels(
    tmp_path: Path,
    capsys,
):
    manifest_path, labels_path, predictions_path = _write_artifacts(
        tmp_path,
        perfect=True,
    )
    labels_path.write_bytes(b"{}\n")

    exit_code = command.main(
        [
            "--manifest",
            str(manifest_path),
            "--predictions",
            str(predictions_path),
        ]
    )
    error = json.loads(capsys.readouterr().err)

    assert exit_code == 2
    assert error["status"] == "invalid_artifact"

"""Versioned contracts and deterministic evaluation for a legal golden corpus.

This module intentionally contains no bundled legal examples.  A production
corpus is an externally governed artifact whose exact label bytes are pinned
by the approved manifest.  The evaluator only consumes those artifacts and
never infers, repairs, or silently drops a case.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator


MANIFEST_SCHEMA_VERSION = "realytics-legal-golden-corpus-manifest-v1"
LABEL_SCHEMA_VERSION = "realytics-legal-golden-label-v1"
PREDICTION_SCHEMA_VERSION = "realytics-legal-golden-prediction-v1"
EVALUATION_SCHEMA_VERSION = "realytics-legal-golden-evaluation-v1"

_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"
_SEMVER_PATTERN = (
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


class GoldenCorpusIntegrityError(ValueError):
    """Raised when a corpus or prediction artifact cannot be trusted."""


class GoldenCorpusModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
        frozen=True,
    )


class GoldenCorpusApproval(GoldenCorpusModel):
    """Maker-checker approval for the frozen label artifact."""

    status: Literal["approved"]
    prepared_by: str = Field(min_length=1, max_length=300)
    approved_by: str = Field(min_length=1, max_length=300)
    approved_at: datetime
    review_policy_version: str = Field(min_length=1, max_length=100)
    approval_record_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _validate_human_approval(self) -> "GoldenCorpusApproval":
        if self.prepared_by == self.approved_by:
            raise ValueError(
                "golden corpus preparer and approver must be different"
            )
        if (
            self.approved_at.tzinfo is None
            or self.approved_at.utcoffset() is None
        ):
            raise ValueError("approvedAt must be timezone-aware")
        return self


class GoldenCorpusThresholds(GoldenCorpusModel):
    """Counsel-approved minimums; the evaluator supplies no hidden defaults."""

    minimum_case_count: int = Field(ge=2)
    minimum_answerable_case_count: int = Field(ge=1)
    minimum_abstention_case_count: int = Field(ge=1)
    minimum_claim_precision: float = Field(ge=0, le=1)
    minimum_claim_recall: float = Field(ge=0, le=1)
    minimum_abstention_precision: float = Field(ge=0, le=1)
    minimum_abstention_recall: float = Field(ge=0, le=1)
    minimum_abstention_accuracy: float = Field(ge=0, le=1)
    minimum_locator_accuracy: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _validate_population_minimums(self) -> "GoldenCorpusThresholds":
        category_minimum = (
            self.minimum_answerable_case_count
            + self.minimum_abstention_case_count
        )
        if self.minimum_case_count < category_minimum:
            raise ValueError(
                "minimumCaseCount must cover answerable and abstention minima"
            )
        return self


class LegalGoldenCorpusManifest(GoldenCorpusModel):
    schema_version: Literal[
        "realytics-legal-golden-corpus-manifest-v1"
    ] = MANIFEST_SCHEMA_VERSION
    corpus_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    corpus_version: str = Field(pattern=_SEMVER_PATTERN)
    label_schema_version: Literal[
        "realytics-legal-golden-label-v1"
    ] = LABEL_SCHEMA_VERSION
    labels_path: str = Field(min_length=1, max_length=500)
    labels_sha256: str = Field(pattern=_SHA256_PATTERN)
    case_count: int = Field(ge=1)
    thresholds: GoldenCorpusThresholds
    approval: GoldenCorpusApproval

    @model_validator(mode="after")
    def _validate_manifest_contract(self) -> "LegalGoldenCorpusManifest":
        path = Path(self.labels_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(
                "labelsPath must be a manifest-relative path without '..'"
            )
        if self.case_count < self.thresholds.minimum_case_count:
            raise ValueError(
                "caseCount is lower than the approved minimumCaseCount"
            )
        return self


class GoldenEvidenceLocator(GoldenCorpusModel):
    """Exact, normalized corpus lineage used for locator scoring."""

    corpus_snapshot_id: str = Field(min_length=1, max_length=300)
    source_fetch_id: str = Field(min_length=1, max_length=300)
    official_resource_id: str = Field(min_length=1, max_length=500)
    official_version_id: str = Field(min_length=1, max_length=500)
    provision_id: str | None = Field(default=None, max_length=500)
    locator: str = Field(min_length=1, max_length=2_000)
    content_sha256: str = Field(pattern=_SHA256_PATTERN)

    def exact_identity(self) -> tuple[str | None, ...]:
        return (
            self.corpus_snapshot_id,
            self.source_fetch_id,
            self.official_resource_id,
            self.official_version_id,
            self.provision_id,
            self.locator,
            self.content_sha256,
        )


class ExpectedLegalClaim(GoldenCorpusModel):
    claim_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    acceptable_evidence_locators: tuple[
        GoldenEvidenceLocator, ...
    ] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_unique_locators(self) -> "ExpectedLegalClaim":
        identities = [
            locator.exact_identity()
            for locator in self.acceptable_evidence_locators
        ]
        if len(identities) != len(set(identities)):
            raise ValueError(
                "acceptableEvidenceLocators must not contain duplicates"
            )
        return self


class LegalGoldenExpectedLabel(GoldenCorpusModel):
    schema_version: Literal[
        "realytics-legal-golden-label-v1"
    ] = LABEL_SCHEMA_VERSION
    corpus_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    corpus_version: str = Field(pattern=_SEMVER_PATTERN)
    case_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    input_ref: str = Field(min_length=1, max_length=2_000)
    input_sha256: str = Field(pattern=_SHA256_PATTERN)
    expected_abstain: bool
    expected_claims: tuple[ExpectedLegalClaim, ...] = ()

    @model_validator(mode="after")
    def _validate_expected_result(self) -> "LegalGoldenExpectedLabel":
        if self.expected_abstain and self.expected_claims:
            raise ValueError(
                "an abstention case cannot contain expectedClaims"
            )
        if not self.expected_abstain and not self.expected_claims:
            raise ValueError(
                "an answerable case requires at least one expectedClaim"
            )
        claim_ids = [claim.claim_id for claim in self.expected_claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("expectedClaims must have unique claimId values")
        return self


class PredictedLegalClaim(GoldenCorpusModel):
    claim_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    evidence_locators: tuple[GoldenEvidenceLocator, ...] = ()

    @model_validator(mode="after")
    def _validate_unique_locators(self) -> "PredictedLegalClaim":
        identities = [
            locator.exact_identity() for locator in self.evidence_locators
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("evidenceLocators must not contain duplicates")
        return self


class LegalGoldenPrediction(GoldenCorpusModel):
    schema_version: Literal[
        "realytics-legal-golden-prediction-v1"
    ] = PREDICTION_SCHEMA_VERSION
    evaluation_run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    corpus_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    corpus_version: str = Field(pattern=_SEMVER_PATTERN)
    case_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    abstained: bool
    predicted_claims: tuple[PredictedLegalClaim, ...] = ()

    @model_validator(mode="after")
    def _validate_prediction(self) -> "LegalGoldenPrediction":
        if self.abstained and self.predicted_claims:
            raise ValueError(
                "an abstained prediction cannot contain predictedClaims"
            )
        if not self.abstained and not self.predicted_claims:
            raise ValueError(
                "empty output must be an explicit abstention"
            )
        claim_ids = [claim.claim_id for claim in self.predicted_claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError(
                "predictedClaims must have unique claimId values"
            )
        return self


class GoldenMetricResult(GoldenCorpusModel):
    metric: Literal[
        "claim_precision",
        "claim_recall",
        "abstention_precision",
        "abstention_recall",
        "abstention_accuracy",
        "locator_accuracy",
    ]
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    value: float | None = Field(default=None, ge=0, le=1)
    minimum: float = Field(ge=0, le=1)
    passed: bool


class LegalGoldenEvaluationReport(GoldenCorpusModel):
    schema_version: Literal[
        "realytics-legal-golden-evaluation-v1"
    ] = EVALUATION_SCHEMA_VERSION
    evaluation_run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    corpus_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    corpus_version: str = Field(pattern=_SEMVER_PATTERN)
    labels_sha256: str = Field(pattern=_SHA256_PATTERN)
    predictions_sha256: str = Field(pattern=_SHA256_PATTERN)
    evaluated_case_count: int = Field(ge=0)
    answerable_case_count: int = Field(ge=0)
    abstention_case_count: int = Field(ge=0)
    true_positive_claim_count: int = Field(ge=0)
    false_positive_claim_count: int = Field(ge=0)
    false_negative_claim_count: int = Field(ge=0)
    unsafe_answer_count: int = Field(ge=0)
    inappropriate_abstention_count: int = Field(ge=0)
    metrics: tuple[GoldenMetricResult, ...]
    violations: tuple[str, ...]
    passed: bool
    evaluation_sha256: str = Field(pattern=_SHA256_PATTERN)


def sha256_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def parse_json_lines(
    payload: bytes,
    *,
    model_type: type[GoldenCorpusModel],
    artifact_name: str,
) -> list[GoldenCorpusModel]:
    """Strict UTF-8 JSONL parser with actionable line diagnostics."""

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GoldenCorpusIntegrityError(
            f"{artifact_name} must be UTF-8"
        ) from exc
    rows: list[GoldenCorpusModel] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            document = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise GoldenCorpusIntegrityError(
                f"{artifact_name} line {line_number} is not valid JSON"
            ) from exc
        if not isinstance(document, dict):
            raise GoldenCorpusIntegrityError(
                f"{artifact_name} line {line_number} must be an object"
            )
        try:
            rows.append(model_type.model_validate(document))
        except ValueError as exc:
            raise GoldenCorpusIntegrityError(
                f"{artifact_name} line {line_number} is invalid: {exc}"
            ) from exc
    if not rows:
        raise GoldenCorpusIntegrityError(
            f"{artifact_name} must contain at least one record"
        )
    return rows


def evaluate_legal_golden_corpus(
    manifest: LegalGoldenCorpusManifest,
    expected_labels: Sequence[LegalGoldenExpectedLabel],
    predictions: Sequence[LegalGoldenPrediction],
    *,
    predictions_sha256: str,
) -> LegalGoldenEvaluationReport:
    """Evaluate one complete prediction run against a frozen corpus.

    All cases must be present exactly once.  Unknown or missing cases are an
    integrity error rather than an accuracy penalty, preventing accidental
    cherry-picking or denominator manipulation.
    """

    _validate_complete_artifacts(manifest, expected_labels, predictions)
    expected_by_case = {label.case_id: label for label in expected_labels}
    prediction_by_case = {
        prediction.case_id: prediction for prediction in predictions
    }

    true_positive_claims = 0
    false_positive_claims = 0
    false_negative_claims = 0
    exact_locator_claims = 0
    matched_claims_requiring_locator = 0
    abstention_true_positive = 0
    abstention_false_positive = 0
    abstention_false_negative = 0
    abstention_true_negative = 0

    for case_id in sorted(expected_by_case):
        expected = expected_by_case[case_id]
        predicted = prediction_by_case[case_id]

        if expected.expected_abstain:
            if predicted.abstained:
                abstention_true_positive += 1
            else:
                abstention_false_negative += 1
        elif predicted.abstained:
            abstention_false_positive += 1
        else:
            abstention_true_negative += 1

        expected_claims = {
            claim.claim_id: claim for claim in expected.expected_claims
        }
        predicted_claims = {
            claim.claim_id: claim for claim in predicted.predicted_claims
        }
        expected_ids = set(expected_claims)
        predicted_ids = set(predicted_claims)
        matched_ids = expected_ids & predicted_ids

        true_positive_claims += len(matched_ids)
        false_positive_claims += len(predicted_ids - expected_ids)
        false_negative_claims += len(expected_ids - predicted_ids)

        for claim_id in matched_ids:
            matched_claims_requiring_locator += 1
            acceptable = {
                locator.exact_identity()
                for locator in expected_claims[
                    claim_id
                ].acceptable_evidence_locators
            }
            submitted = {
                locator.exact_identity()
                for locator in predicted_claims[
                    claim_id
                ].evidence_locators
            }
            if acceptable & submitted:
                exact_locator_claims += 1

    thresholds = manifest.thresholds
    metric_inputs = (
        (
            "claim_precision",
            true_positive_claims,
            true_positive_claims + false_positive_claims,
            thresholds.minimum_claim_precision,
        ),
        (
            "claim_recall",
            true_positive_claims,
            true_positive_claims + false_negative_claims,
            thresholds.minimum_claim_recall,
        ),
        (
            "abstention_precision",
            abstention_true_positive,
            abstention_true_positive + abstention_false_positive,
            thresholds.minimum_abstention_precision,
        ),
        (
            "abstention_recall",
            abstention_true_positive,
            abstention_true_positive + abstention_false_negative,
            thresholds.minimum_abstention_recall,
        ),
        (
            "abstention_accuracy",
            abstention_true_positive + abstention_true_negative,
            len(expected_labels),
            thresholds.minimum_abstention_accuracy,
        ),
        (
            "locator_accuracy",
            exact_locator_claims,
            matched_claims_requiring_locator,
            thresholds.minimum_locator_accuracy,
        ),
    )
    metrics = tuple(
        _metric_result(
            metric=metric,
            numerator=numerator,
            denominator=denominator,
            minimum=minimum,
        )
        for metric, numerator, denominator, minimum in metric_inputs
    )

    answerable_case_count = sum(
        not label.expected_abstain for label in expected_labels
    )
    abstention_case_count = len(expected_labels) - answerable_case_count
    violations: list[str] = []
    if len(expected_labels) < thresholds.minimum_case_count:
        violations.append("minimum_case_count_not_met")
    if (
        answerable_case_count
        < thresholds.minimum_answerable_case_count
    ):
        violations.append("minimum_answerable_case_count_not_met")
    if (
        abstention_case_count
        < thresholds.minimum_abstention_case_count
    ):
        violations.append("minimum_abstention_case_count_not_met")
    for metric in metrics:
        if not metric.passed:
            suffix = (
                "undefined"
                if metric.value is None
                else "below_threshold"
            )
            violations.append(f"{metric.metric}_{suffix}")

    evaluation_run_id = predictions[0].evaluation_run_id
    report_payload: dict[str, Any] = {
        "evaluationRunId": evaluation_run_id,
        "corpusId": manifest.corpus_id,
        "corpusVersion": manifest.corpus_version,
        "labelsSha256": manifest.labels_sha256,
        "predictionsSha256": predictions_sha256,
        "evaluatedCaseCount": len(expected_labels),
        "answerableCaseCount": answerable_case_count,
        "abstentionCaseCount": abstention_case_count,
        "truePositiveClaimCount": true_positive_claims,
        "falsePositiveClaimCount": false_positive_claims,
        "falseNegativeClaimCount": false_negative_claims,
        "unsafeAnswerCount": abstention_false_negative,
        "inappropriateAbstentionCount": abstention_false_positive,
        "metrics": [
            metric.model_dump(mode="json", by_alias=True)
            for metric in metrics
        ],
        "violations": violations,
        "passed": not violations,
    }
    evaluation_hash = sha256_bytes(
        json.dumps(
            report_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    return LegalGoldenEvaluationReport(
        evaluation_run_id=evaluation_run_id,
        corpus_id=manifest.corpus_id,
        corpus_version=manifest.corpus_version,
        labels_sha256=manifest.labels_sha256,
        predictions_sha256=predictions_sha256,
        evaluated_case_count=len(expected_labels),
        answerable_case_count=answerable_case_count,
        abstention_case_count=abstention_case_count,
        true_positive_claim_count=true_positive_claims,
        false_positive_claim_count=false_positive_claims,
        false_negative_claim_count=false_negative_claims,
        unsafe_answer_count=abstention_false_negative,
        inappropriate_abstention_count=abstention_false_positive,
        metrics=metrics,
        violations=tuple(violations),
        passed=not violations,
        evaluation_sha256=evaluation_hash,
    )


def _metric_result(
    *,
    metric: str,
    numerator: int,
    denominator: int,
    minimum: float,
) -> GoldenMetricResult:
    value = numerator / denominator if denominator else None
    return GoldenMetricResult(
        metric=metric,
        numerator=numerator,
        denominator=denominator,
        value=value,
        minimum=minimum,
        passed=value is not None and value >= minimum,
    )


def _validate_complete_artifacts(
    manifest: LegalGoldenCorpusManifest,
    expected_labels: Sequence[LegalGoldenExpectedLabel],
    predictions: Sequence[LegalGoldenPrediction],
) -> None:
    if len(expected_labels) != manifest.case_count:
        raise GoldenCorpusIntegrityError(
            "label count does not match manifest caseCount"
        )
    expected_ids = _unique_case_ids(expected_labels, "expected labels")
    prediction_ids = _unique_case_ids(predictions, "predictions")
    if expected_ids != prediction_ids:
        missing = sorted(expected_ids - prediction_ids)
        unknown = sorted(prediction_ids - expected_ids)
        raise GoldenCorpusIntegrityError(
            "prediction cases must exactly match the corpus: "
            f"missing={missing!r}, unknown={unknown!r}"
        )
    run_ids = {
        prediction.evaluation_run_id for prediction in predictions
    }
    if len(run_ids) != 1:
        raise GoldenCorpusIntegrityError(
            "all predictions must have one evaluationRunId"
        )
    for label in expected_labels:
        if (
            label.corpus_id != manifest.corpus_id
            or label.corpus_version != manifest.corpus_version
        ):
            raise GoldenCorpusIntegrityError(
                f"label {label.case_id!r} targets a different corpus"
            )
    for prediction in predictions:
        if (
            prediction.corpus_id != manifest.corpus_id
            or prediction.corpus_version != manifest.corpus_version
        ):
            raise GoldenCorpusIntegrityError(
                f"prediction {prediction.case_id!r} targets a "
                "different corpus"
            )


def _unique_case_ids(
    rows: Iterable[
        LegalGoldenExpectedLabel | LegalGoldenPrediction
    ],
    artifact_name: str,
) -> set[str]:
    result: set[str] = set()
    for row in rows:
        if row.case_id in result:
            raise GoldenCorpusIntegrityError(
                f"{artifact_name} contains duplicate caseId "
                f"{row.case_id!r}"
            )
        result.add(row.case_id)
    return result


__all__ = [
    "EVALUATION_SCHEMA_VERSION",
    "GoldenCorpusApproval",
    "GoldenCorpusIntegrityError",
    "GoldenCorpusThresholds",
    "GoldenEvidenceLocator",
    "GoldenMetricResult",
    "LABEL_SCHEMA_VERSION",
    "LegalGoldenCorpusManifest",
    "LegalGoldenEvaluationReport",
    "LegalGoldenExpectedLabel",
    "LegalGoldenPrediction",
    "MANIFEST_SCHEMA_VERSION",
    "PREDICTION_SCHEMA_VERSION",
    "PredictedLegalClaim",
    "ExpectedLegalClaim",
    "evaluate_legal_golden_corpus",
    "parse_json_lines",
    "sha256_bytes",
]

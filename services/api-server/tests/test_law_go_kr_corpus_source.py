from __future__ import annotations

import json
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from legal_intelligence.models import LegalResourceKind
from legal_intelligence.resilience import (
    ProviderMetric,
    ProviderQuarantineRecord,
    RateLimitDecision,
    RetryExhaustedError,
    RetryPolicy,
)
from use_cases.law_go_kr_corpus_source import (
    LawGoKrCorpusSourceError,
    LawGoKrOfficialCorpusPageSource,
    LawGoKrRecordContext,
)
from use_cases.legal_corpus_sync import (
    OfficialLegalRecordInput,
    OfficialProvisionInput,
    _validate_page_request_envelope,
)


NOW = datetime(2026, 7, 20, 1, 2, 3, tzinfo=timezone.utc)


def _normalizer(context: LawGoKrRecordContext) -> OfficialLegalRecordInput:
    identifier = str(context.list_row["법령ID"])
    return OfficialLegalRecordInput(
        resource_type="statute",
        official_identifier=identifier,
        official_version_identifier=f"{identifier}-v1",
        canonical_title=str(context.list_row["법령명한글"]),
        issuing_authority="central_government",
        provisions=(
            OfficialProvisionInput(
                provision_type="article",
                provision_identifier="article-1",
                display_path="Article 1",
                body_text=str(
                    context.detail_payload["법령"]["조문"][
                        "조문단위"
                    ][0]["조문내용"]
                ),
                source_locator=(
                    f"https://www.law.go.kr/lawService.do?ID={identifier}"
                ),
            ),
        ),
        observed_at=context.observed_at,
    )


def _source(http_get_json, **overrides):
    options = {
        "oc": "secret@example.com",
        "resource_kind": LegalResourceKind.STATUTE,
        "query": "Housing Act",
        "requested_scope": "statutes:housing",
        "requested_by": "legal-sync-worker",
        "requested_as_of_date": date(2025, 1, 1),
        "record_normalizer": _normalizer,
        "display": 2,
        "http_get_json": http_get_json,
        "now": lambda: NOW,
    }
    options.update(overrides)
    return LawGoKrOfficialCorpusPageSource(**options)


def test_source_fetches_list_and_detail_and_emits_resume_checkpoint():
    calls = []

    def fake_get(url: str, timeout: float):
        params = parse_qs(urlsplit(url).query)
        calls.append(params)
        if urlsplit(url).path.endswith("lawSearch.do"):
            return {
                "LawSearch": {
                    "law": [
                        {
                            "법령ID": "law-1",
                            "법령명한글": "Housing Act",
                        },
                        {
                            "법령ID": "law-2",
                            "법령명한글": "Housing Decree",
                        },
                    ],
                    "totalCnt": 3,
                    "page": "1",
                    "numOfRows": "2",
                }
            }
        identifier = params["ID"][0]
        return {
            "법령": {
                "기본정보": {"법령ID": identifier},
                "조문": {
                    "조문단위": [
                        {"조문내용": f"detail-{identifier}"}
                    ]
                },
            }
        }

    page = _source(fake_get).fetch_page(checkpoint_before=None)

    assert page.terminal is False
    assert page.partial is False
    assert len(page.requests) == 2
    assert len({request.idempotency_key for request in page.requests}) == 1
    assert page.requests[0].idempotency_key.startswith("law-go-kr-page:")
    _validate_page_request_envelope(page.requests)
    assert calls[0]["target"] == ["law"]
    assert calls[0]["page"] == ["1"]
    assert [call["ID"][0] for call in calls[1:]] == [
        "law-1",
        "law-2",
    ]

    first = page.requests[0]
    assert first.source_authority == "central_government"
    assert first.transport == "direct_api"
    assert first.checkpoint_before is None
    checkpoint = json.loads(first.checkpoint_after)
    assert checkpoint["nextPage"] == 2
    assert first.fetch_metadata == {
        "requestedAsOfDate": "2025-01-01",
        "verifiedEffectiveDate": None,
        "observedAt": NOW.isoformat(),
        "historyObserved": False,
    }
    assert b"secret@example.com" not in first.raw_content
    assert "secret@example.com" not in first.source_locator
    assert (
        first.source_verification.response_sha256
        == __import__("hashlib").sha256(first.raw_content).hexdigest()
    )


def test_page_acquisition_key_replays_exact_bytes_and_rotates_on_correction():
    def source_for(detail_text: str):
        def fake_get(url: str, timeout: float):
            del timeout
            params = parse_qs(urlsplit(url).query)
            if urlsplit(url).path.endswith("lawSearch.do"):
                return {
                    "LawSearch": {
                        "law": {
                            "\ubc95\ub839ID": "law-1",
                            "\ubc95\ub839\uba85\ud55c\uae00": "Housing Act",
                        },
                        "totalCnt": "1",
                        "page": "1",
                        "numOfRows": "2",
                    }
                }
            return {
                "\ubc95\ub839": {
                    "\uae30\ubcf8\uc815\ubcf4": {"\ubc95\ub839ID": params["ID"][0]},
                    "\uc870\ubb38": {
                        "\uc870\ubb38\ub2e8\uc704": [
                            {"\uc870\ubb38\ub0b4\uc6a9": detail_text}
                        ]
                    },
                }
            }

        return _source(fake_get)

    first = source_for("original").fetch_page(
        checkpoint_before=None
    ).requests[0]
    exact_retry = source_for("original").fetch_page(
        checkpoint_before=None
    ).requests[0]
    correction = source_for("corrected").fetch_page(
        checkpoint_before=None
    ).requests[0]

    assert first.idempotency_key == exact_retry.idempotency_key
    assert first.source_verification.response_sha256 == (
        exact_retry.source_verification.response_sha256
    )
    assert correction.idempotency_key != first.idempotency_key
    assert correction.source_verification.response_sha256 != (
        first.source_verification.response_sha256
    )


def test_corpus_source_quarantines_boundary_failure_without_retrying():
    calls = 0

    class Quarantine:
        def __init__(self) -> None:
            self.items: list[ProviderQuarantineRecord] = []

        def store(self, record: ProviderQuarantineRecord) -> None:
            self.items.append(record)

    quarantine = Quarantine()

    def deep_get(_url: str, _timeout: float):
        nonlocal calls
        calls += 1
        return {"one": {"two": {"three": "rejected"}}}

    source = _source(
        deep_get,
        json_max_depth=2,
        json_max_collection_items=10,
        json_max_nodes=100,
        quarantine_sink=quarantine,
        retry_policy=RetryPolicy(max_attempts=3),
    )

    with pytest.raises(RetryExhaustedError) as error:
        source.fetch_page(checkpoint_before=None)

    assert calls == 1
    assert error.value.retryable is False
    assert [item.reason_code for item in quarantine.items] == [
        "PROVIDER_RESPONSE_JSON_DEPTH_LIMIT"
    ]


def test_source_resumes_only_its_own_target_and_query_scope():
    source = _source(lambda *_: {})
    foreign_checkpoint = json.dumps(
        {
            "schemaVersion": 1,
            "target": "prec",
            "querySha256": "0" * 64,
            "nextPage": 2,
        }
    )

    with pytest.raises(
        LawGoKrCorpusSourceError,
        match="does not belong",
    ):
        source.fetch_page(checkpoint_before=foreign_checkpoint)


def test_source_marks_partial_page_when_one_detail_cannot_normalize():
    def fake_get(url: str, timeout: float):
        params = parse_qs(urlsplit(url).query)
        if urlsplit(url).path.endswith("lawSearch.do"):
            return {
                "LawSearch": {
                    "law": [
                        {
                            "법령ID": "law-1",
                            "법령명한글": "Housing Act",
                        },
                        {
                            "법령ID": "law-2",
                            "법령명한글": "Broken Act",
                        },
                    ],
                    "totalCnt": 2,
                    "page": "1",
                    "numOfRows": "2",
                }
            }
        if params["ID"] == ["law-2"]:
            raise TimeoutError("detail timeout")
        return {
            "법령": {
                "기본정보": {"법령ID": "law-1"},
                "조문": {
                    "조문단위": [
                        {"조문내용": "verified detail"}
                    ]
                },
            }
        }

    page = _source(fake_get).fetch_page(checkpoint_before=None)

    assert page.terminal is True
    assert page.partial is True
    assert len(page.requests) == 1
    assert page.diagnostics[0].code == "LAW_GO_KR_RECORD_QUARANTINED"
    assert page.diagnostics[0].official_identifier == "law-2"
    assert page.diagnostics[0].target == "law"


def test_source_honors_local_retry_after_without_unlimited_network_call():
    sleeps = []
    metrics: list[ProviderMetric] = []
    network_calls = []

    class Limiter:
        calls = 0

        def acquire(self, provider_id, *, tokens=1.0):
            assert provider_id == "law_go_kr_direct"
            assert tokens == 1.0
            self.calls += 1
            if self.calls == 2:
                return RateLimitDecision(
                    allowed=False,
                    retry_after_seconds=0.25,
                )
            return RateLimitDecision(allowed=True)

    class Telemetry:
        def record(self, metric):
            metrics.append(metric)

    def fake_get(url: str, timeout: float):
        del timeout
        network_calls.append(url)
        params = parse_qs(urlsplit(url).query)
        if urlsplit(url).path.endswith("lawSearch.do"):
            return {
                "LawSearch": {
                    "law": {
                        "법령ID": "law-1",
                        "법령명한글": "Housing Act",
                    },
                    "totalCnt": "1",
                    "page": "1",
                    "numOfRows": "2",
                }
            }
        return {
            "법령": {
                "기본정보": {"법령ID": params["ID"][0]},
                "조문": {
                    "조문단위": [
                        {"조문내용": "verified detail"}
                    ]
                },
            }
        }

    page = _source(
        fake_get,
        rate_limiter=Limiter(),
        retry_policy=RetryPolicy(
            max_attempts=2,
            initial_backoff_seconds=0,
        ),
        sleep=sleeps.append,
        jitter=lambda _low, high: high,
        telemetry=Telemetry(),
    ).fetch_page(checkpoint_before=None)

    assert page.partial is False
    assert sleeps == [0.25]
    assert len(network_calls) == 2
    detail_metric = next(
        item for item in metrics if item.operation == "corpus_detail"
    )
    assert detail_metric.attempts == 2
    assert detail_metric.rate_limited is True


def test_source_rejects_list_only_or_schema_drift_page():
    quarantined = []

    class Quarantine:
        def store(self, record):
            quarantined.append(record)

    source = _source(
        lambda *_: {"UnexpectedSearch": {"unexpected": []}},
        quarantine_sink=Quarantine(),
    )

    with pytest.raises(
        LawGoKrCorpusSourceError,
        match="registered",
    ):
        source.fetch_page(checkpoint_before=None)

    assert len(quarantined) == 1
    assert (
        quarantined[0].reason_code
        == "LAW_GO_KR_CORPUS_LIST_SCHEMA_MISMATCH"
    )
    assert quarantined[0].target == "law"
    assert quarantined[0].payload_digest.startswith("sha256:")


def test_source_validates_fixture_backed_history_before_normalization():
    seen_history = []

    def fake_get(url: str, timeout: float):
        params = parse_qs(urlsplit(url).query)
        if urlsplit(url).path.endswith("lawSearch.do"):
            return {
                "LawSearch": {
                    "law": {
                        "법령ID": "law-1",
                        "법령명한글": "Housing Act",
                    },
                    "totalCnt": "1",
                    "page": "1",
                    "numOfRows": "2",
                }
            }
        return {
            "법령": {
                "기본정보": {"법령ID": params["ID"][0]},
                "조문": {
                    "조문단위": [
                        {"조문내용": "verified detail"}
                    ]
                },
            }
        }

    def history_fetcher(descriptor, row, detail):
        seen_history.append((descriptor.list_target, row, detail))
        return {
            "LawHistory": {
                "법령ID": "law-1",
                "lawHistory": [
                    {
                        "법령일련번호": "law-1-v1",
                        "시행일자": "20250101",
                    }
                ],
            }
        }

    page = _source(
        fake_get,
        history_fetcher=history_fetcher,
        history_schema_version="law-go-kr-statute-history-v1",
    ).fetch_page(checkpoint_before=None)

    assert page.partial is False
    assert len(seen_history) == 1
    request = page.requests[0]
    assert request.fetch_metadata["historyObserved"] is True
    raw = json.loads(request.raw_content)
    assert (
        raw["records"][0]["history"]["LawHistory"]["lawHistory"][0][
            "법령일련번호"
        ]
        == "law-1-v1"
    )


def test_source_quarantines_history_that_exceeds_structural_boundary():
    quarantined = []
    normalizer_called = False

    class Quarantine:
        def store(self, record):
            quarantined.append(record)

    def fake_get(url: str, timeout: float):
        del timeout
        params = parse_qs(urlsplit(url).query)
        if urlsplit(url).path.endswith("lawSearch.do"):
            return {
                "LawSearch": {
                    "law": {
                        "\ubc95\ub839ID": "law-1",
                        "\ubc95\ub839\uba85\ud55c\uae00": "Housing Act",
                    },
                    "totalCnt": "1",
                    "page": "1",
                    "numOfRows": "2",
                }
            }
        return {
            "\ubc95\ub839": {
                "\uae30\ubcf8\uc815\ubcf4": {"\ubc95\ub839ID": params["ID"][0]},
                "\uc870\ubb38": {
                    "\uc870\ubb38\ub2e8\uc704": [
                        {"\uc870\ubb38\ub0b4\uc6a9": "verified detail"}
                    ]
                },
            }
        }

    def history_fetcher(_descriptor, _row, _detail):
        return {
            "LawHistory": {
                "\ubc95\ub839ID": "law-1",
                "lawHistory": [
                    {
                        "\ubc95\ub839\uc77c\ub828\ubc88\ud638": (
                            f"law-1-v{index}"
                        )
                    }
                    for index in range(5)
                ],
            }
        }

    def normalizer(context):
        nonlocal normalizer_called
        normalizer_called = True
        return _normalizer(context)

    source = _source(
        fake_get,
        record_normalizer=normalizer,
        history_fetcher=history_fetcher,
        history_schema_version="law-go-kr-statute-history-v1",
        json_max_collection_items=4,
        quarantine_sink=Quarantine(),
    )

    with pytest.raises(
        LawGoKrCorpusSourceError,
        match="no publishable records",
    ) as error:
        source.fetch_page(checkpoint_before=None)

    assert error.value.partial is True
    assert error.value.retryable is False
    assert normalizer_called is False
    assert len(quarantined) == 1
    assert quarantined[0].operation == "corpus_history"
    assert (
        quarantined[0].reason_code
        == "PROVIDER_RESPONSE_JSON_COLLECTION_LIMIT"
    )


def test_source_rejects_unregistered_history_schema_version():
    with pytest.raises(
        ValueError,
        match="fixture-backed contract",
    ):
        _source(
            lambda *_: {},
            history_fetcher=lambda *_: {},
            history_schema_version="caller-defined-v1",
        )


def test_source_quarantines_detail_bound_to_a_different_record():
    def fake_get(url: str, timeout: float):
        if urlsplit(url).path.endswith("lawSearch.do"):
            return {
                "LawSearch": {
                    "law": {
                        "법령ID": "law-1",
                        "법령명한글": "Housing Act",
                    },
                    "totalCnt": "1",
                    "page": "1",
                    "numOfRows": "2",
                }
            }
        return {
            "법령": {
                "기본정보": {"법령ID": "law-2"},
                "조문": {
                    "조문단위": [
                        {"조문내용": "wrong record"}
                    ]
                },
            }
        }

    with pytest.raises(
        LawGoKrCorpusSourceError,
        match="no publishable records",
    ):
        _source(fake_get).fetch_page(checkpoint_before=None)

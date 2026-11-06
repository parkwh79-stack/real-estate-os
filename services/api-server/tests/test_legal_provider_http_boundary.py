from __future__ import annotations

from io import BytesIO

import pytest

import legal_intelligence.providers as providers
from legal_intelligence.models import (
    DataClassification,
    LegalResourceKind,
    LegalSearchQuery,
    ProviderStatus,
)
from legal_intelligence.resilience import (
    ProviderQuarantineRecord,
    RetryPolicy,
)


class _Response:
    def __init__(
        self,
        body: bytes,
        *,
        headers: dict[str, str],
    ) -> None:
        self.headers = headers
        self._body = BytesIO(body)
        self.read_sizes: list[int] = []

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return self._body.read(size)


class _Quarantine:
    def __init__(self) -> None:
        self.items: list[ProviderQuarantineRecord] = []

    def store(self, record: ProviderQuarantineRecord) -> None:
        self.items.append(record)


def _read_json(
    response: _Response,
    *,
    max_response_bytes: int = 1024,
    max_depth: int = 16,
    max_collection_items: int = 100,
    max_nodes: int = 1000,
):
    return providers._read_bounded_json_mapping(
        response,
        allowed_media_families=("json",),
        max_response_bytes=max_response_bytes,
        json_max_depth=max_depth,
        json_max_collection_items=max_collection_items,
        json_max_nodes=max_nodes,
    )


def test_content_length_is_rejected_before_reading_body():
    response = _Response(
        b'{"ok":true}',
        headers={
            "Content-Type": "application/json",
            "Content-Length": "4097",
        },
    )

    with pytest.raises(
        providers.ProviderResponseBoundaryError,
        match="byte limit",
    ) as error:
        _read_json(response, max_response_bytes=4096)

    assert error.value.reason_code == "PROVIDER_RESPONSE_TOO_LARGE"
    assert response.read_sizes == []


def test_chunked_body_is_bounded_even_without_content_length():
    response = _Response(
        b'{"payload":"too-large"}',
        headers={"Content-Type": "application/problem+json"},
    )

    with pytest.raises(providers.ProviderResponseBoundaryError) as error:
        _read_json(response, max_response_bytes=8)

    assert error.value.reason_code == "PROVIDER_RESPONSE_TOO_LARGE"
    assert response.read_sizes
    assert max(response.read_sizes) <= 9


@pytest.mark.parametrize(
    ("content_type", "families", "accepted"),
    (
        ("application/json; charset=utf-8", ("json",), True),
        ("application/ld+json", ("json",), True),
        ("application/xml", ("xml",), True),
        ("application/atom+xml", ("xml",), True),
        ("text/html", ("json",), False),
        ("application/xml", ("json",), False),
    ),
)
def test_response_media_type_is_endpoint_contract_specific(
    content_type,
    families,
    accepted,
):
    response = _Response(
        b"{}",
        headers={
            "Content-Type": content_type,
            "Content-Length": "2",
        },
    )

    if accepted:
        assert providers._read_bounded_response_bytes(
            response,
            allowed_media_families=families,
            max_response_bytes=100,
        ) == b"{}"
    else:
        with pytest.raises(
            providers.ProviderResponseBoundaryError
        ) as error:
            providers._read_bounded_response_bytes(
                response,
                allowed_media_families=families,
                max_response_bytes=100,
            )
        assert (
            error.value.reason_code
            == "PROVIDER_RESPONSE_CONTENT_TYPE_REJECTED"
        )
        assert response.read_sizes == []


@pytest.mark.parametrize(
    ("body", "reason_code", "limits"),
    (
        (
            b'{"a":{"b":{"c":{"d":1}}}}',
            "PROVIDER_RESPONSE_JSON_DEPTH_LIMIT",
            {"max_depth": 3, "max_collection_items": 10, "max_nodes": 100},
        ),
        (
            b'{"items":[1,2,3]}',
            "PROVIDER_RESPONSE_JSON_COLLECTION_LIMIT",
            {"max_depth": 10, "max_collection_items": 2, "max_nodes": 100},
        ),
        (
            b'{"a":1,"b":2,"c":3}',
            "PROVIDER_RESPONSE_JSON_NODE_LIMIT",
            {"max_depth": 10, "max_collection_items": 10, "max_nodes": 3},
        ),
        (
            b'{"duplicate":1,"duplicate":2}',
            "PROVIDER_RESPONSE_JSON_DUPLICATE_KEY",
            {"max_depth": 10, "max_collection_items": 10, "max_nodes": 100},
        ),
        (
            b'{"invalid":NaN}',
            "PROVIDER_RESPONSE_JSON_NON_FINITE",
            {"max_depth": 10, "max_collection_items": 10, "max_nodes": 100},
        ),
    ),
)
def test_json_structure_limits_fail_closed(body, reason_code, limits):
    response = _Response(
        body,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        },
    )
    with pytest.raises(providers.ProviderResponseBoundaryError) as error:
        _read_json(response, **limits)

    assert error.value.reason_code == reason_code


def test_parser_recursion_error_is_normalized_to_depth_boundary_error():
    body = (
        '{"root":'
        + "[" * 2000
        + "0"
        + "]" * 2000
        + "}"
    ).encode()
    response = _Response(
        body,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        },
    )

    with pytest.raises(providers.ProviderResponseBoundaryError) as error:
        _read_json(
            response,
            max_response_bytes=len(body),
            max_depth=256,
            max_collection_items=1,
            max_nodes=5000,
        )

    assert error.value.reason_code == "PROVIDER_RESPONSE_JSON_DEPTH_LIMIT"


def test_injected_mapping_rejects_non_string_keys_and_cycles():
    with pytest.raises(providers.ProviderResponseBoundaryError) as key_error:
        providers._validate_json_mapping_payload(
            {1: "not-json-key"},
            max_response_bytes=1024,
            json_max_depth=10,
            json_max_collection_items=10,
            json_max_nodes=100,
        )
    assert key_error.value.reason_code == "PROVIDER_RESPONSE_JSON_KEY_INVALID"

    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    with pytest.raises(providers.ProviderResponseBoundaryError) as cycle_error:
        providers._validate_json_mapping_payload(
            cyclic,
            max_response_bytes=1024,
            json_max_depth=10,
            json_max_collection_items=10,
            json_max_nodes=100,
        )
    assert (
        cycle_error.value.reason_code
        == "PROVIDER_RESPONSE_JSON_GRAPH_INVALID"
    )


def test_direct_provider_quarantines_boundary_failure_without_retrying():
    calls = 0
    quarantine = _Quarantine()

    def deep_payload(_url: str, _timeout: float):
        nonlocal calls
        calls += 1
        return {"one": {"two": {"three": "rejected"}}}

    provider = providers.LawGoKrDirectProvider(
        oc="secret@example.com",
        http_get_json=deep_payload,
        max_response_bytes=1024,
        json_max_depth=2,
        json_max_collection_items=10,
        json_max_nodes=100,
        quarantine_sink=quarantine,
        retry_policy=RetryPolicy(max_attempts=3),
    )

    result = provider.search(
        LegalSearchQuery(
            text="Housing Act",
            resource_kinds=(LegalResourceKind.STATUTE,),
            data_classification=DataClassification.PUBLIC,
        )
    )

    assert result.status == ProviderStatus.FAILED
    assert calls == 1
    assert result.diagnostics[0].code == (
        "PROVIDER_RESPONSE_JSON_DEPTH_LIMIT"
    )
    assert "secret@example.com" not in result.diagnostics[0].message
    assert [item.reason_code for item in quarantine.items] == [
        "PROVIDER_RESPONSE_JSON_DEPTH_LIMIT"
    ]


def test_mcp_provider_quarantines_injected_boundary_failure_without_retrying():
    calls = 0
    quarantine = _Quarantine()

    def deep_payload(
        _url: str,
        _payload: dict[str, object],
        _timeout: float,
    ):
        nonlocal calls
        calls += 1
        return {"one": {"two": {"three": "rejected"}}}

    provider = providers.McpBridgeProvider(
        url="http://127.0.0.1:8787",
        post_json=deep_payload,
        max_response_bytes=1024,
        json_max_depth=2,
        json_max_collection_items=10,
        json_max_nodes=100,
        quarantine_sink=quarantine,
        retry_policy=RetryPolicy(max_attempts=3),
    )

    result = provider.search(
        LegalSearchQuery(
            text="Housing Act",
            resource_kinds=(LegalResourceKind.STATUTE,),
            data_classification=DataClassification.PUBLIC,
        )
    )

    assert result.status == ProviderStatus.FAILED
    assert calls == 1
    assert result.diagnostics[0].code == (
        "PROVIDER_RESPONSE_JSON_DEPTH_LIMIT"
    )
    assert [item.reason_code for item in quarantine.items] == [
        "PROVIDER_RESPONSE_JSON_DEPTH_LIMIT"
    ]

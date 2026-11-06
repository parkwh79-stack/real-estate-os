"""Strict Law.go.kr list/detail/history response boundaries.

The National Law Information Center has resource-specific JSON envelopes.
This module validates those envelopes before a payload reaches a semantic
normalizer.  It deliberately preserves the complete source object in
``raw_payload``: the boundary proves shape and identity, but does not claim
that a search result or an unversioned detail response proves historical
applicability.

The contracts are fixture-backed and versioned.  A production endpoint may
only be enabled after its official response fixture is added to the contract
suite; unknown envelopes and ambiguous identities fail closed.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import LegalResourceKind


class LawGoKrExternalContractError(ValueError):
    """An external response did not satisfy its registered target contract."""


class LawGoKrExternalListRow(BaseModel):
    """Identity projection of one official search-list row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_kind: LegalResourceKind
    target: str = Field(pattern=r"^[A-Za-z0-9]+$")
    official_identifier: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=1_000)
    source_locator: str | None = Field(default=None, max_length=4_000)
    raw_payload: Mapping[str, Any]

    @field_validator("raw_payload")
    @classmethod
    def _raw_payload_is_json_object(
        cls,
        value: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        _assert_json_object(value, "list row")
        return dict(value)


class LawGoKrExternalListPage(BaseModel):
    """Validated, target-specific view of one official list envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_kind: LegalResourceKind
    target: str = Field(pattern=r"^[A-Za-z0-9]+$")
    contract_version: str = Field(pattern=r"^law-go-kr-[a-z0-9-]+-v[0-9]+$")
    envelope_key: str = Field(min_length=1, max_length=100)
    page: int = Field(ge=1)
    display: int = Field(ge=1, le=100)
    total_count: int = Field(ge=0)
    rows: tuple[LawGoKrExternalListRow, ...]
    raw_payload: Mapping[str, Any]

    @field_validator("raw_payload")
    @classmethod
    def _raw_payload_is_json_object(
        cls,
        value: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        _assert_json_object(value, "list response")
        return dict(value)


class LawGoKrExternalDetailResponse(BaseModel):
    """Validated detail envelope bound to the requested official identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_kind: LegalResourceKind
    target: str = Field(pattern=r"^[A-Za-z0-9]+$")
    contract_version: str = Field(pattern=r"^law-go-kr-[a-z0-9-]+-v[0-9]+$")
    envelope_key: str = Field(min_length=1, max_length=100)
    requested_official_identifier: str = Field(min_length=1, max_length=300)
    response_official_identifier: str = Field(min_length=1, max_length=300)
    raw_payload: Mapping[str, Any]

    @field_validator("raw_payload")
    @classmethod
    def _raw_payload_is_json_object(
        cls,
        value: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        _assert_json_object(value, "detail response")
        return dict(value)


class LawGoKrExternalHistoryVersion(BaseModel):
    """One version identity from an official history response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    official_version_identifier: str = Field(min_length=1, max_length=300)
    effective_from: date | None = None
    effective_to: date | None = None
    raw_payload: Mapping[str, Any]

    @field_validator("raw_payload")
    @classmethod
    def _raw_payload_is_json_object(
        cls,
        value: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        _assert_json_object(value, "history version")
        return dict(value)


class LawGoKrExternalHistoryResponse(BaseModel):
    """Validated history envelope.

    This model only records official version/effective-date fields present in
    the response.  Selection of the version applicable to a requested date is
    a separate semantic-normalization responsibility.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_kind: LegalResourceKind
    target: str = Field(pattern=r"^[A-Za-z0-9]+$")
    contract_version: str = Field(pattern=r"^law-go-kr-[a-z0-9-]+-v[0-9]+$")
    envelope_key: str = Field(min_length=1, max_length=100)
    requested_official_identifier: str = Field(min_length=1, max_length=300)
    versions: tuple[LawGoKrExternalHistoryVersion, ...] = Field(min_length=1)
    raw_payload: Mapping[str, Any]

    @field_validator("raw_payload")
    @classmethod
    def _raw_payload_is_json_object(
        cls,
        value: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        _assert_json_object(value, "history response")
        return dict(value)


class LawGoKrTargetBoundaryContract(BaseModel):
    """Versioned key contract for one Law.go.kr resource family."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_kind: LegalResourceKind
    list_target: str = Field(pattern=r"^[A-Za-z0-9]+$")
    detail_target: str = Field(pattern=r"^[A-Za-z0-9]+$")
    list_contract_version: str
    detail_contract_version: str
    history_contract_version: str
    list_envelope_keys: tuple[str, ...] = Field(min_length=1)
    list_row_keys: tuple[str, ...] = Field(min_length=1)
    identifier_keys: tuple[str, ...] = Field(min_length=1)
    title_keys: tuple[str, ...] = Field(min_length=1)
    locator_keys: tuple[str, ...] = ()
    detail_envelope_keys: tuple[str, ...] = Field(min_length=1)
    history_envelope_keys: tuple[str, ...] = Field(min_length=1)
    history_row_keys: tuple[str, ...] = Field(min_length=1)
    history_version_identifier_keys: tuple[str, ...] = Field(min_length=1)
    effective_from_keys: tuple[str, ...] = ()
    effective_to_keys: tuple[str, ...] = ()


def _contract(
    *,
    kind: LegalResourceKind,
    list_target: str,
    detail_target: str,
    list_envelopes: tuple[str, ...],
    row_keys: tuple[str, ...],
    identifiers: tuple[str, ...],
    titles: tuple[str, ...],
    locators: tuple[str, ...],
    detail_envelopes: tuple[str, ...],
    history_envelopes: tuple[str, ...],
    history_rows: tuple[str, ...],
    version_ids: tuple[str, ...],
    effective_from: tuple[str, ...] = (),
    effective_to: tuple[str, ...] = (),
) -> LawGoKrTargetBoundaryContract:
    slug = kind.value.replace("_", "-")
    return LawGoKrTargetBoundaryContract(
        resource_kind=kind,
        list_target=list_target,
        detail_target=detail_target,
        list_contract_version=f"law-go-kr-{slug}-list-v1",
        detail_contract_version=f"law-go-kr-{slug}-detail-v1",
        history_contract_version=f"law-go-kr-{slug}-history-v1",
        list_envelope_keys=list_envelopes,
        list_row_keys=row_keys,
        identifier_keys=identifiers,
        title_keys=titles,
        locator_keys=locators,
        detail_envelope_keys=detail_envelopes,
        history_envelope_keys=history_envelopes,
        history_row_keys=history_rows,
        history_version_identifier_keys=version_ids,
        effective_from_keys=effective_from,
        effective_to_keys=effective_to,
    )


_LAW_IDENTIFIERS = ("법령ID", "법령일련번호")
_LAW_TITLES = ("법령명한글",)
_LAW_LOCATORS = ("법령상세링크",)

LAW_GO_KR_BOUNDARY_CONTRACTS: Mapping[
    LegalResourceKind,
    LawGoKrTargetBoundaryContract,
] = {
    LegalResourceKind.STATUTE: _contract(
        kind=LegalResourceKind.STATUTE,
        list_target="law",
        detail_target="law",
        list_envelopes=("LawSearch",),
        row_keys=("law",),
        identifiers=_LAW_IDENTIFIERS,
        titles=_LAW_TITLES,
        locators=_LAW_LOCATORS,
        detail_envelopes=("법령",),
        history_envelopes=("LawHistory",),
        history_rows=("lawHistory", "법령연혁"),
        version_ids=("법령일련번호", "법령ID"),
        effective_from=("시행일자",),
        effective_to=("시행종료일자",),
    ),
    LegalResourceKind.PROVISION: _contract(
        kind=LegalResourceKind.PROVISION,
        list_target="law",
        detail_target="law",
        list_envelopes=("LawSearch",),
        row_keys=("law",),
        identifiers=_LAW_IDENTIFIERS,
        titles=_LAW_TITLES,
        locators=_LAW_LOCATORS,
        detail_envelopes=("법령",),
        history_envelopes=("LawHistory",),
        history_rows=("lawHistory", "법령연혁"),
        version_ids=("법령일련번호", "법령ID"),
        effective_from=("시행일자",),
        effective_to=("시행종료일자",),
    ),
    LegalResourceKind.PRECEDENT: _contract(
        kind=LegalResourceKind.PRECEDENT,
        list_target="prec",
        detail_target="prec",
        list_envelopes=("PrecSearch",),
        row_keys=("prec",),
        identifiers=("판례일련번호",),
        titles=("사건명",),
        locators=("판례상세링크",),
        detail_envelopes=("PrecService", "판례"),
        history_envelopes=("PrecHistory",),
        history_rows=("precHistory", "판례연혁"),
        version_ids=("판례일련번호", "판결문일련번호"),
        effective_from=("선고일자",),
    ),
    LegalResourceKind.INTERPRETATION: _contract(
        kind=LegalResourceKind.INTERPRETATION,
        list_target="expc",
        detail_target="expc",
        list_envelopes=("ExpcSearch",),
        row_keys=("expc",),
        identifiers=("법령해석례일련번호",),
        titles=("안건명",),
        locators=("법령해석례상세링크",),
        detail_envelopes=("ExpcService", "법령해석례"),
        history_envelopes=("ExpcHistory",),
        history_rows=("expcHistory", "법령해석례연혁"),
        version_ids=("법령해석례일련번호", "안건번호"),
        effective_from=("회신일자",),
    ),
    LegalResourceKind.ADMINISTRATIVE_RULE: _contract(
        kind=LegalResourceKind.ADMINISTRATIVE_RULE,
        list_target="admrul",
        detail_target="admrul",
        list_envelopes=("AdmRulSearch",),
        row_keys=("admrul",),
        identifiers=("행정규칙ID", "행정규칙일련번호"),
        titles=("행정규칙명",),
        locators=("행정규칙상세링크",),
        detail_envelopes=("AdmRulService", "행정규칙"),
        history_envelopes=("AdmRulHistory",),
        history_rows=("admrulHistory", "행정규칙연혁"),
        version_ids=("행정규칙일련번호", "행정규칙ID"),
        effective_from=("시행일자", "발령일자"),
        effective_to=("시행종료일자",),
    ),
    LegalResourceKind.LOCAL_ORDINANCE: _contract(
        kind=LegalResourceKind.LOCAL_ORDINANCE,
        list_target="ordin",
        detail_target="ordin",
        list_envelopes=("OrdinSearch",),
        row_keys=("ordin",),
        identifiers=("자치법규ID", "자치법규일련번호"),
        titles=("자치법규명",),
        locators=("자치법규상세링크",),
        detail_envelopes=("OrdinService", "자치법규"),
        history_envelopes=("OrdinHistory",),
        history_rows=("ordinHistory", "자치법규연혁"),
        version_ids=("자치법규일련번호", "자치법규ID"),
        effective_from=("시행일자",),
        effective_to=("시행종료일자",),
    ),
    LegalResourceKind.CONSTITUTIONAL_DECISION: _contract(
        kind=LegalResourceKind.CONSTITUTIONAL_DECISION,
        list_target="detc",
        detail_target="detc",
        list_envelopes=("DetcSearch",),
        row_keys=("detc",),
        identifiers=("헌재결정례일련번호", "결정문일련번호"),
        titles=("사건명",),
        locators=("헌재결정례상세링크",),
        detail_envelopes=("DetcService", "헌재결정례"),
        history_envelopes=("DetcHistory",),
        history_rows=("detcHistory", "헌재결정례연혁"),
        version_ids=("헌재결정례일련번호", "결정문일련번호"),
        effective_from=("종국일자", "선고일자"),
    ),
    LegalResourceKind.LEGAL_TERM: _contract(
        kind=LegalResourceKind.LEGAL_TERM,
        list_target="lstrmAI",
        detail_target="lstrm",
        list_envelopes=("LstrmAISearch", "LstrmSearch"),
        row_keys=("법령용어", "lstrmAI", "lstrm"),
        identifiers=("법령용어ID",),
        titles=("법령용어명",),
        locators=("법령용어상세링크",),
        detail_envelopes=("LstrmService", "법령용어"),
        history_envelopes=("LstrmHistory",),
        history_rows=("lstrmHistory", "법령용어연혁"),
        version_ids=("법령용어ID", "법령용어버전ID"),
        effective_from=("적용일자",),
        effective_to=("종료일자",),
    ),
}


class LawGoKrExternalPayloadAdapter:
    """Parse official-style payloads through a target-specific boundary."""

    def __init__(self, resource_kind: LegalResourceKind) -> None:
        try:
            self.contract = LAW_GO_KR_BOUNDARY_CONTRACTS[resource_kind]
        except KeyError as exc:
            raise LawGoKrExternalContractError(
                f"no Law.go.kr boundary contract for {resource_kind.value}"
            ) from exc

    def parse_list(
        self,
        payload: Mapping[str, Any],
        *,
        requested_page: int,
        requested_display: int,
    ) -> LawGoKrExternalListPage:
        root_key, envelope = _registered_envelope(
            payload,
            self.contract.list_envelope_keys,
            phase="list",
        )
        raw_rows, _row_key = _first_registered_value(
            envelope,
            self.contract.list_row_keys,
            phase="list rows",
        )
        if isinstance(raw_rows, Mapping):
            raw_rows = [raw_rows]
        if not isinstance(raw_rows, list):
            raise LawGoKrExternalContractError(
                "Law.go.kr list rows must be an array or object"
            )

        page = _optional_non_negative_int(
            envelope,
            ("page", "pageNo", "페이지"),
        )
        display = _optional_non_negative_int(
            envelope,
            ("numOfRows", "display", "목록수"),
        )
        total_count = _optional_non_negative_int(
            envelope,
            ("totalCnt", "totalCount", "total", "총건수"),
        )
        if page is not None and page != requested_page:
            raise LawGoKrExternalContractError(
                "Law.go.kr list response page does not match the request"
            )
        if display is not None and display != requested_display:
            raise LawGoKrExternalContractError(
                "Law.go.kr list response display does not match the request"
            )
        if total_count is None:
            raise LawGoKrExternalContractError(
                "Law.go.kr list response has no total count"
            )

        rows: list[LawGoKrExternalListRow] = []
        for raw_row in raw_rows:
            if not isinstance(raw_row, Mapping):
                raise LawGoKrExternalContractError(
                    "Law.go.kr list contains a non-object row"
                )
            official_identifier = _first_text(
                raw_row,
                self.contract.identifier_keys,
            )
            title = _first_text(raw_row, self.contract.title_keys)
            if not official_identifier or not title:
                raise LawGoKrExternalContractError(
                    "Law.go.kr list row has no registered identity/title"
                )
            rows.append(
                LawGoKrExternalListRow(
                    resource_kind=self.contract.resource_kind,
                    target=self.contract.list_target,
                    official_identifier=official_identifier,
                    title=title,
                    source_locator=_first_text(
                        raw_row,
                        self.contract.locator_keys,
                    ),
                    raw_payload=dict(raw_row),
                )
            )

        return LawGoKrExternalListPage(
            resource_kind=self.contract.resource_kind,
            target=self.contract.list_target,
            contract_version=self.contract.list_contract_version,
            envelope_key=root_key,
            page=requested_page,
            display=requested_display,
            total_count=total_count,
            rows=tuple(rows),
            raw_payload=dict(payload),
        )

    def parse_detail(
        self,
        payload: Mapping[str, Any],
        *,
        requested_official_identifier: str,
    ) -> LawGoKrExternalDetailResponse:
        root_key, envelope = _registered_envelope(
            payload,
            self.contract.detail_envelope_keys,
            phase="detail",
        )
        identities = _recursive_registered_texts(
            envelope,
            self.contract.identifier_keys,
        )
        if not identities:
            raise LawGoKrExternalContractError(
                "Law.go.kr detail response has no registered identity"
            )
        if requested_official_identifier not in identities:
            raise LawGoKrExternalContractError(
                "Law.go.kr detail identity does not match the list row"
            )
        return LawGoKrExternalDetailResponse(
            resource_kind=self.contract.resource_kind,
            target=self.contract.detail_target,
            contract_version=self.contract.detail_contract_version,
            envelope_key=root_key,
            requested_official_identifier=requested_official_identifier,
            response_official_identifier=requested_official_identifier,
            raw_payload=dict(payload),
        )

    def parse_history(
        self,
        payload: Mapping[str, Any],
        *,
        requested_official_identifier: str,
    ) -> LawGoKrExternalHistoryResponse:
        root_key, envelope = _registered_envelope(
            payload,
            self.contract.history_envelope_keys,
            phase="history",
        )
        history_identities = _recursive_registered_texts(
            envelope,
            self.contract.identifier_keys,
        )
        if requested_official_identifier not in history_identities:
            raise LawGoKrExternalContractError(
                "Law.go.kr history identity does not match the list row"
            )
        raw_versions, _row_key = _first_registered_value(
            envelope,
            self.contract.history_row_keys,
            phase="history rows",
        )
        if isinstance(raw_versions, Mapping):
            raw_versions = [raw_versions]
        if not isinstance(raw_versions, list) or not raw_versions:
            raise LawGoKrExternalContractError(
                "Law.go.kr history response has no version rows"
            )
        versions: list[LawGoKrExternalHistoryVersion] = []
        for raw_version in raw_versions:
            if not isinstance(raw_version, Mapping):
                raise LawGoKrExternalContractError(
                    "Law.go.kr history contains a non-object row"
                )
            version_identifier = _first_text(
                raw_version,
                self.contract.history_version_identifier_keys,
            )
            if not version_identifier:
                raise LawGoKrExternalContractError(
                    "Law.go.kr history row has no version identity"
                )
            versions.append(
                LawGoKrExternalHistoryVersion(
                    official_version_identifier=version_identifier,
                    effective_from=_first_date(
                        raw_version,
                        self.contract.effective_from_keys,
                    ),
                    effective_to=_first_date(
                        raw_version,
                        self.contract.effective_to_keys,
                    ),
                    raw_payload=dict(raw_version),
                )
            )
        return LawGoKrExternalHistoryResponse(
            resource_kind=self.contract.resource_kind,
            target=self.contract.detail_target,
            contract_version=self.contract.history_contract_version,
            envelope_key=root_key,
            requested_official_identifier=requested_official_identifier,
            versions=tuple(versions),
            raw_payload=dict(payload),
        )


def _registered_envelope(
    payload: Mapping[str, Any],
    registered_keys: tuple[str, ...],
    *,
    phase: str,
) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        raise LawGoKrExternalContractError(
            f"Law.go.kr {phase} response must be an object"
        )
    matches = [
        (key, payload[key])
        for key in registered_keys
        if key in payload
    ]
    if len(matches) != 1:
        raise LawGoKrExternalContractError(
            f"Law.go.kr {phase} response must contain exactly one "
            "registered envelope"
        )
    key, value = matches[0]
    if not isinstance(value, Mapping):
        raise LawGoKrExternalContractError(
            f"Law.go.kr {phase} envelope must be an object"
        )
    return key, value


def _first_registered_value(
    value: Mapping[str, Any],
    keys: tuple[str, ...],
    *,
    phase: str,
) -> tuple[Any, str]:
    matches = [(key, value[key]) for key in keys if key in value]
    if len(matches) != 1:
        raise LawGoKrExternalContractError(
            f"Law.go.kr {phase} must contain exactly one registered field"
        )
    key, result = matches[0]
    return result, key


def _recursive_registered_texts(
    value: Any,
    keys: tuple[str, ...],
) -> frozenset[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key in keys and nested is not None and str(nested).strip():
                found.add(str(nested).strip())
            found.update(_recursive_registered_texts(nested, keys))
    elif isinstance(value, list):
        for nested in value:
            found.update(_recursive_registered_texts(nested, keys))
    return frozenset(found)


def _first_text(
    value: Mapping[str, Any],
    keys: tuple[str, ...],
) -> str | None:
    for key in keys:
        candidate = value.get(key)
        if candidate is not None and str(candidate).strip():
            return str(candidate).strip()
    return None


def _optional_non_negative_int(
    value: Mapping[str, Any],
    keys: tuple[str, ...],
) -> int | None:
    raw = _first_text(value, keys)
    if raw is None:
        return None
    digits = "".join(character for character in raw if character.isdigit())
    if not digits:
        raise LawGoKrExternalContractError(
            f"Law.go.kr numeric field {keys[0]} is invalid"
        )
    return int(digits)


def _first_date(
    value: Mapping[str, Any],
    keys: tuple[str, ...],
) -> date | None:
    raw = _first_text(value, keys)
    if raw is None:
        return None
    digits = "".join(character for character in raw if character.isdigit())
    if len(digits) < 8:
        raise LawGoKrExternalContractError(
            f"Law.go.kr date field {keys[0]} is invalid"
        )
    try:
        return date(
            int(digits[:4]),
            int(digits[4:6]),
            int(digits[6:8]),
        )
    except ValueError as exc:
        raise LawGoKrExternalContractError(
            f"Law.go.kr date field {keys[0]} is invalid"
        ) from exc


def _assert_json_object(value: Mapping[str, Any], label: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    try:
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON-compatible") from exc


__all__ = [
    "LAW_GO_KR_BOUNDARY_CONTRACTS",
    "LawGoKrExternalContractError",
    "LawGoKrExternalDetailResponse",
    "LawGoKrExternalHistoryResponse",
    "LawGoKrExternalHistoryVersion",
    "LawGoKrExternalListPage",
    "LawGoKrExternalListRow",
    "LawGoKrExternalPayloadAdapter",
    "LawGoKrTargetBoundaryContract",
]

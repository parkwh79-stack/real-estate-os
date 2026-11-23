"""Evidence for the school-site charge on an explicitly chosen business date."""
from datetime import date, datetime, timedelta, timezone
import re
from typing import Annotated, Literal

from psycopg import Error as PostgresError
from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from legal_intelligence.models import DataClassification, LegalResourceKind, SourceAuthority
from use_cases.cash_flow_dependencies import financial_input_hash
from use_cases.legal_intelligence import build_legal_intelligence_service
from use_cases.statutory_cost_review import official_statutory_url, statutory_cost_query_plan

EvidenceHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Locator = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
SchoolEventKind = Literal[
    "approval_application", "approval", "amended_approval", "construction_start",
    "sale_approval_application", "sale_notice", "sale", "completion",
]
_PROVISIONS = re.compile(r"^제5조(?:의2)?(?:$|[①-⑳(제])")


class SchoolSiteEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["unknown", "actual", "assumed"] = "unknown"
    kind: SchoolEventKind | None = None
    eventDate: str | None = None
    archiveId: EvidenceHash | None = None
    location: Locator | None = None

    @model_validator(mode="after")
    def validate_date_and_source(self):
        if self.status == "unknown":
            if any(value is not None for value in (self.kind, self.eventDate, self.archiveId, self.location)):
                raise ValueError("사건일 미확인 상태에서는 날짜와 근거를 비워 주세요.")
            return self
        if not self.kind or not self.eventDate:
            raise ValueError("검토 기준 사건과 날짜를 입력하세요.")
        try:
            day = date.fromisoformat(self.eventDate)
            if day.isoformat() != self.eventDate:
                raise ValueError
        except ValueError as exc:
            raise ValueError("사건일을 연-월-일로 입력하세요.") from exc
        if self.status == "actual":
            today = datetime.now(timezone(timedelta(hours=9))).date()
            if day > today or not self.archiveId or not self.location:
                raise ValueError("실제 사건일은 오늘까지의 날짜와 보관한 증빙 위치가 필요합니다.")
        elif self.archiveId is not None or self.location is not None:
            raise ValueError("가정 사건일은 실제 사건 증빙과 구별해 입력하세요.")
        return self


class SchoolSiteLawSelection(SchoolSiteEvent):
    expectedBasisHash: EvidenceHash | None = None


def resolve_school_site_law_basis(event: SchoolSiteEvent, source_locator: dict | None = None) -> dict:
    """Freeze the official source for a review date, separately from applicability."""
    if event.status == "actual" and not source_locator:
        raise ValueError("사업 사건 증빙을 현재 프로젝트에서 확인하세요.")
    basis = {
        "schemaVersion": "realytics-school-site-law-basis-v1",
        "event": {
            "status": event.status,
            "kind": event.kind,
            "date": event.eventDate,
            "sourceLocator": source_locator,
        },
        "editionStatus": "event_unconfirmed",
        "applicability": "unknown",
        "edition": None,
    }
    if event.eventDate:
        basis["editionStatus"], basis["edition"] = _school_law_edition(date.fromisoformat(event.eventDate))
    return {**basis, "basisHash": financial_input_hash(basis)}


def _school_law_edition(as_of: date) -> tuple[str, dict | None]:
    title = statutory_cost_query_plan()[0]["query"]
    try:
        service = build_legal_intelligence_service(retain_provider_operations=False)
        lookup = service.search(
            query=title, resource_kinds=(LegalResourceKind.STATUTE, LegalResourceKind.PROVISION),
            as_of=as_of, limit=20, data_classification=DataClassification.PUBLIC,
        )
        hits = [
            hit for hit in lookup.authoritative_hits
            if "".join(hit.title.split()) == "".join(title.split())
            and hit.is_authoritative_evidence
            and hit.attestation.source_authority == SourceAuthority.LAW_GO_KR
            and hit.attestation.is_applicability_verified
            and hit.attestation.requested_as_of_date == as_of
        ]
        editions = {(hit.attestation.official_resource_id, hit.attestation.official_version_id) for hit in hits}
        if len(editions) > 1:
            return "edition_conflict", None
        if not hits:
            return "edition_unconfirmed", None
        official_id, version_id = next(iter(editions))
        resource = service.get_resource(official_id, as_of=as_of, provision_limit=2000)
    except (PostgresError, RuntimeError, OSError):
        return "edition_unavailable", None
    if not resource:
        return "edition_unconfirmed", None

    source_url = official_statutory_url(resource.get("sourceUrl"))
    identifiers = resource.get("officialIdentifiers") or {}
    digest = resource.get("normalizedContentSha256")
    attested_digests = {hit.content.get("normalizedContentSha256") for hit in hits}
    start = resource.get("effectiveFrom")
    end = resource.get("effectiveTo")
    day = as_of.isoformat()
    if (
        resource.get("sourceAuthority") != SourceAuthority.LAW_GO_KR.value
        or resource.get("verificationStatus") != "verified_snapshot"
        or identifiers.get("officialIdentifier") != official_id
        or identifiers.get("officialVersionIdentifier") != version_id
        or "".join(str(resource.get("title") or "").split()) != "".join(title.split())
        or resource.get("asOfDate") != day
        or not isinstance(start, str) or start > day or (end is not None and (not isinstance(end, str) or end < day))
        or not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        or attested_digests != {digest}
        or not source_url
    ):
        return "edition_unconfirmed", None

    provisions = []
    for provision in resource.get("provisions") or ():
        path = provision.get("displayPath") or provision.get("provisionIdentifier") or ""
        digest_provision = provision.get("contentHash")
        if not isinstance(digest_provision, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest_provision):
            continue
        kind = provision.get("provisionType")
        if kind != "addendum" and not (kind == "article" and _PROVISIONS.match("".join(path.split()))):
            continue
        provisions.append({
            "path": path,
            "contentHash": digest_provision,
            "url": official_statutory_url(provision.get("locator")) or source_url,
        })
    return "edition_verified", {
        "title": title, "officialId": official_id, "officialVersionId": version_id,
        "effectiveFrom": start, "effectiveTo": end, "contentHash": digest,
        "url": source_url, "provisions": sorted(provisions, key=lambda row: (row["path"], row["contentHash"]))[:30],
    }

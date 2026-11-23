from datetime import date
from types import SimpleNamespace as NS

import pytest

from legal_intelligence.models import DataClassification, SourceAuthority
from use_cases import school_site_law_basis as module


class LawRuntime:
    def __init__(self):
        self.calls = []
        self.mismatched_detail = False
        self.conflicting_editions = False

    def search(self, *, query, as_of, data_classification, **kwargs):
        assert query == "학교용지 확보 등에 관한 특례법"
        assert data_classification is DataClassification.PUBLIC
        self.calls.append(as_of)
        ids = [self._version(as_of)]
        if self.conflicting_editions:
            ids.append("conflicting-version")
        return NS(authoritative_hits=[
            NS(title=query, is_authoritative_evidence=True, content={
                "normalizedContentSha256": "sha256:" + ("a" if version == "pre-amendment" else "b") * 64,
            }, attestation=NS(
                source_authority=SourceAuthority.LAW_GO_KR, is_applicability_verified=True,
                requested_as_of_date=as_of, official_resource_id="act", official_version_id=version,
            )) for version in ids
        ])

    @staticmethod
    def _version(as_of):
        return "pre-amendment" if as_of < date(2026, 12, 3) else "amendment"

    def get_resource(self, resource_id, *, as_of, provision_limit):
        assert resource_id == "act"
        earlier = self._version(as_of) == "pre-amendment"
        return {
            "title": "학교용지 확보 등에 관한 특례법",
            "sourceAuthority": "law_go_kr",
            "verificationStatus": "verified_snapshot",
            "officialIdentifiers": {"officialIdentifier": "act", "officialVersionIdentifier": (
                "unrelated-edition" if self.mismatched_detail else self._version(as_of)
            )},
            "normalizedContentSha256": "sha256:" + ("a" if earlier else "b") * 64,
            "asOfDate": as_of.isoformat(),
            "effectiveFrom": "2025-01-01" if earlier else "2026-12-03",
            "effectiveTo": "2026-12-02" if earlier else None,
            "sourceUrl": "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=123&OC=internal",
            "provisions": [
                {"displayPath": "제5조의2", "provisionType": "article", "contentHash": "sha256:" + "c" * 64},
                {"displayPath": "부칙", "provisionType": "addendum", "contentHash": "sha256:" + "d" * 64},
                {"displayPath": "제6조", "provisionType": "article", "contentHash": "sha256:" + "e" * 64},
            ],
        }


def test_event_edition_changes_at_boundary_and_unconfirmed_skips_lookup(monkeypatch):
    runtime = LawRuntime()
    monkeypatch.setattr(module, "build_legal_intelligence_service", lambda **kwargs: runtime)
    missing = module.resolve_school_site_law_basis(module.SchoolSiteEvent())
    assert missing["editionStatus"] == "event_unconfirmed"
    assert not runtime.calls
    before, after = [
        module.resolve_school_site_law_basis(
            module.SchoolSiteEvent(status="assumed", kind="sale_approval_application", eventDate=day),
        ) for day in ("2026-12-02", "2026-12-03")
    ]
    assert runtime.calls == [date(2026, 12, 2), date(2026, 12, 3)]
    assert before["basisHash"] != after["basisHash"]
    assert before["edition"]["officialVersionId"] == "pre-amendment"
    assert after["edition"]["officialVersionId"] == "amendment"
    assert before["edition"]["effectiveTo"] == "2026-12-02"
    assert after["edition"]["url"].endswith("?lsiSeq=123")
    assert {row["path"] for row in after["edition"]["provisions"]} == {"제5조의2", "부칙"}
    assert before["applicability"] == after["applicability"] == "unknown"


@pytest.mark.parametrize(("failure", "expected"), [
    ("mismatched_detail", "edition_unconfirmed"), ("conflicting_editions", "edition_conflict"),
])
def test_mixed_editions_remain_unconfirmed(monkeypatch, failure, expected):
    runtime = LawRuntime()
    setattr(runtime, failure, True)
    monkeypatch.setattr(module, "build_legal_intelligence_service", lambda **kwargs: runtime)
    result = module.resolve_school_site_law_basis(
        module.SchoolSiteEvent(status="assumed", kind="approval", eventDate="2026-12-03"),
    )
    assert result["editionStatus"] == expected
    assert result["edition"] is None


def test_public_reference_is_saved_separately_when_the_corpus_has_no_match(monkeypatch):
    reference = {
        "officialVersionId": "286359",
        "effectiveFrom": "2026-12-03",
        "url": "https://www.law.go.kr/LSW/lsInfoP.do?efYd=20261203&lsiSeq=286359",
        "amendmentUrl": "https://www.law.go.kr/LSW/lsInfoP.do?efYd=20261203&lsiSeq=286359&viewCls=lsRvsDocInfoR",
    }
    empty_corpus = NS(search=lambda **kwargs: NS(authoritative_hits=[]))
    monkeypatch.setattr(module, "build_legal_intelligence_service", lambda **kwargs: empty_corpus)
    monkeypatch.setattr(module, "school_site_public_reference", lambda day: reference)
    event = module.SchoolSiteEvent(status="assumed", kind="sale_notice_approval", eventDate="2026-12-03")

    basis = module.resolve_school_site_law_basis(event)
    assert basis["publicReference"] == reference
    assert basis["editionStatus"] == "edition_unconfirmed"
    assert basis["edition"] is None and basis["applicability"] == "unknown"

    monkeypatch.setattr(module, "school_site_public_reference", lambda day: None)
    assert basis["basisHash"] != module.resolve_school_site_law_basis(event)["basisHash"]

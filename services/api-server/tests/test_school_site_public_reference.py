from datetime import date, datetime

import httpx
import pytest

from use_cases import school_site_public_reference as module


def history_row(version, published, notice, starts):
    return (
        f"""<a onclick="lsViewLsHst2('{version}', '{published}', '{notice}', """
        f"""'{starts}', 'N', '0');return false;">시행연혁</a>"""
    )


_HISTORY = (
    history_row("289395", "20260908", "21901", "20270909")
    + history_row("267343", "20241220", "20568", "20250621")
    + history_row("286359", "20260602", "21722", "20261203")
)


@pytest.fixture
def official_viewer(monkeypatch):
    requests = []
    settings = {"history": _HISTORY, "wrong_law": False, "redirect": False}

    def send(request):
        requests.append(request.url)
        assert request.url.host == "www.law.go.kr"
        if request.url.path.endswith("lsHstListR.do"):
            assert request.url.params["lsId"] == "000894"
            if settings["redirect"]:
                return httpx.Response(302, headers={"Location": "https://example.com/"})
            html = settings["history"]
        else:
            version = request.url.params["lsiSeq"]
            start = request.url.params["efYd"]
            day = date.fromisoformat(start)
            notice = {"267343": "20568", "286359": "21722", "289395": "21901"}[version]
            law = "000999" if settings["wrong_law"] else "000894"
            html = f"""<input id="lsId" value="{law}" />
                <input id="frm_lsiSeq" value="{version}" />
                <input id="frm_efYd" value="{start}" />
                <button>학교용지 확보 등에 관한 특례법 [시행 {day.year}. {day.month}. {day.day}.] [법률 제{notice}호, 일부개정]</button>"""
        return httpx.Response(200, text=html, headers={"Content-Type": "text/html; charset=UTF-8"})

    class FixedDateTime:
        @staticmethod
        def now(tz):
            return datetime(2026, 11, 23, tzinfo=tz)

    real_client = httpx.Client
    monkeypatch.setattr(module, "datetime", FixedDateTime)
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(send), **kwargs))
    return settings, requests


@pytest.mark.parametrize(("as_of", "version", "start"), [
    ("2026-12-02", "267343", "2025-06-21"),
    ("2026-12-03", "286359", "2026-12-03"),
    ("2027-09-09", "289395", "2027-09-09"),
])
def test_reference_matches_published_effective_boundaries(official_viewer, as_of, version, start):
    _, requests = official_viewer
    reference = module.school_site_public_reference(date.fromisoformat(as_of))
    assert reference["officialVersionId"] == version
    assert reference["effectiveFrom"] == start
    assert reference["url"] == str(requests[1])
    assert reference["amendmentUrl"] == reference["url"] + "&viewCls=lsRvsDocInfoR"
    assert all(as_of.replace("-", "") not in str(url) or url == requests[1] for url in requests)


@pytest.mark.parametrize("as_of", ["2025-06-20", "2027-09-10"])
def test_outside_known_dates_never_extrapolates(official_viewer, as_of):
    _, requests = official_viewer
    assert module.school_site_public_reference(date.fromisoformat(as_of)) is None
    assert len(requests) == 1


@pytest.mark.parametrize("problem", ["wrong_law", "redirect", "same_effective_day", "oversized", "changed_markup"])
def test_public_reference_fails_closed(official_viewer, problem):
    settings, requests = official_viewer
    if problem in {"wrong_law", "redirect"}:
        settings[problem] = True
    elif problem == "same_effective_day":
        settings["history"] += history_row("286358", "20260602", "21721", "20261203")
    elif problem == "oversized":
        settings["history"] += "a" * module._MAX_BYTES
    else:
        settings["history"] = settings["history"].replace("return false;", "newFunction();")
    assert module.school_site_public_reference(date(2026, 12, 3)) is None
    assert len(requests) == (2 if problem == "wrong_law" else 1)

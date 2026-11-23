"""Read-only links to published school-site law editions, outside the sealed corpus."""
from datetime import date, datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
import re

import httpx

_LAW_ID = "000894"
_TITLE = "학교용지 확보 등에 관한 특례법"
_BASE = "https://www.law.go.kr/LSW"
_HISTORY = f"{_BASE}/lsHstListR.do?lsId={_LAW_ID}&chrClsCd=010202&ancYnChk=0"
_MAX_BYTES = 512 * 1024
_HISTORY_ITEM = re.compile(
    r"lsViewLsHst2\('([0-9]+)',\s*'([0-9]{8})',\s*'([0-9]+)',"
    r"\s*'([0-9]{8})',\s*'[YN]',\s*'0'\);return false;"
)
_TAG = re.compile(r"<[^>]*>")


class _OfficialPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.versions = set()
        self.invalid_history = False
        self.identifiers = {}

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        action = attrs.get("onclick") or ""
        if tag == "a" and action.startswith("lsViewLsHst2("):
            match = _HISTORY_ITEM.fullmatch(action.strip())
            if match is None:
                self.invalid_history = True
            else:
                self.versions.add(match.groups())
        if tag == "input" and attrs.get("id") in {"lsId", "frm_lsiSeq", "frm_efYd"}:
            self.identifiers[attrs["id"]] = attrs.get("value")


def _html(client: httpx.Client, url: str) -> str:
    with client.stream("GET", url) as response:
        response.raise_for_status()
        if not response.headers.get("Content-Type", "").lower().startswith("text/html"):
            raise ValueError("Expected official HTML")
        data = bytearray()
        for chunk in response.iter_bytes(chunk_size=8192):
            if len(data) + len(chunk) > _MAX_BYTES:
                raise ValueError("Official HTML exceeds size limit")
            data.extend(chunk)
    return data.decode("utf-8")


def _pick_edition(history: _OfficialPage, as_of: date, today: date):
    if history.invalid_history:
        return None
    versions = [
        (date.fromisoformat(start), date.fromisoformat(published), version, notice)
        for version, published, notice, start in history.versions
    ]
    if any(published > start or published > today for start, published, _, _ in versions):
        return None
    prior = [row for row in versions if row[0] <= as_of]
    if not prior:
        return None
    start = max(row[0] for row in prior)
    matching = [row for row in prior if row[0] == start]
    next_start = min((row[0] for row in versions if row[0] > start), default=None)
    if len(matching) != 1 or (next_start is None and as_of > max(today, start)):
        return None
    _, _, version, notice = matching[0]
    return version, notice, start


def school_site_public_reference(as_of: date) -> dict | None:
    """Use the public viewer only for a human-readable reference to review."""
    try:
        with httpx.Client(timeout=3.0, follow_redirects=False, trust_env=False) as client:
            history = _OfficialPage()
            history.feed(_html(client, _HISTORY))
            today = datetime.now(timezone(timedelta(hours=9))).date()
            selected = _pick_edition(history, as_of, today)
            if selected is None:
                return None
            version, notice, start = selected
            compact = start.strftime("%Y%m%d")
            url = f"{_BASE}/lsInfoP.do?efYd={compact}&lsiSeq={version}"
            detail = _html(client, url)
            page = _OfficialPage()
            page.feed(detail)
            if page.identifiers != {"lsId": _LAW_ID, "frm_lsiSeq": version, "frm_efYd": compact}:
                return None
            visible_date = f"{start.year}. {start.month}. {start.day}."
            headline = _TAG.sub("", unescape(detail))
            if f"{_TITLE} [시행 {visible_date}] [법률 제{int(notice)}호," not in headline:
                return None
    except (httpx.HTTPError, ValueError):
        return None

    return {
        "officialVersionId": version,
        "effectiveFrom": start.isoformat(),
        "url": url,
        "amendmentUrl": f"{url}&viewCls=lsRvsDocInfoR",
    }

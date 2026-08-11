"""CMSearch 分页：长 lookback 时需取末页，避免漏掉最新录像段。"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

from hikvision_status import HikvisionNVR


def _item(st: str, et: str, uri: str = "rtsp://x/t") -> ET.Element:
    xml = f"""<searchMatchItem>
      <timeSpan><startTime>{st}</startTime><endTime>{et}</endTime></timeSpan>
      <mediaSegmentDescriptor><playbackURI>{uri}</playbackURI></mediaSegmentDescriptor>
    </searchMatchItem>"""
    return ET.fromstring(xml)


def _page_xml(items: List[ET.Element], total: int) -> str:
    body = "".join(ET.tostring(i, encoding="unicode") for i in items)
    return f"""<?xml version="1.0"?>
<CMSearchResult>
  <numOfMatches>{len(items)}</numOfMatches>
  <totalMatches>{total}</totalMatches>
  <matchList>{body}</matchList>
</CMSearchResult>"""


def test_cmsearch_collect_fetches_last_page(monkeypatch):
    """首页只有旧段、总数>页大小时，应再请求末页并合并到最新段。"""
    n = HikvisionNVR(ip="1.1.1.1", username="a", password="b", quiet=True)
    now = datetime(2026, 8, 11, 3, 0, 0, tzinfo=timezone.utc)
    old_items = [
        _item("2026-08-10T02:00:00Z", "2026-08-10T04:00:00Z", "rtsp://old1"),
        _item("2026-08-10T05:00:00Z", "2026-08-10T07:00:00Z", "rtsp://old2"),
    ]
    new_items = [
        _item("2026-08-11T01:00:00Z", "2026-08-11T04:00:00Z", "rtsp://new"),
    ]
    calls: List[Tuple[int, int]] = []

    def fake_post(endpoint, body, **kw):
        assert endpoint == "/ContentMgmt/search"
        # 解析 position / maxResults
        pos = 0
        max_r = 40
        if "<searchResultPostion>" in body:
            pos = int(body.split("<searchResultPostion>")[1].split("<")[0])
        if "<maxResults>" in body:
            max_r = int(body.split("<maxResults>")[1].split("<")[0])
        calls.append((pos, max_r))
        if pos == 0:
            return 200, _page_xml(old_items, total=12)
        # last page
        return 200, _page_xml(new_items, total=12)

    monkeypatch.setattr(n, "_post", fake_post)
    # freeze "now" indirectly via search window only — collect itself doesn't use now
    code, matches = n._cmsearch_collect(
        "101",
        now - timedelta(days=1),
        now + timedelta(minutes=5),
        max_results=2,  # 强制小页，触发末页
    )
    assert code == 200
    assert len(calls) == 2
    assert calls[0][0] == 0
    assert calls[1][0] == 10  # total 12 - page 2
    uris = [(m.findtext(".//playbackURI") or "") for m in matches]
    assert "rtsp://old1" in uris
    assert "rtsp://new" in uris


def test_search_track_recent_uses_newest_segment(monkeypatch):
    """合并末页后，正在录的最新段应判「正常」而非「仅有较早片段」。"""
    n = HikvisionNVR(ip="1.1.1.1", username="a", password="b", quiet=True)
    now = datetime.now(timezone.utc)
    # 旧段 + 覆盖 now 的新段
    old_st = (now - timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_et = (now - timedelta(hours=18)).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_st = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_et = (now + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def fake_collect(track_id, start, end, **kw):
        items = [
            _item(old_st, old_et, "rtsp://old"),
            _item(new_st, new_et, "rtsp://live"),
        ]
        return 200, items

    monkeypatch.setattr(n, "_cmsearch_collect", fake_collect)
    res = n._search_track_recent("101", 1440)
    assert res["ok"] is True
    assert res["status"] == "正常"
    assert res["playback_uri"] == "rtsp://live"
    assert "较早" not in (res.get("detail") or "")


def test_search_track_recent_short_window_fallback(monkeypatch):
    """长窗只返回旧段时，近 90 分短窗回退应捞到正在录的段。"""
    n = HikvisionNVR(ip="1.1.1.1", username="a", password="b", quiet=True)
    now = datetime.now(timezone.utc)
    old_st = (now - timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_et = (now - timedelta(hours=18)).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_st = (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_et = (now + timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    calls = []

    def fake_collect(track_id, start, end, **kw):
        span = (end - start).total_seconds()
        calls.append(span)
        # 长窗（~24h）只给旧段；短窗（~90min）给新段
        if span > 3600 * 3:
            return 200, [_item(old_st, old_et, "rtsp://old")]
        return 200, [_item(new_st, new_et, "rtsp://live")]

    monkeypatch.setattr(n, "_cmsearch_collect", fake_collect)
    res = n._search_track_recent("101", 1440)
    assert len(calls) >= 2
    assert res["ok"] is True
    assert res["playback_uri"] == "rtsp://live"


def test_cmsearch_page_uses_unique_search_id(monkeypatch):
    import re

    ids = []

    def fake_post(endpoint, body, **kw):
        m = re.search(r"<searchID>([^<]+)</searchID>", body)
        assert m
        ids.append(m.group(1))
        return 200, _page_xml([], total=0)

    n = HikvisionNVR(ip="1.1.1.1", username="a", password="b", quiet=True)
    monkeypatch.setattr(n, "_post", fake_post)
    now = datetime.now(timezone.utc)
    n._cmsearch_page("101", now - timedelta(hours=1), now, max_results=5, position=0)
    n._cmsearch_page("101", now - timedelta(hours=1), now, max_results=5, position=0)
    assert len(ids) == 2
    assert ids[0] != ids[1]
    assert len(ids[0]) >= 32

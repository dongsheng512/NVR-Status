"""物理通道解析与按通道去重。

Hikvision 的 Track id 常见编码为「物理通道 + 两位码流号」(101=通道1主码流,
102=通道1子码流)。若不统一映射, 同一摄像头的主/子码流会被当成两个通道:
通道表出现重复行、指标分母被放大、子码流行名称显示「未知」。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from hikvision_status import HikvisionNVR


def _track(tid: str, *, channel: str = "", record: bool = True, mode: str = "CMR") -> ET.Element:
    ch = f"<Channel>{channel}</Channel>" if channel else ""
    rec = "true" if record else "false"
    return ET.fromstring(
        f"""<Track>
  <id>{tid}</id>
  {ch}
  <DefaultRecordingMode>{mode}</DefaultRecordingMode>
  <Actions>
    <ActionRecordingMode>{mode}</ActionRecordingMode>
    <Record>{rec}</Record>
  </Actions>
</Track>"""
    )


def _tracks_root(tracks) -> ET.Element:
    root = ET.Element("TrackList")
    for t in tracks:
        root.append(t)
    return root


# ---------- _physical_channel 映射 ----------


def test_physical_channel_maps_main_and_sub_stream_to_same_channel():
    """101/102 同属通道 1；201/202 同属通道 2。"""
    assert HikvisionNVR._physical_channel(_track("101")) == "1"
    assert HikvisionNVR._physical_channel(_track("102")) == "1"
    assert HikvisionNVR._physical_channel(_track("201")) == "2"
    assert HikvisionNVR._physical_channel(_track("202")) == "2"


def test_physical_channel_prefers_src_channel():
    tr = ET.fromstring(
        "<Track><id>102</id><SrcChannel>7</SrcChannel></Track>"
    )
    assert HikvisionNVR._physical_channel(tr) == "7"


def test_physical_channel_keeps_plain_and_large_ids():
    """通道号本身 >=100 或低位非 01/02 时不得被误折算。"""
    assert HikvisionNVR._physical_channel(_track("1")) == "1"
    assert HikvisionNVR._physical_channel(_track("100")) == "100"
    assert HikvisionNVR._physical_channel(_track("111")) == "111"
    assert HikvisionNVR._physical_channel(_track("未知")) == "未知"


# ---------- get_recording_status 去重 ----------


def _client(tracks, cameras):
    n = HikvisionNVR(ip="1.1.1.1", username="a", password="b", quiet=True)
    root = _tracks_root(tracks)
    n._parse = lambda endpoint, tag="": root if "record/tracks" in endpoint else None
    n.get_cameras = lambda: cameras
    n.check_disk_recording = False
    n.deep_av_check = False
    return n


def test_recording_status_merges_sub_stream_tracks():
    """一通道主+子两条 Track → 合并为一行, 保留主码流(编号更小)。"""
    n = _client(
        [_track("101"), _track("102"), _track("201"), _track("202")],
        [
            {"id": "1", "名称": "前门", "在线": "true"},
            {"id": "2", "名称": "后门", "在线": "true"},
        ],
    )
    recs = n.get_recording_status()
    assert len(recs) == 2
    assert [r["通道"] for r in recs] == ["1", "2"]
    # 保留的是主码流
    assert [r["track_id"] for r in recs] == ["101", "201"]
    # 名称/在线按物理通道命中, 不再出现「未知」
    assert [r["名称"] for r in recs] == ["前门", "后门"]
    assert [r["在线"] for r in recs] == ["true", "true"]


def test_recording_status_keeps_one_row_per_channel_when_single_track():
    """每通道只有一条 Track 时不受影响(保持原样)。"""
    n = _client(
        [_track("101"), _track("201")],
        [{"id": "1", "名称": "A", "在线": "true"}, {"id": "2", "名称": "B", "在线": "true"}],
    )
    recs = n.get_recording_status()
    assert [r["通道"] for r in recs] == ["1", "2"]
    assert [r["track_id"] for r in recs] == ["101", "201"]


def test_recording_status_does_not_merge_unparsed_channels():
    """通道号解析不出来时不得合并, 否则会丢摄像头。"""
    n = _client(
        [_track("未知"), _track("abc"), _track("101")],
        [{"id": "1", "名称": "A", "在线": "true"}],
    )
    recs = n.get_recording_status()
    assert len(recs) == 3


def test_disk_results_are_keyed_per_record_not_per_track_id(monkeypatch):
    """同一 track_id 出现多次时, 落盘结论不得互相覆盖。

    固件偶发返回重复/「未知」track_id; 若以 track_id 作结果键,
    两条记录会拿到同一份（后写入的）落盘结论。
    """
    n = _client(
        [_track("未知"), _track("未知")],
        [{"id": "1", "名称": "A", "在线": "true"}],
    )
    n.check_disk_recording = True
    n.search_workers = 1  # 单线程 → 完成顺序确定
    seq = {"i": 0}

    def fake_recent(track_id, lookback):
        seq["i"] += 1
        return {
            "ok": True,
            "status": "正常" if seq["i"] == 1 else "异常",
            "detail": f"call{seq['i']}",
        }

    monkeypatch.setattr(n, "_search_track_recent", fake_recent)
    recs = n.get_recording_status()
    assert len(recs) == 2
    assert sorted(r["落盘状态"] for r in recs) == ["异常", "正常"]

"""结果区指标分母口径。

摄像头数是 physical channel 数，而录像/落盘/音频计数基于 records（Track），
两者不应混用——否则一通道多 Track 的机型会出现 "5/4" 这类失真比例。
"""

from __future__ import annotations

from ui.panels.results_panel import ResultsPanel


def _record(ch: int) -> dict:
    return {
        "通道": ch,
        "名称": f"cam{ch}",
        "在线": "true",
        "录像含音频": True,
        "落盘状态": "正常",
        "录像是否正常": "正常",
    }


def _render(
    qapp,
    *,
    cameras: int,
    tracks: int,
    stats: dict,
    deep: bool = False,
    records: list | None = None,
) -> ResultsPanel:
    panel = ResultsPanel()
    panel.render_result(
        {
            "device_name": "NVR1",
            "info": {"型号": "X", "固件版本": "1"},
            "health": {"健康状态": "良好", "统计": stats, "预警信息": []},
            "records": (
                records if records is not None else [_record(i) for i in range(1, tracks + 1)]
            ),
            "deep_av": deep,
        }
    )
    return panel


def test_metric_denominator_follows_record_population(qapp):
    """分母必须与分子同源: 4 个摄像头但 6 条记录时,

    录像/落盘/音频卡用 6 作分母(与计数同源, 不会出现 5/4),
    摄像头在线卡仍用摄像头数 4。
    """
    panel = _render(
        qapp,
        cameras=4,
        tracks=6,
        stats={
            "摄像头在线": 4,
            "摄像头离线": 0,
            "摄像头总数": 4,
            "录像正常": 5,
            "录像异常": 1,
            "落盘正常": 6,
            "落盘异常": 0,
            "含音频": 6,
            "落盘已检查": True,
            "录像已检查": True,
        },
    )
    cards = panel._metric_cards
    assert cards["online"].value.text() == "4/4"
    assert cards["record"].value.text() == "5/6"
    assert cards["disk"].value.text() == "6/6"
    assert cards["audio"].value.text() == "6/6"
    assert cards["audio"]._tone == "ok"


def test_audio_tone_warns_when_not_all_tracks_have_audio(qapp):
    """通道未全部开启音频时不得误判为 ok（分母按通道数）。"""
    panel = _render(
        qapp,
        cameras=4,
        tracks=6,
        stats={
            "摄像头在线": 4,
            "摄像头离线": 0,
            "摄像头总数": 4,
            "录像正常": 6,
            "录像异常": 0,
            "落盘正常": 6,
            "落盘异常": 0,
            "含音频": 4,
            "落盘已检查": True,
            "录像已检查": True,
        },
    )
    assert panel._metric_cards["audio"].value.text() == "4/6"
    assert panel._metric_cards["audio"]._tone == "warn"


def test_metric_shows_unchecked_when_disk_search_skipped(qapp):
    """快速模式（未查落盘）时录像/近期录像显示「未检查」而非 0/n。"""
    panel = _render(
        qapp,
        cameras=2,
        tracks=2,
        stats={
            "摄像头在线": 2,
            "摄像头离线": 0,
            "摄像头总数": 2,
            "录像跳过": 2,
            "落盘跳过": 2,
            "含音频": 2,
            "落盘已检查": False,
            "录像已检查": False,
        },
    )
    cards = panel._metric_cards
    assert cards["record"].value.text() == "未检查"
    assert cards["disk"].value.text() == "未检查"
    assert cards["record"]._tone == "muted"


# ---------- 音频卡片：空集不告警 / 深度抽检走实测口径 ----------


def test_audio_card_not_alerted_on_empty_result(qapp):
    """空结果集（0 通道）时音频卡不得变成黄色告警。"""
    panel = _render(
        qapp,
        cameras=0,
        tracks=0,
        stats={
            "摄像头在线": 0,
            "摄像头离线": 0,
            "摄像头总数": 0,
            "含音频": 0,
            "落盘已检查": True,
            "录像已检查": True,
        },
        records=[],
    )
    card = panel._metric_cards["audio"]
    assert card.value.text() == "未检查"
    assert card._tone == "muted"


def _deep_record(ch: int, achk: str) -> dict:
    rec = _record(ch)
    rec["视频抽检"] = "正常"
    rec["音频抽检"] = achk
    return rec


def test_audio_card_uses_measured_column_in_deep_mode(qapp):
    """深度抽检时音频卡展示实测音轨，而非录像配置开关。"""
    records = [
        _deep_record(1, "正常"),
        _deep_record(2, "正常"),
        _deep_record(3, "跳过"),  # 配置未开音频
        _deep_record(4, "跳过"),
    ]
    panel = _render(
        qapp,
        cameras=4,
        tracks=4,
        stats={
            "摄像头在线": 4,
            "摄像头离线": 0,
            "摄像头总数": 4,
            "录像正常": 4,
            "录像异常": 0,
            "落盘正常": 4,
            "落盘异常": 0,
            "含音频": 2,  # 配置口径：只有 2 路开了音频
            "落盘已检查": True,
            "录像已检查": True,
        },
        deep=True,
        records=records,
    )
    card = panel._metric_cards["audio"]
    assert card.title.text() == "音频实测"
    assert card.value.text() == "2/2"  # 分母是「实际抽到流」的通道，跳过不计
    assert card._tone == "ok"


def test_audio_card_measured_flags_missing_track_as_bad(qapp):
    """实测发现无音轨必须报红，且标题与配置口径区分开。"""
    records = [
        _deep_record(1, "正常"),
        _deep_record(2, "异常"),  # 无音轨
    ]
    panel = _render(
        qapp,
        cameras=2,
        tracks=2,
        stats={
            "摄像头在线": 2,
            "摄像头离线": 0,
            "摄像头总数": 2,
            "录像正常": 2,
            "录像异常": 0,
            "落盘正常": 2,
            "落盘异常": 0,
            "含音频": 2,  # 配置说都开了音频——但实测有一条拉不到
            "落盘已检查": True,
            "录像已检查": True,
        },
        deep=True,
        records=records,
    )
    card = panel._metric_cards["audio"]
    assert card.title.text() == "音频实测"
    assert card.value.text() == "1/2"
    assert card._tone == "bad"
    assert "无音轨 1" in card.toolTip()


def test_audio_card_measured_warns_on_silence_and_unknown(qapp):
    """静音警告与「未确认」按 warn 处理，不掩盖也不升级为红。"""
    records = [
        _deep_record(1, "正常"),
        _deep_record(2, "警告"),
        _deep_record(3, "未知"),
    ]
    panel = _render(
        qapp,
        cameras=3,
        tracks=3,
        stats={
            "摄像头在线": 3,
            "摄像头离线": 0,
            "摄像头总数": 3,
            "录像正常": 3,
            "录像异常": 0,
            "落盘正常": 3,
            "落盘异常": 0,
            "含音频": 3,
            "落盘已检查": True,
            "录像已检查": True,
        },
        deep=True,
        records=records,
    )
    card = panel._metric_cards["audio"]
    assert card.value.text() == "2/3"
    assert card._tone == "warn"
    assert "未确认 1" in card.toolTip()


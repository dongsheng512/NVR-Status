"""摄像头在线卡：把「通道检测异常」与「在线状态未确认」轻量呈现。

数值仍只显示 online/total（形态不变、不新增卡片）；通道级异常走 tone=warn，
未确认只进 tooltip —— 避免把「未确认」当成异常塞进数值。
"""

from __future__ import annotations

from tests.test_metric_denominator import _render


def _stats(**over) -> dict:
    s = {
        "摄像头在线": 4,
        "摄像头离线": 0,
        "摄像头总数": 4,
        "摄像头状态未确认": 0,
        "通道检测异常": 0,
    }
    s.update(over)
    return s


def test_online_card_tone_warn_on_channel_detect_anomaly(qapp):
    panel = _render(qapp, cameras=4, tracks=4, stats=_stats(通道检测异常=2))
    card = panel._metric_cards["online"]
    assert card.value.text() == "4/4"
    assert card._tone == "warn"
    assert "通道检测异常" in card.toolTip()


def test_online_card_tooltip_mentions_unconfirmed(qapp):
    panel = _render(
        qapp, cameras=4, tracks=4, stats=_stats(摄像头在线=3, 摄像头状态未确认=1)
    )
    card = panel._metric_cards["online"]
    assert card.value.text() == "3/4"
    assert card._tone == "ok"
    assert "未确认" in card.toolTip()


def test_online_card_bad_on_offline_and_no_tip(qapp):
    panel = _render(qapp, cameras=4, tracks=4, stats=_stats(摄像头在线=3, 摄像头离线=1))
    card = panel._metric_cards["online"]
    assert card._tone == "bad"
    assert card.toolTip() == ""

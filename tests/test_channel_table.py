"""A4-2 通道表筛选 / 排序 / 状态判定最小回归（offscreen）。"""

from __future__ import annotations

from PySide6.QtCore import Qt

from ui.widgets.channel_table import ChannelFilterProxy, ChannelTableModel, row_tag


def _rec(ch, online="true", disk="正常", record="正常", vchk="跳过", achk="跳过"):
    return {
        "通道": ch,
        "名称": f"cam{ch}",
        "在线": online,
        "录像含音频": True,
        "落盘状态": disk,
        "落盘详情": "",
        "录像是否正常": record,
        "视频抽检": vchk,
        "音频抽检": achk,
    }


def _setup(records, deep=False):
    model = ChannelTableModel()
    model.set_records(records, deep)
    proxy = ChannelFilterProxy()
    proxy.setSourceModel(model)
    return model, proxy


def test_only_offline_filter(qapp):
    recs = [_rec(1), _rec(2, online="false"), _rec(3)]
    _model, proxy = _setup(recs)
    assert proxy.rowCount() == 3
    proxy.set_only_offline(True)
    assert proxy.rowCount() == 1


def test_only_abnormal_filter(qapp):
    recs = [
        _rec(1),
        _rec(2, disk="异常"),
        _rec(3, record="未配置"),
        _rec(4, record="未知", disk="跳过"),
    ]
    _model, proxy = _setup(recs)
    proxy.set_only_abnormal(True)
    assert proxy.rowCount() == 2  # 通道 2(异常) + 通道 3(未配置)


def test_sort_by_channel_number(qapp):
    recs = [_rec(10), _rec(2), _rec(1)]
    _model, proxy = _setup(recs)
    proxy.sort(0, Qt.SortOrder.AscendingOrder)
    rows = []
    for r in range(proxy.rowCount()):
        src = proxy.mapToSource(proxy.index(r, 0))
        rows.append(_model.record_at(src.row())["通道"])
    assert rows == [1, 2, 10]  # 按通道号数值而非字典序


def test_row_tag_judgement():
    assert row_tag(_rec(1), False) == "ok"
    assert row_tag(_rec(1, online="false"), False) == "error"
    assert row_tag(_rec(1, disk="异常"), False) == "error"
    assert row_tag(_rec(1, record="未知", disk="跳过"), False) == "muted"
    # 深抽检：视频异常 → error
    assert row_tag(_rec(1, vchk="异常"), True) == "error"
    # 深抽检：音频警告 → warn
    assert row_tag(_rec(1, achk="警告"), True) == "warn"

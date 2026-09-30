"""A4-2 通道表筛选 / 排序 / 状态判定最小回归（offscreen）。"""

from __future__ import annotations

from PySide6.QtCore import Qt

from ui.widgets.channel_table import (
    NAME_MIN_WIDTH,
    ChannelFilterProxy,
    ChannelTableModel,
    ChannelTableView,
    row_tag,
)


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


def test_name_column_stays_readable_in_deep_mode(qapp):
    view = ChannelTableView()
    view.resize(720, 360)
    view.show()
    qapp.processEvents()
    recs = [_rec(i) for i in range(1, 8)]
    recs[0]["名称"] = "前端相机-1"
    view.set_records(recs, deep=True)
    qapp.processEvents()
    name_idx = 1
    assert view.view.columnWidth(name_idx) >= NAME_MIN_WIDTH
    header = view.model.headerData(name_idx, Qt.Orientation.Horizontal)
    assert header == "名称"


# ---------- 名称列伸缩模式：只在需要时切换，避免 resize 抖动 ----------


def _spy_resize_mode(view):
    """记录 setSectionResizeMode 调用。

    注意：不能用 monkeypatch 打在 PySide6 实例上 —— teardown 时它会对
    shiboken 对象做 delattr，直接段错误。改为手动还原（见 finally）。
    """
    header = view.view.horizontalHeader()
    calls = []
    orig = header.setSectionResizeMode

    def spy(idx, mode):
        calls.append(mode)
        return orig(idx, mode)

    header.setSectionResizeMode = spy
    return calls, orig, header


def test_name_column_mode_switches_only_on_real_transition(qapp):
    """空间不足 / 宽裕 各只切一次模式；重复调用同一状态不得再触碰表头。

    回归：原先每次 resize 都无条件 setSectionResizeMode，而该调用会触发表头
    重排 → 又回调 resizeEvent → 列宽抖动。

    用「其他列的总宽」来驱动上下文，避免 patch Qt 对象（会段错误）：
    其他列撑到 5000px → avail 必然 < NAME_MIN_WIDTH；收到 48px → 必然宽裕。
    """
    from PySide6.QtWidgets import QHeaderView

    view = ChannelTableView()
    view.resize(1000, 360)
    view.show()
    qapp.processEvents()
    view.set_records([_rec(1)], deep=False)
    qapp.processEvents()

    name_idx = view.model.columns().index("name")
    calls, orig, header = _spy_resize_mode(view)

    def set_others(width: int) -> None:
        for i in range(len(view.model.columns())):
            if i != name_idx:
                view.view.setColumnWidth(i, width)

    try:
        set_others(5000)  # 空间不足
        view._name_mode = None
        view._balance_name_column()
        assert calls == [QHeaderView.ResizeMode.Interactive]
        assert view.view.columnWidth(name_idx) == NAME_MIN_WIDTH
        # 幂等：同一状态下重复调用不得再设置
        view._balance_name_column()
        view._balance_name_column()
        assert len(calls) == 1

        set_others(48)  # 空间宽裕
        view._balance_name_column()
        assert calls == [
            QHeaderView.ResizeMode.Interactive,
            QHeaderView.ResizeMode.Stretch,
        ]
        view._balance_name_column()
        assert len(calls) == 2  # 仍幂等

        set_others(5000)  # 再变窄，仍只切一次
        view._balance_name_column()
        assert calls[-1] == QHeaderView.ResizeMode.Interactive
        assert len(calls) == 3
    finally:
        header.setSectionResizeMode = orig


def test_repeated_resize_does_not_thrash_header(qapp):
    """真实窗口下连续 resize 不应触发大量表头模式切换。"""
    view = ChannelTableView()
    view.resize(900, 360)
    view.show()
    qapp.processEvents()
    view.set_records([_rec(i) for i in range(1, 6)], deep=False)
    qapp.processEvents()
    calls, orig, header = _spy_resize_mode(view)
    try:
        for w in (900, 880, 860, 840, 820, 800, 780, 760):
            view.resize(w, 360)
            qapp.processEvents()
    finally:
        header.setSectionResizeMode = orig
    assert len(calls) <= 1, f"同一宽度区间内不应反复切换模式，实际 {len(calls)} 次"

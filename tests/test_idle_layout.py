"""初始态布局：空结果区、巡检按钮层级、左栏主路径、macOS 标题栏。"""

from __future__ import annotations

from ui.panels.left_panel import LeftPanel
from ui.panels.results_panel import IDLE_SUMMARY, ResultsPanel, summarize_warn_lines
from ui.panels.log_panel import LogPanel
from ui.widgets.channel_table import ChannelTableView


def test_results_idle_hides_empty_chrome(qapp):
    panel = ResultsPanel()
    assert panel.summary_label.text() == IDLE_SUMMARY
    assert panel.warn_box.isHidden()
    assert panel.device_sub_label.isHidden()
    assert panel._metric_cards["health"].value.text() == "—"
    table = panel.channel_table
    assert panel.idle_hint.text() == "巡检后显示通道列表"
    assert not panel.idle_hint.isHidden()
    assert table.isHidden()
    assert table.placeholder.text() == "暂无通道数据"
    assert table.toolbar_host.isHidden()


def test_results_show_warn_and_toolbar_after_render(qapp):
    panel = ResultsPanel()
    panel.render_result(
        {
            "device_name": "NVR1",
            "info": {"型号": "X", "固件版本": "1"},
            "health": {
                "健康状态": "良好",
                "统计": {
                    "摄像头在线": 1,
                    "摄像头离线": 0,
                    "摄像头总数": 1,
                    "录像正常": 1,
                    "录像异常": 0,
                    "落盘正常": 1,
                    "落盘异常": 0,
                    "含音频": 1,
                    "落盘已检查": True,
                    "录像已检查": True,
                },
                "预警信息": [],
            },
            "records": [
                {
                    "通道": 1,
                    "名称": "cam1",
                    "在线": "true",
                    "录像含音频": True,
                    "落盘状态": "正常",
                    "录像是否正常": "正常",
                }
            ],
            "deep_av": False,
        }
    )
    assert panel.warn_box.isHidden()
    assert not panel.warn_toggle.isHidden()
    assert "▶" in panel.warn_toggle.text()
    assert panel.idle_hint.isHidden()
    assert not panel.channel_table.isHidden()
    assert not panel.channel_table.toolbar_host.isHidden()
    assert "健康：良好" in panel.summary_label.text()

    panel.warn_toggle.click()
    assert not panel.warn_box.isHidden()
    panel.warn_toggle.click()
    assert panel.warn_box.isHidden()

    panel.clear_result()
    assert panel.last_result is None
    assert panel.summary_label.text() == IDLE_SUMMARY
    assert panel.warn_box.isHidden()
    assert panel.warn_toggle.isHidden()
    assert not panel.idle_hint.isHidden()
    assert panel.channel_table.isHidden()


def test_summarize_warn_lines_prefers_alerts():
    text = summarize_warn_lines(
        [
            "预警：",
            "  • 7块硬盘空间已满/即将用尽（循环覆盖推断已开启，将覆盖旧录像继续录）",
            "循环覆盖：推断已开启（存在使用率≥95% 且状态仍为 ok 的硬盘）",
            "硬盘：hdd1 ok 100.0% | hdd2 ok 100.0%",
        ]
    )
    assert "7块硬盘空间已满" in text
    assert "循环覆盖已开启" in text
    assert "hdd1" not in text
    assert summarize_warn_lines(["预警：无"]) == "预警：无"


def test_log_panel_collapse(qapp):
    panel = LogPanel()
    assert panel.expanded
    assert not panel.log_box.isHidden()
    panel.set_expanded(False)
    assert not panel.expanded
    assert panel.log_box.isHidden()
    panel.toggle()
    assert panel.expanded
    assert not panel.log_box.isHidden()


def test_left_panel_scan_hierarchy_and_order(qapp):
    panel = LeftPanel()
    assert panel.btn_scan_quick.property("role") == "primary"
    assert panel.btn_scan_deep.property("role") != "primary"
    assert panel.btn_cancel.isHidden()
    assert not panel.btn_del_dev.isEnabled()

    panel.resize(400, 900)
    panel.show()
    qapp.processEvents()

    def _y(w):
        return w.mapTo(panel, w.rect().topLeft()).y()

    assert _y(panel.cmb_scan_target) < _y(panel.btn_toggle_devices)
    assert _y(panel.btn_toggle_devices) < _y(panel.btn_scan_quick)
    assert _y(panel.btn_scan_quick) < _y(panel.btn_toggle_settings)
    assert _y(panel.btn_history) > _y(panel.btn_toggle_settings)

    panel.set_cancel_enabled(True)
    assert panel.btn_cancel.isVisible()
    assert panel.btn_cancel.isEnabled()
    panel.set_cancel_enabled(False)
    assert not panel.btn_cancel.isVisible()

    panel._toggle_device_list()
    assert panel.btn_del_dev.isEnabled()


def test_channel_table_toolbar_hidden_when_empty(qapp):
    view = ChannelTableView()
    assert view.toolbar_host.isHidden()
    view.set_records(
        [
            {
                "通道": 1,
                "名称": "cam1",
                "在线": "true",
                "录像含音频": True,
                "落盘状态": "正常",
                "录像是否正常": "正常",
            }
        ],
        False,
    )
    assert not view.toolbar_host.isHidden()
    view.set_records([], False)
    assert view.toolbar_host.isHidden()


def test_mac_chrome_toolbar_holds_profile_bar(qapp):
    import sys

    from PySide6.QtWidgets import QWidget

    from ui.main_window import MainWindow

    w = MainWindow()
    if sys.platform != "darwin":
        assert w.profile_bar.parent() is not None
        assert not w._mac_chrome
        return
    chrome = w.findChild(QWidget, "MacChromeBar")
    assert chrome is not None
    assert w._mac_chrome
    assert not w.unifiedTitleAndToolBarOnMac()
    assert chrome.isAncestorOf(w.profile_bar)


def test_is_interactive_excludes_labels(qapp):
    from PySide6.QtWidgets import QLabel, QPushButton

    from ui.main_window import _is_interactive

    assert _is_interactive(QPushButton("新建"))
    assert not _is_interactive(QLabel("配置档案"))


def test_profile_bar_empty_press_does_not_crash(qapp):
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtGui import QMouseEvent

    from ui.widgets.profile_bar import ProfileBar

    bar = ProfileBar()
    bar.show()
    qapp.processEvents()
    pos = QPoint(8, 8)
    ev = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    bar.mousePressEvent(ev)

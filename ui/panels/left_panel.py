"""左侧配置面板：设备列表 + 扫描目标 + 扫描设置 + 巡检操作 + 主题。

B1 拆分：从 ui/main_window 抽出，主窗只组装。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from config_store import _empty_device, default_av_save_root
from ui import theme
from ui.widgets.device_editor import DeviceEditorDialog

LEFT_PANEL_MIN_WIDTH = 360

# 扫描目标下拉的「全部设备」选项（B5 多设备队列）
ALL_TARGET_LABEL = "全部设备"

# 回溯窗口单位：存档 lookback 始终为分钟；unit 仅 GUI 展示
_LOOKBACK_UNITS = (
    ("minute", "分钟", 1, 10080),      # 最长 7 天（按分钟调）
    ("hour", "小时", 60, 168),         # 最长 7 天
    ("day", "天", 24 * 60, 30),        # 最长 30 天
)
_LOOKBACK_UNIT_BY_KEY = {u[0]: u for u in _LOOKBACK_UNITS}
_LOOKBACK_UNIT_BY_LABEL = {u[1]: u for u in _LOOKBACK_UNITS}


def _lookback_minutes_of(value: int, unit_key: str) -> int:
    """控件数值 + 单位 → 分钟（A4 测试对象，无 UI 依赖）。"""
    _k, _label, mul, _mx = _LOOKBACK_UNIT_BY_KEY.get(
        (unit_key or "").strip().lower(), _LOOKBACK_UNITS[0]
    )
    return max(1, int(value) * int(mul))


def _lookback_value_for_minutes(
    minutes: int, unit_key: Optional[str] = None
) -> tuple:
    """分钟 → (控件数值, 单位 key)。未给单位时按整除性推断。"""
    minutes = max(1, int(minutes or 60))
    key = (unit_key or "").strip().lower()
    if key not in _LOOKBACK_UNIT_BY_KEY:
        if minutes % (24 * 60) == 0 and minutes >= 24 * 60:
            key = "day"
        elif minutes % 60 == 0 and minutes >= 60:
            key = "hour"
        else:
            key = "minute"
    _k, _label, mul, mx = _LOOKBACK_UNIT_BY_KEY[key]
    value = max(1, min(mx, (minutes + mul - 1) // mul))
    return value, key


class LeftPanel(QWidget):
    """左侧配置区。

    信号：scan_requested(mode)、cancel_requested()、save_profile_requested()、
          theme_selected(text)、log_requested(msg, level)。
    """

    scan_requested = Signal(str)
    cancel_requested = Signal()
    save_profile_requested = Signal()
    history_requested = Signal()
    theme_selected = Signal(str)
    log_requested = Signal(str, str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._device_rows: List[Dict[str, Any]] = []
        self._device_list_expanded = False  # 默认折叠设备列表
        self._scan_settings_expanded = False
        self._ffmpeg_ok = False
        self._theme_mode = theme.ThemeMode.SYSTEM
        self._lookback_unit_guard = False
        self._lookback_unit_prev = "minute"

        self.setMinimumWidth(LEFT_PANEL_MIN_WIDTH)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        left_layout = QVBoxLayout(self)
        # 右侧留出与 splitter 把手的间距，避免边框被右侧面板视觉上「盖住」
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setMinimumWidth(LEFT_PANEL_MIN_WIDTH - 8)
        body = QWidget()
        body.setMinimumWidth(LEFT_PANEL_MIN_WIDTH - 28)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(4, 4, 10, 4)
        body_layout.setSpacing(8)
        scroll.setWidget(body)
        left_layout.addWidget(scroll, 1)

        # 设备列表：仅设备行默认折叠；添加/删除/保存始终可见
        self.btn_toggle_devices = QPushButton("▶  设备列表")
        self.btn_toggle_devices.setProperty("role", "toggle")
        self.btn_toggle_devices.setToolTip("展开 / 折叠设备明细")
        self.btn_toggle_devices.clicked.connect(self._toggle_device_list)
        body_layout.addWidget(self.btn_toggle_devices)

        self.dev_body = QWidget()
        self.dev_body.setObjectName("DeviceListBody")
        dev_layout = QVBoxLayout(self.dev_body)
        dev_layout.setContentsMargins(4, 2, 4, 4)
        dev_layout.setSpacing(6)
        self.dev_list = QVBoxLayout()
        self.dev_list.setSpacing(6)
        self.dev_list.setContentsMargins(0, 0, 0, 0)
        dev_layout.addLayout(self.dev_list)
        self.dev_body.setVisible(False)
        body_layout.addWidget(self.dev_body)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self.btn_add_dev = QPushButton("添加设备")
        self.btn_del_dev = QPushButton("删除选中")
        self.btn_save_profile = QPushButton("保存档案")
        for b in (self.btn_add_dev, self.btn_del_dev, self.btn_save_profile):
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn_row.addWidget(b)
        body_layout.addLayout(btn_row)

        # 扫描目标：紧凑原生下拉，少空白
        target_group = QGroupBox("扫描目标设备")
        target_layout = QHBoxLayout(target_group)
        target_layout.setContentsMargins(8, 10, 8, 8)
        self.cmb_scan_target = QComboBox()
        self.cmb_scan_target.setObjectName("ScanTargetCombo")
        self.cmb_scan_target.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.cmb_scan_target.setMaxVisibleItems(12)
        self.cmb_scan_target.setToolTip("选择单台设备，或「全部设备」按队列依次巡检")
        target_layout.addWidget(self.cmb_scan_target, 1)
        body_layout.addWidget(target_group)

        # 扫描设置（折叠）：QFormLayout + 紧凑原生风格输入控件
        self.btn_toggle_settings = QPushButton("▶  扫描设置")
        self.btn_toggle_settings.setProperty("role", "toggle")
        self.btn_toggle_settings.setToolTip("展开 / 折叠扫描参数")
        self.btn_toggle_settings.clicked.connect(self._toggle_scan_settings)
        body_layout.addWidget(self.btn_toggle_settings)

        self.settings_basic_group = QGroupBox("基础")
        self.settings_basic_group.setObjectName("ScanSettings")
        basic_form = self._make_settings_form(self.settings_basic_group)

        self.sp_lookback = self._make_spinbox(1, 10080)
        self.sp_lookback.setToolTip("回溯检查近多久内的录像落盘情况（可切换分钟/小时/天）")
        # 分段按钮：分钟 | 小时 | 天（比下拉更清晰、好点）
        self._lookback_unit_seg, self._lookback_unit_group = self._make_segmented(
            [(k, lab) for k, lab, _m, _x in _LOOKBACK_UNITS],
            object_name="LookbackUnitSeg",
        )
        self._lookback_unit_group.idClicked.connect(self._on_lookback_unit_changed)
        lookback_cell = QWidget()
        lookback_row = QHBoxLayout(lookback_cell)
        lookback_row.setContentsMargins(0, 0, 0, 0)
        lookback_row.setSpacing(8)
        lookback_row.addWidget(self.sp_lookback, 0)
        lookback_row.addWidget(self._lookback_unit_seg, 1)

        self.sp_workers = self._make_spinbox(1, 32)
        self.sp_workers.setToolTip("并发查询通道状态的工作线程数")
        self.chk_no_search = QCheckBox("快速模式（仅配置查询）")
        self.chk_no_search.setObjectName("FormField")

        basic_form.addRow("检查近多久", lookback_cell)
        basic_form.addRow("检查并发数", self.sp_workers)
        basic_form.addRow("", self.chk_no_search)

        self.settings_deep_group = QGroupBox("深度抽检")
        self.settings_deep_group.setObjectName("ScanSettings")
        deep_form = self._make_settings_form(self.settings_deep_group)

        self.ed_save_root = QLineEdit()
        self.ed_save_root.setObjectName("FormField")
        self.ed_save_root.setPlaceholderText("抽检片段保存目录…")
        self.ed_save_root.setClearButtonEnabled(True)
        self.ed_save_root.setMinimumWidth(0)
        self.ed_save_root.setFixedHeight(26)
        self.ed_save_root.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.btn_browse_root = QPushButton("浏览…")
        self.btn_browse_root.setObjectName("FormField")
        self.btn_browse_root.setFixedHeight(26)
        self.btn_browse_root.setFixedWidth(52)
        path_cell = QWidget()
        path_row = QHBoxLayout(path_cell)
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(6)
        path_row.addWidget(self.ed_save_root, 1)
        path_row.addWidget(self.btn_browse_root)

        self.sp_av_sec = self._make_spinbox(3, 12)
        self.sp_av_sec.setSuffix(" 秒")
        self.sp_av_workers = self._make_spinbox(1, 3)
        self.sp_av_limit = self._make_spinbox(0, 256)
        self.sp_av_limit.setSpecialValueText("全部")
        self.sp_av_limit.setToolTip("0 = 抽检全部通道")
        self.sp_busy_days_ago = self._make_spinbox(0, 30)
        self.sp_busy_days_ago.setSpecialValueText("今天")
        self.sp_busy_days_ago.setSuffix(" 天前")
        self.sp_busy_days_ago.setToolTip(
            "视频抽检优先落在哪一天：0=今天，1=昨天，N=N 天前（最长 30 天）"
        )
        self.sp_busy_start = self._make_spinbox(0, 23)
        self.sp_busy_start.setSuffix(" 时")
        self.sp_busy_start.setToolTip("该日繁忙时段开始小时（本地时间）")
        self.sp_busy_end = self._make_spinbox(1, 24)
        self.sp_busy_end.setSuffix(" 时")
        self.sp_busy_end.setToolTip("该日繁忙时段结束小时（本地时间，不含该点）")
        self.sp_silence = self._make_double_spinbox(-120.0, 0.0, decimals=1, step=1.0)
        self.sp_silence.setSuffix(" dB")

        deep_form.addRow("片段保存路径", path_cell)
        deep_form.addRow("抽检秒数", self.sp_av_sec)
        deep_form.addRow("抽检并发", self.sp_av_workers)
        deep_form.addRow("抽检路上限", self.sp_av_limit)
        deep_form.addRow("抽检日期", self.sp_busy_days_ago)
        deep_form.addRow("繁忙开始", self.sp_busy_start)
        deep_form.addRow("繁忙结束", self.sp_busy_end)
        deep_form.addRow("静音阈值", self.sp_silence)

        self.settings_basic_group.setVisible(False)
        self.settings_deep_group.setVisible(False)
        body_layout.addWidget(self.settings_basic_group)
        body_layout.addWidget(self.settings_deep_group)

        # 巡检操作
        action_group = QGroupBox("开始巡检")
        action_layout = QVBoxLayout(action_group)
        action_layout.setContentsMargins(8, 12, 8, 8)
        action_layout.setSpacing(6)

        scan_btns = QHBoxLayout()
        scan_btns.setSpacing(6)
        self.btn_scan_quick = QPushButton("快速巡检")
        self.btn_scan_quick.setProperty("role", "primary")
        self.btn_scan_quick.setToolTip("状态 + 近期录像落盘检查（快速）")
        self.btn_scan_deep = QPushButton("深度巡检")
        self.btn_scan_deep.setProperty("role", "primary")
        self.btn_scan_deep.setToolTip("深度巡检：含录像抽检与音频检查（需 ffmpeg）")
        for b in (self.btn_scan_quick, self.btn_scan_deep):
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            scan_btns.addWidget(b)
        action_layout.addLayout(scan_btns)

        deep_opts = QHBoxLayout()
        deep_opts.setSpacing(6)
        self.chk_av_save = QCheckBox("保存抽检片段")
        self.chk_av_save.setToolTip("仅深度巡检时生效：将抽检片段写入保存路径")
        self.chk_av_save.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setToolTip("取消当前巡检")
        self.btn_cancel.setFixedWidth(64)
        deep_opts.addWidget(self.chk_av_save, 1)
        deep_opts.addWidget(self.btn_cancel)
        action_layout.addLayout(deep_opts)

        # ffmpeg 状态放在「开始巡检」最下方
        self.ffmpeg_label = QLabel("")
        self.ffmpeg_label.setWordWrap(True)
        self.ffmpeg_label.setStyleSheet(
            "color: " + theme.ui_color("muted", False) + "; font-size: 11px;"
        )
        action_layout.addWidget(self.ffmpeg_label)
        body_layout.addWidget(action_group)

        # 底部操作（导出已移至右侧检查结果区）
        bottom_group = QGroupBox("操作")
        bottom_grid = QGridLayout(bottom_group)
        bottom_grid.setContentsMargins(8, 12, 8, 8)
        bottom_grid.setHorizontalSpacing(6)
        bottom_grid.setVerticalSpacing(6)
        bottom_grid.setColumnStretch(0, 1)
        bottom_grid.setColumnStretch(1, 1)
        self.btn_open_save = QPushButton("打开保存目录")
        self.btn_open_config = QPushButton("打开配置目录")
        self.btn_history = QPushButton("历史报告")
        for b in (self.btn_open_save, self.btn_open_config, self.btn_history):
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bottom_grid.addWidget(self.btn_open_save, 0, 0)
        bottom_grid.addWidget(self.btn_open_config, 0, 1)
        bottom_grid.addWidget(self.btn_history, 1, 0, 1, 2)

        # 主题：分段切换 浅色 | 深色 | 跟随系统
        theme_cell = QWidget()
        theme_lay = QVBoxLayout(theme_cell)
        theme_lay.setContentsMargins(0, 4, 0, 0)
        theme_lay.setSpacing(4)
        theme_lbl = QLabel("主题")
        theme_lbl.setStyleSheet("font-weight: 600;")
        theme_lay.addWidget(theme_lbl)
        theme_items = [
            (m.value, theme.display_name(m)) for m in theme.ThemeMode
        ]
        self._theme_seg, self._theme_group = self._make_segmented(
            theme_items,
            object_name="ThemeSeg",
        )
        self._theme_group.idClicked.connect(self._on_theme_segment_clicked)
        theme_lay.addWidget(self._theme_seg)
        bottom_grid.addWidget(theme_cell, 2, 0, 1, 2)
        body_layout.addWidget(bottom_group)
        body_layout.addStretch(1)

        self.btn_add_dev.clicked.connect(self._add_device)
        self.btn_del_dev.clicked.connect(self._del_device)
        self.btn_save_profile.clicked.connect(self.save_profile_requested.emit)
        self.btn_open_save.clicked.connect(self._open_save_dir)
        self.btn_open_config.clicked.connect(self._open_config_dir)
        self.btn_history.clicked.connect(self.history_requested.emit)
        self.btn_scan_quick.clicked.connect(lambda: self.scan_requested.emit("quick"))
        self.btn_scan_deep.clicked.connect(lambda: self.scan_requested.emit("deep"))
        self.btn_cancel.clicked.connect(self.cancel_requested.emit)
        self.btn_browse_root.clicked.connect(self._browse_save_root)

    # ---------- 配色 / 主题 ----------

    def set_theme_mode(self, mode: theme.ThemeMode) -> None:
        self._theme_mode = mode
        self._select_segment(self._theme_group, mode.value)

    def _on_theme_segment_clicked(self, _id: int = 0) -> None:
        btn = self._theme_group.checkedButton()
        if btn is None:
            return
        # 信号约定：传显示名（与原先 ComboBox currentText 一致）
        self.theme_selected.emit(btn.text())

    def set_dark(self, dark: bool) -> None:
        muted = theme.ui_color("muted", dark)
        border = theme.BORDER["dark" if dark else "light"]
        for row in self._device_rows:
            row["ip_lbl"].setStyleSheet(
                f"color: {muted}; font-family: Menlo, Consolas, monospace; font-size: 11px;"
            )
            frame = row.get("row")
            if frame is not None:
                frame.setStyleSheet(
                    f"QFrame#DeviceRow {{"
                    f"  background: transparent;"
                    f"  border: 1px solid {border};"
                    f"  border-radius: 6px;"
                    f"}}"
                )
            row["chk"].setToolTip(self._device_row_tip(row))
        self.ffmpeg_label.setStyleSheet(
            "color: "
            + theme.ui_color("ok" if self._ffmpeg_ok else "warn", dark)
            + "; font-size: 11px;"
        )

    def set_ffmpeg_status(self, ok: bool) -> None:
        self._ffmpeg_ok = bool(ok)
        if ok:
            self.ffmpeg_label.setText(
                "ffmpeg: 已就绪 · 可进行深度巡检（录像抽检 / 音频检查）"
            )
        else:
            self.ffmpeg_label.setText(
                "未检测到 ffmpeg/ffprobe：深度巡检与保存片段不可用。"
                "可将二进制放到应用目录 bin/，或安装到系统 PATH。"
            )
        self.ffmpeg_label.setStyleSheet(
            "color: "
            + theme.ui_color("ok" if ok else "warn", theme.effective_dark())
            + "; font-size: 11px;"
        )

    # ---------- 设备列表折叠 / 扫描设置 ----------

    def _toggle_device_list(self) -> None:
        self._device_list_expanded = not self._device_list_expanded
        self.dev_body.setVisible(self._device_list_expanded)
        self._refresh_device_toggle_label()

    def _refresh_device_toggle_label(self) -> None:
        n = len(self._device_rows)
        arrow = "▼  " if self._device_list_expanded else "▶  "
        if n <= 0:
            self.btn_toggle_devices.setText(f"{arrow}设备列表")
        else:
            self.btn_toggle_devices.setText(f"{arrow}设备列表（{n} 台）")

    def _toggle_scan_settings(self) -> None:
        self._scan_settings_expanded = not self._scan_settings_expanded
        self.settings_basic_group.setVisible(self._scan_settings_expanded)
        self.settings_deep_group.setVisible(self._scan_settings_expanded)
        self.btn_toggle_settings.setText(
            ("▼  " if self._scan_settings_expanded else "▶  ") + "扫描设置"
        )

    @staticmethod
    def _make_settings_form(group: QGroupBox) -> QFormLayout:
        """扫描设置用表单：紧凑、标签右对齐，数值框不铺满整行。"""
        form = QFormLayout(group)
        form.setContentsMargins(10, 10, 10, 8)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(6)
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        form.setFormAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        return form

    @staticmethod
    def _make_spinbox(lo: int, hi: int, value: Optional[int] = None) -> QSpinBox:
        sp = QSpinBox()
        sp.setObjectName("FormField")
        sp.setRange(lo, hi)
        if value is not None:
            sp.setValue(value)
        sp.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        sp.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        sp.setFixedHeight(26)
        sp.setFixedWidth(104)
        sp.setKeyboardTracking(False)
        sp.setFrame(True)
        return sp

    @staticmethod
    def _make_double_spinbox(
        lo: float,
        hi: float,
        *,
        decimals: int = 1,
        step: float = 1.0,
        value: Optional[float] = None,
    ) -> QDoubleSpinBox:
        sp = QDoubleSpinBox()
        sp.setObjectName("FormField")
        sp.setRange(lo, hi)
        sp.setDecimals(decimals)
        sp.setSingleStep(step)
        if value is not None:
            sp.setValue(value)
        sp.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        sp.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        sp.setFixedHeight(26)
        sp.setFixedWidth(104)
        sp.setKeyboardTracking(False)
        sp.setFrame(True)
        return sp

    # ---------- 分段控件 / 回溯窗口 ----------

    @staticmethod
    def _make_segmented(
        items: List[tuple],
        *,
        object_name: str = "SegmentGroup",
    ) -> tuple:
        """创建互斥分段按钮组。items: [(key, label), ...]。返回 (容器, QButtonGroup)。"""
        wrap = QFrame()
        wrap.setObjectName(object_name)
        wrap.setProperty("role", "segment")
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        group = QButtonGroup(wrap)
        group.setExclusive(True)
        for i, (key, label) in enumerate(items):
            btn = QPushButton(str(label))
            btn.setObjectName("SegmentBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("segment", str(key))
            btn.setFixedHeight(24)
            btn.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            group.addButton(btn, i)
            lay.addWidget(btn, 1)
            if i == 0:
                btn.setChecked(True)
        return wrap, group

    @staticmethod
    def _select_segment(group: QButtonGroup, key: str) -> None:
        """按 property segment 选中按钮（不触发业务侧重复逻辑时由调用方 guard）。"""
        for btn in group.buttons():
            if str(btn.property("segment") or "") == str(key):
                btn.setChecked(True)
                return

    def _lookback_unit_key(self) -> str:
        btn = self._lookback_unit_group.checkedButton()
        if btn is not None:
            return str(btn.property("segment") or "minute")
        return "minute"

    @staticmethod
    def _format_lookback_label(opt: Dict[str, Any]) -> str:
        minutes = max(1, int(opt.get("lookback") or 60))
        days_ago = int(opt.get("busy_days_ago") or 0)
        if minutes % (24 * 60) == 0:
            text = f"{minutes // (24 * 60)} 天"
        elif minutes % 60 == 0:
            text = f"{minutes // 60} 小时"
        else:
            text = f"{minutes} 分钟"
        if days_ago > 0:
            day_txt = "昨天" if days_ago == 1 else f"{days_ago} 天前"
            text += f" · 抽检日 {day_txt}"
        return text

    def _lookback_minutes_from_form(self) -> int:
        return _lookback_minutes_of(self.sp_lookback.value(), self._lookback_unit_key())

    def _set_lookback_from_minutes(
        self, minutes: int, unit_key: Optional[str] = None
    ) -> None:
        """把存档中的分钟数还原到「数值 + 单位」控件。"""
        value, key = _lookback_value_for_minutes(minutes, unit_key)
        self._lookback_unit_guard = True
        try:
            self._select_segment(self._lookback_unit_group, key)
            _k, _label, _mul, mx = _LOOKBACK_UNIT_BY_KEY[key]
            self.sp_lookback.setRange(1, mx)
            self.sp_lookback.setValue(value)
            self._lookback_unit_prev = key
        finally:
            self._lookback_unit_guard = False

    def _on_lookback_unit_changed(self, _index: int = 0) -> None:
        """切换单位时尽量保持实际分钟数不变。"""
        if self._lookback_unit_guard:
            return
        old_key = self._lookback_unit_prev or "minute"
        new_key = self._lookback_unit_key()
        if old_key == new_key:
            self._lookback_unit_prev = new_key
            return
        _ok, _ol, old_mul, _omx = _LOOKBACK_UNIT_BY_KEY.get(old_key, _LOOKBACK_UNITS[0])
        minutes = max(1, int(self.sp_lookback.value()) * int(old_mul))
        _nk, _nl, new_mul, new_mx = _LOOKBACK_UNIT_BY_KEY[new_key]
        if minutes % new_mul == 0:
            value = max(1, min(new_mx, minutes // new_mul))
        else:
            # 向上取整，避免切换后窗口变小
            value = max(1, min(new_mx, (minutes + new_mul - 1) // new_mul))
        self._lookback_unit_guard = True
        try:
            self.sp_lookback.setRange(1, new_mx)
            self.sp_lookback.setValue(value)
        finally:
            self._lookback_unit_guard = False
        self._lookback_unit_prev = new_key

    # ---------- 设备 ----------

    def _clear_device_rows(self) -> None:
        while self.dev_list.count():
            item = self.dev_list.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _add_device_row(self, dev: Optional[Dict[str, Any]] = None) -> None:
        device = self._normalize_device(dev)
        dark = theme.effective_dark()
        muted = theme.ui_color("muted", dark)
        border = theme.BORDER["dark" if dark else "light"]

        # 卡片行：[复选框] | 名称+IP  | [编辑]
        row = QFrame()
        row.setObjectName("DeviceRow")
        row.setStyleSheet(
            f"QFrame#DeviceRow {{"
            f"  background: transparent;"
            f"  border: 1px solid {border};"
            f"  border-radius: 6px;"
            f"}}"
        )
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(8, 6, 6, 6)
        row_layout.setSpacing(0)

        # 复选框单独占一列，避免与名称贴死
        chk = QCheckBox()
        chk.setFixedWidth(20)
        chk.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row_layout.addWidget(chk, 0, Qt.AlignmentFlag.AlignTop)
        row_layout.addSpacing(10)

        # 中间：名称（主）+ IP:端口（次）
        info = QWidget()
        info.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)

        name_lbl = QLabel(device["name"] or "NVR")
        f = name_lbl.font()
        f.setBold(True)
        name_lbl.setFont(f)
        name_lbl.setMinimumWidth(0)
        name_lbl.setWordWrap(False)
        name_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        name_lbl.setToolTip(device["name"] or "NVR")

        ip_text = self._format_device_ip(device)
        ip_lbl = QLabel(ip_text)
        ip_lbl.setMinimumWidth(0)
        ip_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        ip_lbl.setStyleSheet(
            f"color: {muted}; font-family: Menlo, Consolas, monospace; font-size: 11px;"
        )
        ip_lbl.setToolTip(ip_text)

        info_layout.addWidget(name_lbl)
        info_layout.addWidget(ip_lbl)
        row_layout.addWidget(info, 1)

        row_layout.addSpacing(8)

        btn_edit = QPushButton("编辑")
        btn_edit.setObjectName("FormField")
        btn_edit.setFixedSize(52, 26)
        btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        row_layout.addWidget(btn_edit, 0, Qt.AlignmentFlag.AlignVCenter)

        row_data = {
            "device": device,
            "row": row,
            "chk": chk,
            "name_lbl": name_lbl,
            "ip_lbl": ip_lbl,
        }
        chk.setToolTip(self._device_row_tip(row_data))
        btn_edit.clicked.connect(lambda: self._edit_device(row_data))
        self.dev_list.addWidget(row)
        self._device_rows.append(row_data)
        self._refresh_scan_target()
        self._refresh_device_toggle_label()

    @staticmethod
    def _format_device_ip(device: Dict[str, Any]) -> str:
        ip = (device.get("ip") or "").strip() or "—"
        try:
            port = int(device.get("port") or 80)
        except (TypeError, ValueError):
            port = 80
        if ip == "—":
            return "—"
        return f"{ip}:{port}"

    @staticmethod
    def _device_row_tip(row_data: Dict[str, Any]) -> str:
        d = row_data["device"]
        lines = [
            f"{d.get('name') or 'NVR'}",
            f"IP: {d.get('ip') or '—'} : {d.get('port') or 80}",
            f"账号: {d.get('username') or 'admin'}",
            f"SSL: {'启用' if d.get('ssl') else '关闭'}",
        ]
        return "\n".join(lines)

    def _refresh_device_labels(self, row_data: Dict[str, Any]) -> None:
        d = row_data["device"]
        name = d.get("name") or "NVR"
        ip_text = self._format_device_ip(d)
        row_data["name_lbl"].setText(name)
        row_data["name_lbl"].setToolTip(name)
        row_data["ip_lbl"].setText(ip_text)
        row_data["ip_lbl"].setToolTip(ip_text)
        row_data["chk"].setToolTip(self._device_row_tip(row_data))

    def _edit_device(self, row_data: Dict[str, Any]) -> None:
        dlg = DeviceEditorDialog(
            device=row_data["device"],
            on_save=lambda dev: self._on_device_dialog_edit(row_data, dev),
            title="编辑设备",
            parent=self,
        )
        dlg.exec()

    def _add_device(self) -> None:
        n = len(self._device_rows) + 1
        d = self._normalize_device(_empty_device())
        d["name"] = f"NVR{n}"
        d["ip"] = ""
        dlg = DeviceEditorDialog(
            device=d,
            on_save=self._on_device_dialog_add,
            title="添加设备",
            parent=self,
        )
        dlg.exec()

    def _on_device_dialog_add(self, device: Dict[str, Any]) -> None:
        self._add_device_row(device)
        self.log_requested.emit(
            f"已添加设备：{device.get('name')} ({device.get('ip')})", "ok"
        )

    def _on_device_dialog_edit(
        self, row_data: Dict[str, Any], device: Dict[str, Any]
    ) -> None:
        row_data["device"] = self._normalize_device(device)
        self._refresh_device_labels(row_data)
        self._refresh_scan_target()
        self.log_requested.emit(
            f"已更新设备：{device.get('name')} ({device.get('ip')})", "ok"
        )

    @staticmethod
    def _normalize_device(dev: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        base = _empty_device()
        if dev:
            base.update(dev)
        try:
            base["port"] = int(base.get("port") or 80)
        except (TypeError, ValueError):
            base["port"] = 80
        base["name"] = str(base.get("name") or "NVR").strip() or "NVR"
        base["ip"] = str(base.get("ip") or "").strip()
        base["username"] = str(base.get("username") or "admin").strip() or "admin"
        base["password"] = str(base.get("password") or "")
        base["ssl"] = bool(base.get("ssl"))
        return base

    def _del_device(self) -> None:
        keep = []
        for r in self._device_rows:
            if r["chk"].isChecked():
                idx = self.dev_list.indexOf(r["chk"].parentWidget())
                if idx >= 0:
                    item = self.dev_list.takeAt(idx)
                    if item.widget() is not None:
                        item.widget().deleteLater()
            else:
                keep.append(r)
        if len(keep) == len(self._device_rows):
            QMessageBox.information(self, "提示", "请先勾选要删除的设备")
            return
        if not keep:
            QMessageBox.warning(self, "提示", "至少保留一台设备")
            self._add_device_row()
            keep = [self._device_rows[-1]]
        self._device_rows = keep
        self._refresh_scan_target()
        self._refresh_device_toggle_label()

    def _collect_devices(self) -> List[Dict[str, Any]]:
        return [self._normalize_device(r["device"]) for r in self._device_rows]

    def _scan_target_data(self) -> Any:
        """当前下拉 userData：设备下标 int，或 \"all\"，或 None。"""
        return self.cmb_scan_target.currentData()

    def _refresh_scan_target(self) -> None:
        prev = self._scan_target_data()
        devices = self._collect_devices()
        row_h = QSize(-1, 25)  # 紧凑行高，选项间距较默认 +1px
        block = self.cmb_scan_target.blockSignals(True)
        try:
            self.cmb_scan_target.clear()
            if not devices:
                self.cmb_scan_target.addItem("（暂无设备）", None)
                return
            for i, d in enumerate(devices):
                name = (d.get("name") or "NVR").strip() or "NVR"
                ip = (d.get("ip") or "").strip() or "—"
                # 紧凑单行：1. 名称 · IP
                text = f"{i + 1}. {name} · {ip}"
                self.cmb_scan_target.addItem(text, i)
                self.cmb_scan_target.setItemData(
                    i, row_h, Qt.ItemDataRole.SizeHintRole
                )
            # 不用 insertSeparator（会占一整行空白）；紧跟全部设备
            all_text = f"全部设备（{len(devices)} 台）"
            self.cmb_scan_target.addItem(all_text, "all")
            all_idx = self.cmb_scan_target.count() - 1
            self.cmb_scan_target.setItemData(
                all_idx, row_h, Qt.ItemDataRole.SizeHintRole
            )
            font = QFont(self.cmb_scan_target.font())
            font.setBold(True)
            self.cmb_scan_target.setItemData(
                all_idx, font, Qt.ItemDataRole.FontRole
            )

            # 恢复选中
            restored = False
            if prev is not None:
                for i in range(self.cmb_scan_target.count()):
                    if self.cmb_scan_target.itemData(i) == prev:
                        self.cmb_scan_target.setCurrentIndex(i)
                        restored = True
                        break
            if not restored:
                self.cmb_scan_target.setCurrentIndex(0)
        finally:
            self.cmb_scan_target.blockSignals(block)

    def _target_labels(self) -> List[str]:
        """兼容旧逻辑的文案列表（导出/测试等）。"""
        devices = self._collect_devices()
        labels = [
            f"{i + 1}. {(d.get('name') or 'NVR')} · {(d.get('ip') or '—')}"
            for i, d in enumerate(devices)
        ]
        if labels:
            labels.append(f"全部设备（{len(devices)} 台）")
        return labels or ["（暂无设备）"]

    def selected_device(self) -> Optional[Dict[str, Any]]:
        devices = self._collect_devices()
        if not devices:
            return None
        data = self._scan_target_data()
        if data == "all" or data is None:
            return None if data == "all" else devices[0]
        try:
            idx = int(data)
        except (TypeError, ValueError):
            return devices[0]
        if 0 <= idx < len(devices):
            return devices[idx]
        return devices[0]

    def selected_target(self) -> Dict[str, Any]:
        """返回选中目标：{"all": True} 或 {"all": False, "device": dev}。"""
        if self._scan_target_data() == "all":
            return {"all": True, "device": None}
        return {"all": False, "device": self.selected_device()}

    def collect_devices(self) -> List[Dict[str, Any]]:
        return self._collect_devices()

    # ---------- 档案表单 ----------

    def load_profile_to_form(self, prof: Dict[str, Any]) -> None:
        self._clear_device_rows()
        self._device_rows = []
        for d in prof.get("devices") or [_empty_device()]:
            self._add_device_row(d)
        # 加载档案后保持折叠，仅刷新标题台数
        self._device_list_expanded = False
        self.dev_body.setVisible(False)
        self._refresh_device_toggle_label()
        opt = prof.get("scan_options") or {}
        self._set_lookback_from_minutes(
            int(opt.get("lookback", 60)),
            str(opt.get("lookback_unit") or ""),
        )
        self._lookback_unit_prev = self._lookback_unit_key()
        self.sp_workers.setValue(int(opt.get("workers", 8)))
        self.chk_no_search.setChecked(bool(opt.get("no_search")))
        root = (opt.get("av_save_root") or "").strip() or default_av_save_root()
        self.ed_save_root.setText(root)
        self.sp_av_sec.setValue(int(opt.get("av_seconds", 6)))
        self.sp_av_workers.setValue(int(opt.get("av_workers", 2)))
        self.sp_av_limit.setValue(int(opt.get("av_limit", 0)))
        self.sp_busy_days_ago.setValue(int(opt.get("busy_days_ago", 0)))
        self.sp_busy_start.setValue(int(opt.get("busy_start", 10)))
        self.sp_busy_end.setValue(int(opt.get("busy_end", 18)))
        self.sp_silence.setValue(float(opt.get("silence_db", -80)))
        self.chk_av_save.setChecked(bool(opt.get("av_save")))
        idx = int(prof.get("default") or 0)
        devices = self._collect_devices()
        if devices:
            self._refresh_scan_target()
            # default 为设备下标；越界则取 0
            want = min(max(0, idx), len(devices) - 1)
            for i in range(self.cmb_scan_target.count()):
                if self.cmb_scan_target.itemData(i) == want:
                    self.cmb_scan_target.setCurrentIndex(i)
                    break

    def form_to_profile_dict(self, profile_name: str) -> Dict[str, Any]:
        devices = self._collect_devices()
        default = 0
        data = self._scan_target_data()
        if isinstance(data, int) and 0 <= data < len(devices):
            default = data
        elif data == "all":
            # 「全部设备」不作为档案默认下标，保留 0
            default = 0
        opt = {
            "lookback": self._lookback_minutes_from_form(),
            "lookback_unit": self._lookback_unit_key(),
            "no_search": bool(self.chk_no_search.isChecked()),
            "workers": self.sp_workers.value(),
            "deep_av_check": False,
            "av_seconds": self.sp_av_sec.value(),
            "av_workers": self.sp_av_workers.value(),
            "av_limit": self.sp_av_limit.value(),
            "silence_db": self.sp_silence.value(),
            "busy_days_ago": self.sp_busy_days_ago.value(),
            "busy_start": self.sp_busy_start.value(),
            "busy_end": self.sp_busy_end.value(),
            "av_save": bool(self.chk_av_save.isChecked()),
            "av_save_root": self.ed_save_root.text().strip(),
        }
        return {
            "name": profile_name,
            "devices": devices,
            "default": default,
            "scan_options": opt,
        }

    # ---------- 目录 ----------

    def _browse_save_root(self) -> None:
        current = self.ed_save_root.text().strip() or default_av_save_root()
        initial = current if os.path.isdir(current) else os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "选择抽检片段保存目录", initial)
        if path:
            self.ed_save_root.setText(path)
            self.save_profile_requested.emit()
            self.log_requested.emit(f"抽检片段保存路径已设为：{path}", "info")

    def _open_save_dir(self) -> None:
        path = self.ed_save_root.text().strip() or default_av_save_root()
        os.makedirs(path, exist_ok=True)
        self._open_path(path)

    def _open_config_dir(self) -> None:
        from config_store import app_data_dir

        self._open_path(app_data_dir())

    def _open_path(self, path: str) -> None:
        import sys

        try:
            if sys.platform == "darwin":
                os.system(f'open "{path}"')
            elif sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                os.system(f'xdg-open "{path}"')
        except Exception as e:
            QMessageBox.critical(self, "打开失败", str(e))

    # ---------- 状态 ----------

    def set_scan_buttons_enabled(self, enabled: bool) -> None:
        self.btn_scan_quick.setEnabled(enabled)
        self.btn_scan_deep.setEnabled(enabled)
        self.btn_save_profile.setEnabled(enabled)

    def set_cancel_enabled(self, enabled: bool) -> None:
        self.btn_cancel.setEnabled(enabled)

"""右侧结果面板：汇总标题 + 指标卡 + 预警 + 通道表 + 详情窗 + 大窗。

B1 拆分：从 ui/main_window 抽出，主窗只组装。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from nvr_core.scan_runner import AV_FIELDS
from ui import theme
from ui.widgets.channel_table import ChannelTableView, row_tag


IDLE_SUMMARY = "尚未扫描 — 选择设备后点击「快速巡检」"


def summarize_warn_lines(lines: Optional[List[str]]) -> str:
    """预警全文 → 一行摘要（详情里仍保留硬盘明细）。"""
    alerts: List[str] = []
    extra: List[str] = []
    for raw in lines or []:
        s = str(raw).strip()
        if s.startswith("•") or s.startswith("-") or s.startswith("·"):
            s = s.lstrip("•·- ").strip()
        if not s or s in ("预警：", "预警:", "预警：无"):
            continue
        if s.startswith("硬盘："):
            continue
        if s.startswith("循环覆盖"):
            extra.append(s)
            continue
        if s.startswith("说明：") or s.startswith("抽检") or s.startswith("片段目录"):
            extra.append(s)
            continue
        alerts.append(s)
    if not alerts and not extra:
        return "预警：无"
    parts: List[str] = []
    for a in alerts[:2]:
        parts.append(a.split("（")[0].strip() or a)
    for e in extra:
        if "开启" in e:
            parts.append("循环覆盖已开启")
            break
        if e.startswith("抽检"):
            parts.append(e[:28])
            break
    if len(alerts) > 2:
        parts.append(f"等{len(alerts)}条")
    return " · ".join(parts) if parts else "预警：无"

# 指标卡固定名称：未巡检时也显示，数值为 "—"
METRIC_TITLES: Dict[str, str] = {
    "health": "健康状态",
    "online": "摄像头在线",
    "record": "录像正常",
    "disk": "近期有录像",
    "audio": "音频配置",
}


class MetricCard(QFrame):
    _ACCENT = {
        "normal": "#6e7781",
        "ok": "#1a7f37",
        "warn": "#b26a00",
        "bad": "#c62828",
        "muted": "#8E8E93",
    }

    def __init__(
        self,
        title: str = "—",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._dark = False
        self._tone = "muted"
        self._title_text = title
        self.setObjectName("Card")
        self.setMinimumHeight(40)
        self.setMaximumHeight(44)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 3, 8, 3)
        root.setSpacing(0)
        self.title = QLabel(title)
        self.title.setStyleSheet(self._title_style(False))
        self.value = QLabel("—")
        f = self.value.font()
        f.setPointSize(13)
        f.setBold(True)
        self.value.setFont(f)
        root.addWidget(self.title)
        root.addWidget(self.value)
        self.setToolTip("")
        # 初始 muted 左边条
        self.setStyleSheet(
            f"QFrame#Card {{ border-left: 3px solid {self._ACCENT['muted']}; }}"
        )

    @staticmethod
    def _title_style(dark: bool) -> str:
        return (
            "color: "
            + theme.ui_color("muted", dark)
            + "; font-size: 11px; font-weight: 600;"
        )

    def set_metric(self, title: str, value: str, tone: str = "normal", tip: str = "") -> None:
        # 空标题时保留原有名称，避免 clear 时被刷掉
        if title and title != "—":
            self._title_text = title
        self.title.setText(self._title_text)
        self.value.setText(value if value is not None else "—")
        self._tone = tone if tone in self._ACCENT else "normal"
        self.setToolTip(tip)
        colors = {
            "normal": ("#1f2328", "#e6e6e6"),
            "ok": ("#1a7f37", "#6dcf7a"),
            "warn": ("#b26a00", "#fcb24a"),
            "bad": ("#c62828", "#ff6b6b"),
            "muted": ("#6e7781", "#8b949e"),
        }
        light, dark = colors.get(tone, colors["normal"])
        accent = self._ACCENT[self._tone]
        self.value.setStyleSheet(
            f"color: {light if not self._dark else dark}; font-size: 13px; font-weight: 700;"
        )
        self.title.setStyleSheet(self._title_style(self._dark))
        self.setStyleSheet(
            f"QFrame#Card {{ border-left: 3px solid {accent}; }}"
        )

    def reset_idle(self) -> None:
        """未巡检 / 清空结果：保留名称，数值用破折号，避免假数据感。"""
        self.set_metric(self._title_text, "—", "muted")

    def set_dark(self, dark: bool) -> None:
        self._dark = dark
        self.title.setStyleSheet(self._title_style(dark))
        # 用当前 tone 重刷数值色
        self.set_metric(self._title_text, self.value.text(), self._tone, self.toolTip())


class ChannelDetailDialog(QWidget):
    """双击通道行时展示单通道全部字段。

    单例复用：ResultsPanel 只重建内容，不再叠窗口。
    """

    _ORDER = ("通道", "名称", "IP", "状态", "设备状态", "在线", "点位", "编号", "厂家", "型号")

    def __init__(self, rec: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent, Qt.WindowType.Window)
        self._dark = theme.effective_dark()
        self._key_labels: List[QLabel] = []
        self.setMinimumSize(560, 420)
        root = QVBoxLayout(self)
        root.setSpacing(8)

        self._head = QLabel("")
        f = self._head.font()
        f.setPointSize(14)
        f.setBold(True)
        self._head.setFont(f)
        root.addWidget(self._head)

        body = QScrollArea()
        body.setWidgetResizable(True)
        self._content = QWidget()
        self._grid = QGridLayout(self._content)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(4)
        self._grid.setContentsMargins(8, 8, 8, 8)
        body.setWidget(self._content)
        root.addWidget(body, 1)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        root.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)

        self.set_record(rec)

    def set_record(self, rec: Dict[str, Any]) -> None:
        ch = rec.get("通道", "")
        name = rec.get("名称") or ""
        self.setWindowTitle(f"通道 {ch} 详情")
        self._head.setText(f"通道 {ch}" + (f"  ·  {name}" if name else ""))
        # 清空旧网格
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._key_labels.clear()

        rows = list(rec.items())

        def _priority(item) -> int:
            key, _ = item
            if key in self._ORDER:
                return self._ORDER.index(key)
            return 100 + len(rows)  # 未列入优先级的按键出现顺序靠后

        rows.sort(key=_priority)
        muted = theme.ui_color("muted", self._dark)
        for i, (key, val) in enumerate(rows):
            if val is None or val == "":
                continue
            k_lbl = QLabel(f"{key}")
            k_lbl.setStyleSheet(f"color: {muted};")
            self._key_labels.append(k_lbl)
            v_lbl = QLabel(str(val))
            v_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if key in ("playback_uri", "seg_start", "seg_end", "IP"):
                v_lbl.setFont(theme.mono_font())
            self._grid.addWidget(k_lbl, i, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
            self._grid.addWidget(v_lbl, i, 1, Qt.AlignmentFlag.AlignTop)
            self._grid.setColumnStretch(1, 1)

    def set_dark(self, dark: bool) -> None:
        self._dark = dark
        muted = theme.ui_color("muted", dark)
        for lbl in self._key_labels:
            lbl.setStyleSheet(f"color: {muted};")


class ResultsExpandWindow(QWidget):
    """通道列表大窗：仅展示通道表，便于大屏审阅。"""

    export_requested = Signal()
    detail_requested = Signal(object)
    single_check_requested = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("通道列表")
        self.setMinimumSize(900, 560)
        self.resize(1200, 800)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.title_label = QLabel("通道列表")
        f = self.title_label.font()
        f.setPointSize(14)
        f.setBold(True)
        self.title_label.setFont(f)
        head.addWidget(self.title_label, 1)

        self.btn_export = QPushButton("导出结果")
        self.btn_export.setProperty("role", "primary")
        self.btn_export.setToolTip("导出当前巡检结果为 CSV / TXT")
        self.btn_export.clicked.connect(self.export_requested.emit)
        head.addWidget(self.btn_export)

        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.close)
        head.addWidget(self.btn_close)
        root.addLayout(head)

        # 大窗内不再重复「大窗显示」，仅保留筛选；导出在标题栏
        self.channel_table = ChannelTableView(show_result_actions=False)
        self.channel_table.view.doubleClicked.connect(self._on_double_click)
        self.channel_table.detail_requested.connect(self.detail_requested.emit)
        self.channel_table.export_requested.connect(self.export_requested.emit)
        self.channel_table.single_check_requested.connect(
            self.single_check_requested.emit
        )
        root.addWidget(self.channel_table, 1)

        self.tip_label = QLabel("提示：双击行查看通道详情 · 右键可复制/导出 · 支持「仅异常 / 仅离线」筛选")
        root.addWidget(self.tip_label)
        self.set_dark(theme.effective_dark())

    def _on_double_click(self, index) -> None:
        if not index.isValid():
            return
        src = self.channel_table.proxy.mapToSource(index)
        rec = self.channel_table.model.record_at(src.row())
        if rec:
            self.detail_requested.emit(rec)

    def apply_snapshot(
        self,
        *,
        title: str,
        records: List[Dict[str, Any]],
        deep: bool,
        dark: bool,
    ) -> None:
        self.title_label.setText(title or "通道列表")
        self.channel_table.set_records(records, deep)
        self.channel_table.set_dark(dark)

    def set_dark(self, dark: bool) -> None:
        self.channel_table.set_dark(dark)
        self.tip_label.setStyleSheet(
            "color: " + theme.ui_color("muted", dark) + "; font-size: 11px;"
        )


class ResultsPanel(QWidget):
    """右侧结果区：汇总/指标/预警/通道表，并管理详情窗与大窗。

    信号：export_requested()、detail_requested(rec)、log_requested(msg, level)。
    """

    export_requested = Signal()
    detail_requested = Signal(object)
    log_requested = Signal(str, str)
    single_check_requested = Signal(list)  # 选中通道记录列表
    layout_mode_changed = Signal(bool)  # True=有巡检结果（通道表展开）

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._last_result: Optional[Dict[str, Any]] = None
        self._last_warn_lines: Optional[List[str]] = None
        self._last_channel_deep = False
        self._last_metrics_snapshot: List[tuple] = []
        self._results_window: Optional[ResultsExpandWindow] = None
        self._detail_dialog: Optional[ChannelDetailDialog] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # 汇总标题（略缩小字号/行距，整体状态区约减 1/3 高度）
        self.summary_label = QLabel(IDLE_SUMMARY)
        f = self.summary_label.font()
        f.setPointSize(12)
        f.setBold(True)
        self.summary_label.setFont(f)
        self.summary_label.setWordWrap(True)
        self.summary_label.setMaximumHeight(36)
        root.addWidget(self.summary_label)

        self.device_sub_label = QLabel("")
        self.device_sub_label.setStyleSheet(
            "color: " + theme.ui_color("muted", False) + "; font-size: 11px;"
        )
        self.device_sub_label.setMaximumHeight(20)
        self.device_sub_label.hide()
        root.addWidget(self.device_sub_label)

        # 指标卡片：始终显示名称，未巡检时数值为「—」
        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(4)
        self._metric_cards: Dict[str, MetricCard] = {}
        for key in ("health", "online", "record", "disk", "audio"):
            card = MetricCard(title=METRIC_TITLES[key])
            card.reset_idle()
            self._metric_cards[key] = card
            metrics_row.addWidget(card, 1)
        root.addLayout(metrics_row)

        # 预警：默认一行摘要，点开才显示全文
        self.warn_toggle = QPushButton("▶  预警：无")
        self.warn_toggle.setProperty("role", "toggle")
        self.warn_toggle.setToolTip("展开 / 折叠预警详情")
        self.warn_toggle.clicked.connect(self._toggle_warn)
        self.warn_toggle.hide()
        root.addWidget(self.warn_toggle)

        self.warn_box = QTextEdit()
        self.warn_box.setObjectName("WarnBox")
        self.warn_box.setReadOnly(True)
        self.warn_box.setMaximumHeight(120)
        self.warn_box.setMinimumHeight(48)
        self.warn_box.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.warn_box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.warn_box.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.warn_box.document().setDocumentMargin(2)
        self.warn_box.hide()
        root.addWidget(self.warn_box)
        self._warn_expanded = False

        # 空闲态用一行提示，不占一整块空白；有结果后再展开通道表
        self.idle_hint = QLabel("巡检后显示通道列表")
        self.idle_hint.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.idle_hint.setStyleSheet(
            "color: " + theme.ui_color("muted", False) + "; font-size: 12px;"
        )
        root.addWidget(self.idle_hint)

        self.channel_table = ChannelTableView()
        self.channel_table.setMinimumHeight(180)
        self.channel_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.channel_table.view.doubleClicked.connect(self._on_channel_double_click)
        self.channel_table.detail_requested.connect(self.open_channel_detail)
        self.channel_table.export_requested.connect(self.export_requested.emit)
        self.channel_table.expand_requested.connect(self.show_results_window)
        self.channel_table.single_check_requested.connect(
            self.single_check_requested.emit
        )
        self.channel_table.hide()
        root.addWidget(self.channel_table, 1)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._sync_result_chrome()

    def _channel_record_at(self, index) -> Optional[Dict[str, Any]]:
        src = self.channel_table.proxy.mapToSource(index)
        return self.channel_table.model.record_at(src.row())

    def _on_channel_double_click(self, index) -> None:
        rec = self._channel_record_at(index)
        if rec:
            self.open_channel_detail(rec)

    # ---------- 配色 ----------

    def set_dark(self, dark: bool) -> None:
        self.channel_table.set_dark(dark)
        for card in self._metric_cards.values():
            card.set_dark(dark)
        self.device_sub_label.setStyleSheet(
            f"color: {theme.ui_color('muted', dark)}; font-size: 11px;"
        )
        self.idle_hint.setStyleSheet(
            f"color: {theme.ui_color('muted', dark)}; font-size: 12px;"
        )
        if self._results_window is not None:
            self._results_window.set_dark(dark)
        if self._detail_dialog is not None:
            self._detail_dialog.set_dark(dark)
        # A5：主题切换后预警区 HTML 按当前主题重绘，避免颜色残留
        if self._last_warn_lines is not None:
            self.warn_box.setHtml(self._warn_html(self._last_warn_lines, dark))
            self.warn_box.document().setDocumentMargin(2)
            arrow = "▼" if self._warn_expanded else "▶"
            self.warn_toggle.setText(f"{arrow}  {summarize_warn_lines(self._last_warn_lines)}")

    # ---------- 详情窗 / 大窗 ----------

    def open_channel_detail(self, rec: Dict[str, Any]) -> None:
        if self._detail_dialog is None:
            self._detail_dialog = ChannelDetailDialog({}, self)
        dlg = self._detail_dialog
        dlg.set_record(rec)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def show_results_window(self) -> None:
        if not self._last_result:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(self, "大窗显示", "暂无巡检结果，请先执行一次巡检。")
            return
        win = self._results_window
        if win is None:
            win = ResultsExpandWindow(self)
            win.export_requested.connect(self.export_requested.emit)
            win.detail_requested.connect(self.open_channel_detail)
            win.single_check_requested.connect(self.single_check_requested.emit)
            self._results_window = win
        self._push_to_results_window(win)
        win.show()
        win.raise_()
        win.activateWindow()

    def _push_to_results_window(self, win: ResultsExpandWindow) -> None:
        dark = theme.effective_dark()
        data = self._last_result or {}
        records = list(data.get("records") or [])
        name = str(data.get("device_name") or "").strip()
        n = len(records)
        title = f"通道列表 · {name}（{n} 路）" if name else f"通道列表（{n} 路）"
        win.apply_snapshot(
            title=title,
            records=records,
            deep=self._last_channel_deep,
            dark=dark,
        )
        win.setWindowTitle(f"通道列表 — {name}" if name else "通道列表")

    def refresh_results_window(self) -> None:
        win = self._results_window
        if win is not None and win.isVisible():
            self._push_to_results_window(win)

    # ---------- 结果渲染 ----------

    @staticmethod
    def _warn_html(lines: List[str], dark: bool) -> str:
        """预警区彩色富文本（紧凑行高，减少空白）。"""
        c = theme.ui_color
        err_words = ("离线", "无录像", "异常", "未启用", "错误", "失败", "不足", "故障", "无权限", "疑似")
        # 行高约 1.2、字号 12、上下无外边距，避免 QTextEdit 看起来「一大块空白」
        row = (
            "margin:0;padding:0;line-height:1.2;font-size:12px;"
        )
        parts = []
        for ln in lines:
            s = ln.strip()
            if not s:
                continue
            # 条目前导空白收紧
            if s.startswith("•") or s.startswith("-"):
                s = "· " + s.lstrip("•- ").strip()
            if s == "预警：无":
                parts.append(f'<p style="{row}color:{c("muted", dark)};">{s}</p>')
            elif s == "预警：" or s == "预警:":
                parts.append(
                    f'<p style="{row}color:{c("step", dark)};font-weight:600;">{s}</p>'
                )
            elif s.startswith("· ") or s.startswith("•"):
                color = c("error", dark) if any(w in s for w in err_words) else c("warn", dark)
                parts.append(f'<p style="{row}color:{color};">{s}</p>')
            elif s.startswith("硬盘"):
                parts.append(f'<p style="{row}color:{c("info", dark)};">{s}</p>')
            elif s.startswith("循环覆盖"):
                if "未开启" in s and "推断" not in s:
                    color = c("error", dark)
                elif "未开启" in s or "未知" in s:
                    color = c("warn", dark)
                elif "已开启" in s:
                    color = c("ok", dark)
                else:
                    color = c("info", dark)
                parts.append(f'<p style="{row}color:{color};">{s}</p>')
            elif s.startswith("抽检"):
                parts.append(f'<p style="{row}color:{c("step", dark)};">{s}</p>')
            else:
                parts.append(f'<p style="{row}color:{c("muted", dark)};">{s}</p>')
        body = "".join(parts) if parts else f'<p style="{row}color:{c("muted", dark)};">—</p>'
        return (
            f'<body style="margin:0;padding:0;">{body}</body>'
        )

    def _toggle_warn(self) -> None:
        if self._last_warn_lines is None:
            return
        self._warn_expanded = not self._warn_expanded
        self.warn_box.setVisible(self._warn_expanded)
        arrow = "▼" if self._warn_expanded else "▶"
        self.warn_toggle.setText(
            f"{arrow}  {summarize_warn_lines(self._last_warn_lines)}"
        )

    def _sync_result_chrome(self) -> None:
        filled = self._last_result is not None
        self.idle_hint.setVisible(not filled)
        self.channel_table.setVisible(filled)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding if filled else QSizePolicy.Policy.Preferred,
        )
        self.layout_mode_changed.emit(filled)

    def clear_result(self) -> None:
        self._last_result = None
        self._last_warn_lines = None
        self._last_metrics_snapshot = []
        self.summary_label.setText(IDLE_SUMMARY)
        self.device_sub_label.clear()
        self.device_sub_label.hide()
        self.channel_table.set_records([], False)
        self.channel_table.set_result_actions_enabled(False)
        self.warn_box.clear()
        self.warn_box.hide()
        self.warn_toggle.hide()
        self._warn_expanded = False
        self._sync_result_chrome()
        for key, card in self._metric_cards.items():
            card._title_text = METRIC_TITLES.get(key, card._title_text)
            card.reset_idle()

    def render_result(self, data: Dict[str, Any]) -> None:
        self._last_result = data
        health = data.get("health") or {}
        stats = health.get("统计") or {}
        status = health.get("健康状态", "未知")
        info = data.get("info") or {}
        records = data.get("records") or []
        deep = bool(data.get("deep_av"))
        name = data.get("device_name") or ""
        self._last_channel_deep = deep

        bits = [f"健康：{status}"]
        if name:
            bits.append(str(name))
        model = (info.get("型号") or "").strip()
        if model and model != "-":
            bits.append(model)
        bits.append(f"{len(records)} 路")
        self.summary_label.setText("  ·  ".join(bits))
        fw = (info.get("固件版本") or "").strip()
        self.summary_label.setToolTip(f"固件 {fw}" if fw and fw != "-" else "")
        self.device_sub_label.hide()

        online = stats.get("摄像头在线", 0)
        offline = stats.get("摄像头离线", 0)
        # 两个口径不要混用:摄像头数是 physical channel 数,
        # 而录像/落盘/音频计数都基于 records(Track),分母必须用通道数,
        # 否则一通道多 Track 的机型会出现 "5/4" 这类失真比例。
        cam_total = stats.get("摄像头总数", len(records))
        rec_total = len(records)
        tone = "ok" if status == "良好" else ("bad" if status == "严重" else "warn")

        disk_checked = bool(stats.get("落盘已检查", True))
        record_checked = bool(stats.get("录像已检查", disk_checked))

        rec_ok = stats.get("录像正常", 0)
        rec_bad = stats.get("录像异常", 0)
        if not record_checked:
            rec_title, rec_value, rec_tone = "录像正常", "未检查", "muted"
        else:
            rec_title, rec_value, rec_tone = "录像正常", f"{rec_ok}/{rec_total}", ("bad" if rec_bad else "ok")

        disk_ok = stats.get("落盘正常", 0)
        disk_bad = stats.get("落盘异常", 0)
        if not disk_checked:
            disk_title, disk_value, disk_tone = "近期有录像", "未检查", "muted"
        else:
            disk_title = "近期有录像"
            disk_value = f"{disk_ok}/{rec_total}"
            if stats.get("落盘未知", 0) and not disk_ok and not disk_bad:
                disk_value, disk_tone = "未检查", "muted"
            else:
                disk_tone = "bad" if disk_bad else "ok"

        audio_yes = stats.get("含音频", 0)
        # 音频卡片有两套口径，不能混：
        #   非深度抽检 → 看「录像配置是否开音频」(SaveAudio)；
        #   深度抽检   → 看「实测能不能拉到音轨」(音频抽检)。
        # 配置开了不等于拉得到音轨（无音轨=异常），配置没开也不算抽检失败（跳过）。
        # 通道数为 0（空结果集）时应显示「未检查」，不能落成黄色告警。
        if not rec_total:
            audio_title, audio_value, audio_tone, audio_tip = (
                "含音频配置",
                "未检查",
                "muted",
                "本次无通道记录",
            )
            audio_line = ""
        elif deep:
            a_ok = a_warn = a_bad = a_skip = a_unknown = 0
            for r in records:
                st = r.get("音频抽检")
                if st == "正常":
                    a_ok += 1
                elif st == "警告":
                    a_warn += 1
                elif st == "异常":
                    a_bad += 1
                elif st == "跳过":
                    a_skip += 1
                else:  # 未知 / 字段缺失
                    a_unknown += 1
            sampled = a_ok + a_warn + a_bad + a_unknown
            audio_title = "音频实测"
            if not sampled:
                audio_value, audio_tone = "未采样", "muted"
            else:
                audio_value = f"{a_ok + a_warn}/{sampled}"
                if a_bad:
                    audio_tone = "bad"
                elif a_warn or a_unknown:
                    audio_tone = "warn"
                else:
                    audio_tone = "ok"
            parts = [f"正常 {a_ok}"]
            if a_warn:
                parts.append(f"静音警告 {a_warn}")
            if a_bad:
                parts.append(f"无音轨 {a_bad}")
            if a_unknown:
                parts.append(f"未确认 {a_unknown}")
            if a_skip:
                parts.append(f"未开音频/跳过 {a_skip}")
            audio_tip = " · ".join(parts)
            audio_line = f" · 音频实测 {a_ok + a_warn}/{sampled}" if sampled else " · 音频未采样"
            if a_bad:
                audio_line += f"(无音轨 {a_bad})"
        else:
            audio_title = "含音频配置"
            audio_value = f"{audio_yes}/{rec_total}"
            audio_tone = "ok" if audio_yes == rec_total else "warn"
            a_unknown = int(stats.get("音频未知", 0) or 0)
            audio_tip = f"已开启 {audio_yes} / 共 {rec_total}"
            if a_unknown:
                audio_tip += f"，另有 {a_unknown} 路状态未知"
            audio_line = ""

        # 摄像头在线卡：离线 → bad；「在线但通道检测状态异常」→ warn；
        # 在线状态未确认（online 缺失且检测状态也给不出结论）只在 tooltip 里说明，
        # 不进卡片数值，避免把未确认当成异常。
        cam_unconfirmed = int(stats.get("摄像头状态未确认", 0) or 0)
        cam_detect_bad = int(stats.get("通道检测异常", 0) or 0)
        online_tone = "bad" if offline else ("warn" if cam_detect_bad else "ok")
        online_tip_parts = []
        if cam_detect_bad:
            online_tip_parts.append(f"{cam_detect_bad} 路在线但通道检测异常(IP 冲突/网络不可达等)")
        if cam_unconfirmed:
            online_tip_parts.append(f"{cam_unconfirmed} 路在线状态未确认")
        online_tip = "；".join(online_tip_parts)

        metrics_snap: List[tuple] = [
            ("health", "健康状态", str(status), tone, ""),
            ("online", "摄像头在线", f"{online}/{cam_total}", online_tone, online_tip),
            ("record", rec_title, rec_value, rec_tone, ""),
            ("disk", disk_title, disk_value, disk_tone, ""),
            ("audio", audio_title, audio_value, audio_tone, audio_tip),
        ]
        self._last_metrics_snapshot = metrics_snap
        for key, title, value, m_tone, tip in metrics_snap:
            self._metric_cards[key].set_metric(title, value, m_tone, tip)

        # 预警区
        warns = list(health.get("预警信息") or [])
        if not disk_checked or not record_checked:
            filtered = []
            for w in warns:
                s = str(w)
                if not disk_checked and ("落盘" in s or "近期录像" in s or "近期无录像" in s or "录像片段" in s):
                    continue
                if not record_checked and (
                    "无正常落盘" in s
                    or "近期无录像" in s
                    or "落盘状态未知" in s
                    or "近期录像状态未知" in s
                    or ("录像" in s and "计划" not in s and "音频" not in s)
                ):
                    continue
                filtered.append(w)
            warns = filtered

        drives = data.get("drives") or []
        lines: List[str] = []
        if warns:
            lines.append("预警：")
            for w in warns:
                lines.append(f"  • {w}")
        else:
            lines.append("预警：无")
        if not disk_checked and not record_checked:
            lines.append("说明：本次未查近期录像状态（快速模式仅配置查询）")
        ow = data.get("disk_overwrite") or health.get("循环覆盖") or {}
        if ow:
            lab = ow.get("label") or stats.get("循环覆盖") or "未知"
            detail = (ow.get("detail") or "").strip()
            if detail and len(detail) > 80:
                detail = detail[:80] + "…"
            lines.append(
                f"循环覆盖：{lab}" + (f"（{detail}）" if detail else "")
            )
        if drives:
            lines.append(
                "硬盘：" + "  |  ".join(
                    f"{d.get('盘符')} {d.get('状态')} {d.get('使用率')}" for d in drives
                )
            )
        if data.get("av_save_dir"):
            lines.append(f"片段目录：{data['av_save_dir']}")
        if deep:
            lines.append(
                f"抽检：视频 正常{stats.get('视频抽检正常', 0)}/"
                f"异常{stats.get('视频抽检异常', 0)}  "
                f"音频 正常{stats.get('音频抽检正常', 0)}/"
                f"异常{stats.get('音频抽检异常', 0)}/"
                f"警告{stats.get('音频抽检警告', 0)}/"
                f"未确认{stats.get('音频抽检未知', 0)}"
            )
        warn_html = self._warn_html(lines, theme.effective_dark())
        self._last_warn_lines = list(lines)
        self.warn_box.setHtml(warn_html)
        self.warn_box.document().setDocumentMargin(2)
        summary = summarize_warn_lines(lines)
        self._warn_expanded = False
        self.warn_toggle.setText(f"▶  {summary}")
        self.warn_toggle.setVisible(True)
        self.warn_box.hide()

        self.channel_table.set_records(records, deep)
        self.channel_table.set_result_actions_enabled(True)
        self._sync_result_chrome()
        self.refresh_results_window()

        issue_n = 0
        for r in records:
            tag = row_tag(r, deep)
            if tag not in ("bad", "warn"):
                continue
            issue_n += 1
            detail = r.get("抽检详情") or r.get("落盘详情") or ""
            if detail and detail not in ("未启用深度抽检", "未检查", "跳过"):
                self.log_requested.emit(
                    f"通道 {r.get('通道')} {r.get('名称') or ''}: {detail}",
                    "warn" if tag == "warn" else "error",
                )

        self.log_requested.emit(
            f"巡检完成 · 健康 {status} · 在线 {online}/{cam_total}"
            f" · 离线 {offline} · 通道 {rec_total} · 录像异常 {rec_bad} · 近期无录像 {disk_bad}"
            + audio_line
            + (f" · 问题通道 {issue_n}" if issue_n else ""),
            "ok" if status == "良好" else ("error" if status == "严重" else "warn"),
        )

    def set_result_actions_enabled(self, enabled: bool) -> None:
        has_result = self._last_result is not None
        self.channel_table.set_result_actions_enabled(enabled and has_result)
        if self._results_window is not None:
            self._results_window.btn_export.setEnabled(enabled and has_result)

    # ---------- 单路深度抽检 ----------

    def set_single_check_running(self, running: bool) -> None:
        """单路抽检进行中：主表与大窗的按钮同步禁用。"""
        self.channel_table.set_single_check_running(running)
        if self._results_window is not None:
            self._results_window.channel_table.set_single_check_running(running)

    def apply_single_check_result(
        self, originals: List[Dict[str, Any]], updated: List[Dict[str, Any]]
    ) -> None:
        """把单路抽检结果回写进当前结果集(按传入顺序一一对应)。

        originals 是结果集里的**原记录**(渲染时传给表格的同一批 dict)，
        updated 是 run_single_av_check 返回的副本；只回写抽检相关字段，
        健康汇总不动 —— 单路复查是诊断动作，不代表整机重新巡检。
        """
        if not self._last_result:
            return
        fields = AV_FIELDS
        for orig, upd in zip(originals, updated):
            if orig is None or upd is None:
                continue
            patch = {k: upd.get(k) for k in fields if k in upd}
            self.channel_table.model.update_record_fields(orig, patch)
        # 抽检列之前可能没展示(快速巡检结果)，抽检完成后切到深度列
        if not self._last_channel_deep:
            self._last_channel_deep = True
            self.channel_table.set_records(
                list(self._last_result.get("records") or []), True
            )
            self._sync_result_chrome()
        self.refresh_results_window()

    @property
    def last_result(self) -> Optional[Dict[str, Any]]:
        return self._last_result

    def warn_text(self) -> str:
        return self.warn_box.toPlainText()

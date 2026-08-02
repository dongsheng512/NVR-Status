"""通道表：QAbstractTableModel + QSortFilterProxyModel + QTableView。

支持排序、筛选（仅异常 / 仅离线）、状态着色、选中复制。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from services.export_report import fmt_bool_audio, fmt_online, fmt_status
from ui import theme

BASE_COLUMNS = ("ch", "name", "online", "audio", "disk", "record")
DEEP_COLUMNS = ("ch", "name", "online", "audio", "disk", "record", "vchk", "achk")
COLUMN_LABELS = {
    "ch": "通道",
    "name": "名称",
    "online": "在线",
    "audio": "含音频",
    "disk": "近期录像",
    "record": "录像",
    "vchk": "视频抽检",
    "achk": "音频抽检",
}
COLUMN_WIDTHS = {
    "ch": 60,
    "name": 200,
    "online": 60,
    "audio": 68,
    "disk": 90,
    "record": 76,
    "vchk": 86,
    "achk": 86,
}


def row_tag(rec: Dict[str, Any], deep: bool) -> str:
    """状态判定：ok / warn / error / muted，语义对齐 v1。"""
    if (
        rec.get("在线") == "false"
        or rec.get("落盘状态") == "异常"
        or rec.get("录像是否正常") in ("异常", "未配置")
    ):
        return "error"
    if deep and (
        rec.get("视频抽检") == "异常" or rec.get("音频抽检") in ("异常", "警告")
    ):
        return "warn" if rec.get("音频抽检") == "警告" else "error"
    if rec.get("落盘状态") == "跳过" or rec.get("录像是否正常") in ("未知", "跳过"):
        return "muted"
    return "ok"


def _display(rec: Dict[str, Any], col: str) -> str:
    if col == "ch":
        return str(rec.get("通道", ""))
    if col == "name":
        return str(rec.get("名称") or "—")
    if col == "online":
        return fmt_online(rec.get("在线"))
    if col == "audio":
        return fmt_bool_audio(rec.get("录像含音频"))
    if col == "disk":
        return fmt_status(rec.get("落盘状态"))
    if col == "record":
        return fmt_status(rec.get("录像是否正常"))
    if col == "vchk":
        return fmt_status(rec.get("视频抽检"))
    if col == "achk":
        return fmt_status(rec.get("音频抽检"))
    return ""


def _sort_key(rec: Dict[str, Any], col: str) -> Any:
    if col == "ch":
        try:
            return (0, int(str(rec.get("通道", "")).strip() or 0))
        except ValueError:
            return (1, str(rec.get("通道", "")))
    return _display(rec, col)


class ChannelTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._records: List[Dict[str, Any]] = []
        self._deep = False
        self._dark = False

    # ---- 数据 ----
    def set_records(self, records: Optional[List[Dict[str, Any]]], deep: bool) -> None:
        self.beginResetModel()
        self._records = list(records or [])
        self._deep = bool(deep)
        self.endResetModel()

    def set_dark(self, dark: bool) -> None:
        if dark != self._dark:
            self._dark = dark
            top_left = self.index(0, 0)
            bottom_right = self.index(max(self.rowCount() - 1, 0), max(self.columnCount() - 1, 0))
            self.dataChanged.emit(top_left, bottom_right, [Qt.ItemDataRole.ForegroundRole, Qt.ItemDataRole.BackgroundRole])

    def records(self) -> List[Dict[str, Any]]:
        return self._records

    def deep(self) -> bool:
        return self._deep

    def columns(self) -> tuple:
        return DEEP_COLUMNS if self._deep else BASE_COLUMNS

    def record_at(self, row: int) -> Optional[Dict[str, Any]]:
        if 0 <= row < len(self._records):
            return self._records[row]
        return None

    # ---- Qt API ----
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._records)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns())

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        rec = self._records[index.row()]
        col = self.columns()[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return _display(rec, col)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignCenter) if col != "name" else int(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
        if role == Qt.ItemDataRole.ForegroundRole:
            tag = row_tag(rec, self._deep)
            color_key = {
                "ok": "ok",
                "warn": "warn",
                "error": "error",
                "muted": "muted",
            }[tag]
            return QColor(theme.ui_color(color_key, self._dark))
        if role == Qt.ItemDataRole.BackgroundRole:
            color = theme.ZEBRA_ODD["dark" if self._dark else "light"] if index.row() % 2 else theme.ZEBRA_EVEN["dark" if self._dark else "light"]
            return QColor(color)
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(rec, col)
        if role == Qt.ItemDataRole.FontRole and col == "name":
            f = QFont()
            f.setBold(False)
            return f
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMN_LABELS.get(self.columns()[section], "")
        if orientation == Qt.Orientation.Vertical and role == Qt.ItemDataRole.DisplayRole:
            return str(section + 1)
        return None

    def _tooltip(self, rec: Dict[str, Any], col: str) -> str:
        lines = []
        if col in ("name", "ch"):
            if rec.get("名称") and rec["名称"] != "未知":
                lines.append(f"名称: {rec['名称']}")
            if rec.get("IP") and rec["IP"] != "未知":
                lines.append(f"IP: {rec['IP']}")
        if col == "disk":
            detail = rec.get("落盘详情")
            if detail:
                lines.append(f"近期录像: {detail}")
        if col in ("record", "vchk", "achk"):
            detail = rec.get("抽检详情")
            if detail:
                lines.append(detail)
            if col == "record" and rec.get("录像模式"):
                lines.append(f"录像模式: {rec['录像模式']}")
        return "\n".join(lines) if lines else None


class ChannelFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._only_abnormal = False
        self._only_offline = False

    def set_only_abnormal(self, on: bool) -> None:
        self._only_abnormal = bool(on)
        self.invalidateFilter()

    def set_only_offline(self, on: bool) -> None:
        self._only_offline = bool(on)
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model: ChannelTableModel = self.sourceModel()
        rec = model.record_at(source_row)
        if rec is None:
            return True
        if self._only_offline and rec.get("在线") != "false":
            return False
        if self._only_abnormal and row_tag(rec, model.deep()) not in ("error", "warn"):
            return False
        return True

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        model: ChannelTableModel = self.sourceModel()
        cols = model.columns()
        col = cols[left.column()]
        l_rec = model.record_at(left.row())
        r_rec = model.record_at(right.row())
        if l_rec is None or r_rec is None:
            return super().lessThan(left, right)
        lk, rk = _sort_key(l_rec, col), _sort_key(r_rec, col)
        if isinstance(lk, tuple) and isinstance(rk, tuple):
            return lk < rk
        return str(lk) < str(rk)


class ChannelTableView(QWidget):
    export_requested = Signal()
    expand_requested = Signal()
    detail_requested = Signal(object)

    def __init__(self, parent=None, *, show_result_actions: bool = True):
        super().__init__(parent)
        self._deep = False
        self._dark = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # 同一行：计数 | 拉伸 | 仅异常 仅离线 | 导出结果 大窗显示
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.count_label = QLabel("通道 0 路")
        self.count_label.setStyleSheet("color: " + theme.ui_color("muted", False) + ";")
        toolbar.addWidget(self.count_label)
        toolbar.addStretch(1)
        self.chk_abnormal = QCheckBox("仅异常")
        self.chk_offline = QCheckBox("仅离线")
        toolbar.addWidget(self.chk_abnormal)
        toolbar.addWidget(self.chk_offline)

        # 与运行日志「复制/清空」同尺寸（FormField + 固定高度 24）
        self.btn_export = QPushButton("导出结果")
        self.btn_export.setObjectName("FormField")
        self.btn_export.setProperty("role", "primary")
        self.btn_export.setToolTip("导出巡检结果为 CSV / TXT")
        self.btn_export.setEnabled(False)
        self.btn_export.setFixedHeight(24)
        self.btn_export.clicked.connect(self.export_requested.emit)

        self.btn_expand = QPushButton("大窗显示")
        self.btn_expand.setObjectName("FormField")
        self.btn_expand.setToolTip("在独立大窗口中仅放大查看通道列表")
        self.btn_expand.setEnabled(False)
        self.btn_expand.setFixedHeight(24)
        self.btn_expand.clicked.connect(self.expand_requested.emit)

        if show_result_actions:
            toolbar.addWidget(self.btn_export)
            toolbar.addWidget(self.btn_expand)
        else:
            self.btn_export.hide()
            self.btn_expand.hide()
        root.addLayout(toolbar)

        self.model = ChannelTableModel(self)
        self.proxy = ChannelFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.ItemDataRole.DisplayRole)

        self.view = QTableView(self)
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setAlternatingRowColors(False)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.verticalHeader().setDefaultSectionSize(24)
        self.view.verticalHeader().setVisible(True)
        self.view.horizontalHeader().setStretchLastSection(False)
        header = self.view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for i, col in enumerate(BASE_COLUMNS):
            self.view.setColumnWidth(i, COLUMN_WIDTHS[col])
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._show_context_menu)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        self.placeholder = QLabel(
            "尚未扫描 — 配置设备后点击「快速巡检」或「深度巡检」"
        )
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.placeholder.setStyleSheet(
            "color: " + theme.ui_color("muted", False) + ";"
        )
        self.stack.addWidget(self.placeholder)
        root.addWidget(self.stack, 1)

        self.chk_abnormal.toggled.connect(self.proxy.set_only_abnormal)
        self.chk_offline.toggled.connect(self.proxy.set_only_offline)
        self.proxy.modelReset.connect(self._refresh_count)
        self.proxy.rowsInserted.connect(self._refresh_count)
        self.proxy.rowsRemoved.connect(self._refresh_count)

        QShortcut(QKeySequence("Ctrl+C"), self.view, activated=self.copy_selection)
        QShortcut(QKeySequence("Ctrl+Shift+C"), self.view, activated=self.copy_all)

        self._sync_stack()

    def _sync_stack(self) -> None:
        empty = self.model.rowCount() == 0
        self.stack.setCurrentWidget(self.placeholder if empty else self.view)

    def set_records(self, records: Optional[List[Dict[str, Any]]], deep: bool) -> None:
        self._deep = bool(deep)
        self.model.set_records(records, deep)
        cols = DEEP_COLUMNS if self._deep else BASE_COLUMNS
        self.view.setSortingEnabled(False)
        for i, col in enumerate(cols):
            self.view.setColumnWidth(i, COLUMN_WIDTHS[col])
            self.view.setColumnHidden(i, False)
        self.view.setSortingEnabled(True)
        self._refresh_count()
        self._sync_stack()

    def set_dark(self, dark: bool) -> None:
        self._dark = dark
        self.model.set_dark(dark)
        muted = theme.ui_color("muted", dark)
        self.count_label.setStyleSheet(f"color: {muted};")
        self.placeholder.setStyleSheet(f"color: {muted};")

    def set_result_actions_enabled(self, enabled: bool) -> None:
        """启用/禁用导出与大窗按钮。"""
        self.btn_export.setEnabled(enabled)
        self.btn_expand.setEnabled(enabled)

    def _refresh_count(self) -> None:
        n = self.proxy.rowCount()
        total = self.model.rowCount()
        if total:
            self.count_label.setText(f"显示 {n} / 通道 {total} 路")
        else:
            self.count_label.setText("通道 0 路")
        self._sync_stack()

    def selected_records(self) -> List[Dict[str, Any]]:
        out = []
        for idx in self.view.selectionModel().selectedRows():
            src = self.proxy.mapToSource(idx)
            rec = self.model.record_at(src.row())
            if rec:
                out.append(rec)
        return out

    def copy_selection(self) -> None:
        """复制选中行（制表符分隔）到剪贴板。"""
        rows = self.selected_records()
        if rows:
            self._copy_records(rows)

    def copy_all(self) -> None:
        self._copy_records(self.model.records())

    def _copy_records(self, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return
        import csv
        import io

        cols = DEEP_COLUMNS if self._deep else BASE_COLUMNS
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([COLUMN_LABELS[c] for c in cols])
        for r in rows:
            w.writerow([_display(r, c) for c in cols])
        QApplication.clipboard().setText(buf.getvalue())

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        act_copy = menu.addAction("复制选中")
        act_copy.setShortcut(QKeySequence.StandardKey.Copy)
        act_copy.triggered.connect(self.copy_selection)
        act_copy_all = menu.addAction("复制全部")
        act_copy_all.triggered.connect(self.copy_all)
        menu.addSeparator()
        act_detail = menu.addAction("通道详情")
        act_detail.triggered.connect(self._emit_detail)
        act_export = menu.addAction("导出结果…")
        act_export.triggered.connect(self.export_requested)
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def _emit_detail(self) -> None:
        recs = self.selected_records()
        if not recs:
            recs = self.model.records()[:1]
        if recs:
            self.detail_requested.emit(recs[0])


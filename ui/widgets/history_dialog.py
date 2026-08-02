"""历史报告对话框：列表浏览已归档巡检结果，可查看 / 再导出。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from services import export_report, history
from ui import theme


class HistoryDialog(QDialog):
    """历史报告列表（B6）。

    信号：report_loaded(dict) — 用户点击「查看」时带完整结果发出，由主窗渲染。
    """

    report_loaded = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("历史报告")
        self.setMinimumSize(720, 420)
        self.resize(860, 520)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        head = QHBoxLayout()
        self.summary = QLabel("")
        self.summary.setStyleSheet(
            "color: " + theme.ui_color("muted", theme.effective_dark()) + ";"
        )
        head.addWidget(self.summary, 1)
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.clicked.connect(self._load)
        head.addWidget(self.btn_refresh)
        root.addLayout(head)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["时间", "设备", "IP", "健康状态", "通道数"]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.doubleClicked.connect(self._view)
        root.addWidget(self.table, 1)

        tip = QLabel("双击行或选中后点「查看」渲染该次结果；「导出」可另存为 CSV / TXT")
        self.tip_label = tip
        tip.setStyleSheet(
            "color: " + theme.ui_color("muted", theme.effective_dark()) + "; font-size: 11px;"
        )
        root.addWidget(tip)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_view = QPushButton("查看")
        self.btn_view.clicked.connect(self._view)
        self.btn_export = QPushButton("导出")
        self.btn_export.clicked.connect(self._export)
        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.accept)
        for b in (self.btn_view, self.btn_export, self.btn_close):
            btns.addWidget(b)
        root.addLayout(btns)

        self._meta: List[Dict[str, Any]] = []
        self._load()

    def set_dark(self, dark: bool) -> None:
        muted = theme.ui_color("muted", dark)
        self.summary.setStyleSheet(f"color: {muted};")
        self.tip_label.setStyleSheet(f"color: {muted}; font-size: 11px;")

    def _load(self) -> None:
        self._meta = history.list_reports()
        self.table.setRowCount(len(self._meta))
        for i, m in enumerate(self._meta):
            self.table.setItem(i, 0, QTableWidgetItem(m["saved_at"]))
            self.table.setItem(i, 1, QTableWidgetItem(m["device_name"]))
            self.table.setItem(i, 2, QTableWidgetItem(m["ip"]))
            self.table.setItem(i, 3, QTableWidgetItem(m["health_status"]))
            self.table.setItem(i, 4, QTableWidgetItem(str(m["channels"])))
            if m["error"]:
                self.table.item(i, 1).setToolTip(m["error"])
                self.table.item(i, 3).setText(m["error"][:40])
        self.summary.setText(f"共 {len(self._meta)} 份报告")

    def refresh(self) -> None:
        self._load()

    def _selected(self) -> Optional[Dict[str, Any]]:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._meta):
            return None
        return self._meta[row]

    def _view(self) -> None:
        m = self._selected()
        if m is None:
            QMessageBox.information(self, "提示", "请先选择一份报告")
            return
        data = history.load_report(m["path"])
        if not data:
            QMessageBox.warning(self, "无法读取", "报告文件缺失或已损坏")
            return
        self.report_loaded.emit(data)
        self.accept()

    def _export(self) -> None:
        m = self._selected()
        if m is None:
            QMessageBox.information(self, "提示", "请先选择一份报告")
            return
        data = history.load_report(m["path"])
        if not data:
            QMessageBox.warning(self, "无法读取", "报告文件缺失或已损坏")
            return
        name = str(data.get("device_name") or "NVR").replace("/", "_").replace(" ", "_")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出历史报告",
            f"nvr_report_{name}_{stamp}.csv",
            "CSV 表格 (*.csv);;文本报告 (*.txt);;全部 (*.*)",
        )
        if not path:
            return
        try:
            if path.lower().endswith(".txt"):
                export_report.export_txt(path, data, self._warn_text(data))
            else:
                if not path.lower().endswith(".csv"):
                    path += ".csv"
                export_report.export_csv(path, data)
            QMessageBox.information(self, "导出成功", f"已保存到：\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    @staticmethod
    def _warn_text(data: Dict[str, Any]) -> str:
        lines = list((data.get("health") or {}).get("预警信息") or [])
        return "\n".join(lines)

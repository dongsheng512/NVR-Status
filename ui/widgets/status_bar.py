"""底部状态栏：状态点 + 文案 + 详情 + 百分比 + 进度条。"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QWidget

from ui import theme

_STATE_LABELS = {
    "ready": "就绪",
    "running": "扫描中",
    "ok": "完成",
    "warn": "完成",
    "error": "失败",
}
_STATE_DETAILS = {
    "ready": "等待开始巡检",
    "running": "正在巡检…",
    "ok": "巡检完成",
    "warn": "巡检完成（有预警）",
    "error": "巡检失败",
}


class StatusBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 6, 12, 6)
        root.setSpacing(8)

        self.dot = QLabel("●")
        self.dot.setFixedWidth(20)
        self.dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.dot)

        self.state_label = QLabel("就绪")
        f = self.state_label.font()
        f.setBold(True)
        self.state_label.setFont(f)
        root.addWidget(self.state_label)

        self.detail_label = QLabel("等待开始巡检")
        self.detail_label.setStyleSheet("color: " + theme.ui_color("muted", False) + ";")
        root.addWidget(self.detail_label, 1)

        self.pct_label = QLabel("")
        self.pct_label.setFixedWidth(44)
        self.pct_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.pct_label.setStyleSheet("color: " + theme.ui_color("muted", False) + ";")
        root.addWidget(self.pct_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFixedWidth(220)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        self._state = "ready"
        self._dark = False
        self._set_state("ready")

    def set_dark(self, dark: bool) -> None:
        self._dark = dark
        muted = theme.ui_color("muted", dark)
        self.detail_label.setStyleSheet(f"color: {muted};")
        self.pct_label.setStyleSheet(f"color: {muted};")
        self._refresh_colors()

    def set_state(self, state: str, text: Optional[str] = None, detail: str = "") -> None:
        self._set_state(state, text, detail)

    def _set_state(self, state: str, text: Optional[str] = None, detail: str = "") -> None:
        state = state if state in _STATE_LABELS else "ready"
        self._state = state
        self.state_label.setText(text if text is not None else _STATE_LABELS[state])
        self.detail_label.setText(detail or _STATE_DETAILS[state])
        self._refresh_colors()

    def _refresh_colors(self) -> None:
        dot_color, bar_color = theme.STATE_DOT.get(self._state, theme.STATE_DOT["ready"])
        if self._dark:
            dot_color, bar_color = theme.STATE_DOT.get(self._state, theme.STATE_DOT["ready"])
            bar_color = bar_color
        self.dot.setStyleSheet(f"color: {dot_color}; font-size: 14px;")
        self.state_label.setStyleSheet(f"color: {dot_color};")
        self.progress.setStyleSheet(
            f"QProgressBar::chunk {{ background: {bar_color}; border-radius: 5px; }}"
        )

    def set_progress(self, fraction: float, current=None, total=None) -> None:
        fraction = max(0.0, min(1.0, float(fraction)))
        self.progress.setValue(int(round(fraction * 1000)))
        if current is not None and total:
            try:
                self.pct_label.setText(f"{int(current)}/{int(total)}")
                return
            except (TypeError, ValueError):
                pass
        self.pct_label.setText(f"{int(round(fraction * 100))}%")

    def reset_progress(self) -> None:
        self.progress.setValue(0)
        self.pct_label.setText("")

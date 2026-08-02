"""运行日志面板：日志区 + 提示 + 图例 + 复制/清空/自动滚动。

B1 拆分：从 ui/main_window 抽出，主窗只组装。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui import theme

LOG_MAX_LINES = 5000


class LogPanel(QGroupBox):
    """运行日志面板：彩色分级日志 + 提示行 + 图例 + 复制/清空/自动滚动。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__("运行日志", parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._log_line_count = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(2)

        head = QHBoxLayout()
        head.setSpacing(6)
        self._log_hint = QLabel("等待操作…")
        self._log_hint.setStyleSheet("color: " + theme.ui_color("muted", False) + ";")
        head.addWidget(self._log_hint, 1)

        # 图例并入标题行，省掉一整行高度
        self._legend_labels: list[QLabel] = []
        for text, key in (("步骤", "step"), ("成功", "ok"), ("警告", "warn"), ("错误", "error")):
            lbl = QLabel(f"●{text}")
            lbl.setProperty("level", key)
            lbl.setStyleSheet("color: " + theme.ui_color(key, False) + "; font-size: 10px;")
            self._legend_labels.append(lbl)
            head.addWidget(lbl)

        self.chk_autoscroll = QCheckBox("自动滚动")
        self.chk_autoscroll.setChecked(True)
        self.chk_autoscroll.setToolTip("日志更新时自动滚动到底部")
        btn_copy = QPushButton("复制")
        btn_clear = QPushButton("清空")
        for b in (btn_copy, btn_clear):
            b.setFixedHeight(24)
            b.setObjectName("FormField")
        head.addWidget(self.chk_autoscroll)
        head.addWidget(btn_copy)
        head.addWidget(btn_clear)
        root.addLayout(head)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFont(theme.mono_font(10))
        self.log_box.setFixedHeight(96)  # 原 72，增高约 1/3
        self.log_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        root.addWidget(self.log_box)

        btn_copy.clicked.connect(self.copy_log)
        btn_clear.clicked.connect(self.clear_log)

    # ---------- 配色 ----------

    def set_dark(self, dark: bool) -> None:
        self._log_hint.setStyleSheet(f"color: {theme.ui_color('muted', dark)};")
        for lbl in self._legend_labels:
            lbl.setStyleSheet(
                "color: " + theme.ui_color(lbl.property("level"), dark) + "; font-size: 10px;"
            )

    # ---------- 日志 ----------

    _LEVEL_MARK = {
        "info": "·",
        "step": "▸",
        "ok": "✓",
        "warn": "!",
        "error": "✕",
        "muted": "·",
    }

    @staticmethod
    def detect_log_level(msg: str) -> str:
        s = (msg or "").strip()
        low = s.lower()
        if (
            s.startswith("错误")
            or "失败" in s
            or "traceback" in low
            or "exception" in low
            or "无法" in s
        ):
            return "error"
        if (
            "警告" in s
            or "跳过" in s
            or "未找到" in s
            or "缺少" in s
            or "密码为空" in s
        ):
            return "warn"
        if (
            s.startswith("——")
            or s.startswith("连接")
            or "开始巡检" in s
            or "快速巡检" in s
            or "深度巡检" in s
            or "开始录像" in s
            or ("摄像头" in s and "路" in s)
            or "优先时段" in s
            or "深度抽检" in s
        ):
            return "step"
        if (
            "完成" in s
            or s.startswith("已")
            or ("正常" in s and "异常" not in s)
            or "设备:" in s
        ):
            return "ok"
        if s.startswith("  ") or s.startswith("通道"):
            return "muted"
        return "info"

    def log(self, msg: str, level: Optional[str] = None) -> None:
        text = str(msg).rstrip("\n")
        if not text:
            return
        level = level or self.detect_log_level(text)
        mark = self._LEVEL_MARK.get(level, "·")
        ts = datetime.now().strftime("%H:%M:%S")
        dark = theme.effective_dark()

        cursor = self.log_box.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        fmt_time = QTextCharFormat()
        fmt_time.setForeground(QColor(theme.ui_color("muted", dark)))
        level_color = level if level in ("step", "ok", "warn", "error") else "info"
        fmt_mark = QTextCharFormat()
        fmt_mark.setForeground(QColor(theme.ui_color(level_color, dark)))
        fmt_body = QTextCharFormat()
        fmt_body.setForeground(QColor(theme.ui_color(level_color, dark)))

        if text.startswith("——") and text.endswith("——"):
            cursor.insertText(f"\n{ts}  ", fmt_time)
            cursor.insertText("──────── ", fmt_time)
            body = text.strip("— ").strip()
            cursor.insertText(f"{body}\n", fmt_mark)
        else:
            cursor.insertText(f"{ts}  ", fmt_time)
            cursor.insertText(f"{mark} ", fmt_mark)
            cursor.insertText(f"{text}\n", fmt_body)

        self.log_box.setTextCursor(cursor)
        if self.chk_autoscroll.isChecked():
            self.log_box.ensureCursorVisible()
        self._log_line_count += 1
        self._trim_log()

        short = text if len(text) <= 42 else text[:40] + "…"
        self._log_hint.setText(f"最近 · {short}   （共 {self._log_line_count} 条）")

    def _trim_log(self) -> None:
        if self._log_line_count <= LOG_MAX_LINES:
            return
        block = self.log_box.document().firstBlock()
        cursor = QTextCursor(block)
        cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
        cursor.removeSelectedText()
        self.log_box.textCursor().deletePreviousChar()
        self._log_line_count -= 1

    def clear_log(self) -> None:
        self.log_box.clear()
        self._log_line_count = 0
        self.log("日志已清空。", level="muted")

    def copy_log(self) -> None:
        text = self.log_box.toPlainText()
        QApplication.clipboard().setText(text)
        self._log_hint.setText("已复制全部日志到剪贴板")

    @property
    def line_count(self) -> int:
        return self._log_line_count

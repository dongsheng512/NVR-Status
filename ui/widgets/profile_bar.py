"""顶栏配置档案条：档案下拉 + 新建/另存为/重命名/删除/导入/导出。"""

from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)


class ProfileBar(QWidget):
    profile_changed = Signal(str)
    new_requested = Signal()
    save_as_requested = Signal()
    rename_requested = Signal()
    delete_requested = Signal()
    import_requested = Signal()
    export_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(6)

        title = QLabel("配置档案")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        root.addWidget(title)

        self.combo = QComboBox()
        self.combo.setMinimumWidth(180)
        root.addWidget(self.combo, 1)

        self.btn_new = QPushButton("新建")
        self.btn_save_as = QPushButton("另存为")
        self.btn_rename = QPushButton("重命名")
        self.btn_delete = QPushButton("删除")
        self.btn_delete.setProperty("role", "danger")
        self.btn_import = QPushButton("导入")
        self.btn_export = QPushButton("导出")

        for btn in (
            self.btn_new,
            self.btn_save_as,
            self.btn_rename,
            self.btn_delete,
            self.btn_import,
            self.btn_export,
        ):
            root.addWidget(btn)

        self.combo.currentTextChanged.connect(self.profile_changed)
        self.btn_new.clicked.connect(self.new_requested)
        self.btn_save_as.clicked.connect(self.save_as_requested)
        self.btn_rename.clicked.connect(self.rename_requested)
        self.btn_delete.clicked.connect(self.delete_requested)
        self.btn_import.clicked.connect(self.import_requested)
        self.btn_export.clicked.connect(self.export_requested)

    def set_profiles(self, names: List[str], active: str) -> None:
        block = self.combo.blockSignals(True)
        try:
            self.combo.clear()
            self.combo.addItems(names or ["默认"])
            if active and active in names:
                self.combo.setCurrentText(active)
        finally:
            self.combo.blockSignals(block)

    def set_active(self, name: str) -> None:
        block = self.combo.blockSignals(True)
        try:
            self.combo.setCurrentText(name)
        finally:
            self.combo.blockSignals(block)

    def active(self) -> str:
        return self.combo.currentText()

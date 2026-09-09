"""顶栏配置档案条：档案下拉 + 新建 + 管理菜单。"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QWidget,
    QWidgetAction,
)

from ui import theme

_COMBO_MIN_W = 240
_COMBO_MAX_W = 360


class _MenuItemRow(QWidget):
    """菜单行：内层自绘悬停，删除项用危险色（QSS 无法按 QAction 着色）。"""

    def __init__(self, text: str, *, danger: bool = False, parent=None):
        super().__init__(parent)
        self._danger = danger
        self._hovered = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.setFixedHeight(30)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(2, 1, 2, 1)
        outer.setSpacing(0)
        self._inner = QWidget()
        self._inner.setObjectName("MenuItemInner")
        inner = QHBoxLayout(self._inner)
        inner.setContentsMargins(14, 4, 16, 4)
        inner.setSpacing(0)
        self._label = QLabel(text)
        self._label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        inner.addWidget(self._label)
        outer.addWidget(self._inner)
        self.sync_style()

    def sizeHint(self) -> QSize:
        return QSize(max(super().sizeHint().width(), 140), 30)

    def set_hovered(self, hovered: bool) -> None:
        if self._hovered == hovered:
            return
        self._hovered = hovered
        self.sync_style()

    def sync_style(self) -> None:
        dark = theme.effective_dark()
        self.setStyleSheet("background: transparent; border: none;")
        if not self.isEnabled():
            fg = theme.TEXT_SECONDARY["dark" if dark else "light"]
            self._inner.setStyleSheet(
                "background: transparent; border: none; border-radius: 4px;"
            )
            self._label.setStyleSheet(
                f"color: {fg}; background: transparent; border: none;"
            )
            return
        if self._hovered:
            self._inner.setStyleSheet(
                "background: #3b82f6; border: none; border-radius: 4px;"
            )
            self._label.setStyleSheet(
                "color: #ffffff; background: transparent; border: none;"
            )
            return
        if self._danger:
            fg = theme.STATUS_COLORS["error"]["dark" if dark else "light"]
        else:
            fg = theme.TEXT_PRIMARY["dark" if dark else "light"]
        self._inner.setStyleSheet(
            "background: transparent; border: none; border-radius: 4px;"
        )
        self._label.setStyleSheet(
            f"color: {fg}; background: transparent; border: none;"
        )

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.EnabledChange:
            self.sync_style()
        super().changeEvent(event)

    def event(self, event: QEvent) -> bool:
        t = event.type()
        if t in (QEvent.Type.HoverEnter, QEvent.Type.Enter):
            self.set_hovered(True)
        elif t in (QEvent.Type.HoverLeave, QEvent.Type.Leave):
            self.set_hovered(False)
        return super().event(event)


class ProfileBar(QWidget):
    profile_changed = Signal(str)
    new_requested = Signal()
    save_as_requested = Signal()
    rename_requested = Signal()
    delete_requested = Signal()
    import_requested = Signal()
    export_requested = Signal()
    open_save_dir_requested = Signal()
    open_config_dir_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ProfileBar")
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        title = QLabel("配置档案")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        self._title_label = title
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        root.addWidget(title)

        self.combo = QComboBox()
        self.combo.setObjectName("ProfileCombo")
        self.combo.setMinimumWidth(_COMBO_MIN_W)
        self.combo.setMaximumWidth(_COMBO_MAX_W)
        self.combo.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self.combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.combo.setMinimumContentsLength(16)
        self.combo.view().setTextElideMode(Qt.TextElideMode.ElideRight)
        self.combo.setToolTip("切换当前配置档案")
        root.addWidget(self.combo, 0)

        self.btn_new = QPushButton("新建")
        self.btn_new.setToolTip("新建空白配置档案")
        root.addWidget(self.btn_new)

        self.btn_manage = QPushButton("管理")
        self.btn_manage.setObjectName("ProfileManageBtn")
        self.btn_manage.setToolTip("另存、重命名、导入、导出、打开目录、删除")
        self.menu = QMenu(self.btn_manage)
        self.menu.setObjectName("ProfileManageMenu")
        self.menu.setToolTipsVisible(True)
        self.menu.setMinimumWidth(168)
        self.btn_manage.setMenu(self.menu)
        root.addWidget(self.btn_manage)

        root.addStretch(1)

        self.act_save_as = self._add_menu_action(
            "另存为…",
            self.save_as_requested,
            tooltip="将当前档案复制为新档案",
        )
        self.act_rename = self._add_menu_action(
            "重命名…",
            self.rename_requested,
            tooltip="重命名当前档案",
        )
        self.menu.addSeparator()
        self.act_import = self._add_menu_action(
            "导入…",
            self.import_requested,
            tooltip="从 JSON 文件导入档案",
        )
        self.act_export = self._add_menu_action(
            "导出…",
            self.export_requested,
            tooltip="导出当前档案为 JSON",
        )
        self.menu.addSeparator()
        self.act_open_save = self._add_menu_action(
            "打开保存目录",
            self.open_save_dir_requested,
            tooltip="打开抽检片段保存目录",
        )
        self.act_open_config = self._add_menu_action(
            "打开配置目录",
            self.open_config_dir_requested,
            tooltip="打开本机配置档案目录",
        )
        self.menu.addSeparator()
        self.act_delete = self._add_menu_action(
            "删除档案…",
            self.delete_requested,
            tooltip="删除当前档案（至少保留一个）",
            danger=True,
        )

        self.combo.currentTextChanged.connect(self.profile_changed)
        self.btn_new.clicked.connect(self.new_requested)
        self.menu.aboutToShow.connect(self._prepare_menu)
        self.menu.hovered.connect(self._on_menu_hovered)
        self._sync_delete_enabled()

    def _add_menu_action(
        self,
        text: str,
        signal: Signal,
        *,
        tooltip: str = "",
        danger: bool = False,
    ) -> QAction:
        act = QWidgetAction(self.menu)
        row = _MenuItemRow(text, danger=danger)
        act.setDefaultWidget(row)
        act.setText(text)
        if tooltip:
            act.setToolTip(tooltip)
            row.setToolTip(tooltip)
        act.triggered.connect(signal.emit)
        self.menu.addAction(act)
        return act

    def _menu_row(self, action: QAction) -> Optional[_MenuItemRow]:
        if not isinstance(action, QWidgetAction):
            return None
        row = action.defaultWidget()
        return row if isinstance(row, _MenuItemRow) else None

    def _sync_delete_enabled(self) -> None:
        ok = self.combo.count() > 1
        self.act_delete.setEnabled(ok)
        row = self._menu_row(self.act_delete)
        if row is not None:
            row.setEnabled(ok)
            row.sync_style()

    def _prepare_menu(self) -> None:
        self._sync_delete_enabled()
        for act in self.menu.actions():
            row = self._menu_row(act)
            if row is not None:
                row.set_hovered(False)
                row.sync_style()

    def _on_menu_hovered(self, action: QAction) -> None:
        for act in self.menu.actions():
            row = self._menu_row(act)
            if row is not None:
                row.set_hovered(act is action)

    def set_profiles(self, names: List[str], active: str) -> None:
        block = self.combo.blockSignals(True)
        try:
            self.combo.clear()
            self.combo.addItems(names or ["默认"])
            if active and active in names:
                self.combo.setCurrentText(active)
        finally:
            self.combo.blockSignals(block)
        self._sync_delete_enabled()

    def set_active(self, name: str) -> None:
        block = self.combo.blockSignals(True)
        try:
            self.combo.setCurrentText(name)
        finally:
            self.combo.blockSignals(block)

    def active(self) -> str:
        return self.combo.currentText()

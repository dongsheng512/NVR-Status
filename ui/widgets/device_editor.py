"""设备添加/编辑对话框：校验 IP 与端口。"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from config_store import _empty_device

_ON_SAVE = Callable[[Dict[str, Any]], None]


def _validate_ip(ip: str) -> bool:
    parts = ip.strip().split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit():
            return False
        if int(p) > 255:
            return False
    return True


class DeviceEditorDialog(QDialog):
    def __init__(
        self,
        device: Optional[Dict[str, Any]] = None,
        on_save: Optional[_ON_SAVE] = None,
        title: str = "添加设备",
        parent=None,
    ):
        super().__init__(parent)
        dev = dict(device or _empty_device())
        try:
            dev["port"] = int(dev.get("port") or 80)
        except (TypeError, ValueError):
            dev["port"] = 80

        self._on_save = on_save
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        self.setModal(True)

        root = QVBoxLayout(self)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.ed_name = QLineEdit(str(dev.get("name") or "NVR"))
        self.ed_ip = QLineEdit(str(dev.get("ip") or ""))
        self.ed_ip.setPlaceholderText("192.168.1.100")
        self.ed_port = QSpinBox()
        self.ed_port.setRange(1, 65535)
        self.ed_port.setValue(dev.get("port", 80))
        self.ed_user = QLineEdit(str(dev.get("username") or "admin"))
        self.ed_pass = QLineEdit(str(dev.get("password") or ""))
        self.ed_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.chk_ssl = QCheckBox("使用 HTTPS (SSL)")

        form.addRow("名称", self.ed_name)
        form.addRow("IP 地址", self.ed_ip)
        form.addRow("端口", self.ed_port)
        form.addRow("用户名", self.ed_user)
        form.addRow("密码", self.ed_pass)
        form.addRow("", self.chk_ssl)
        root.addLayout(form)

        self.hint = QLabel("")
        self.hint.setStyleSheet("color: #b91c1c;")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _on_accept(self) -> None:
        name = self.ed_name.text().strip() or "NVR"
        ip = self.ed_ip.text().strip()
        if not ip:
            self._show_err("IP 地址不能为空")
            return
        if not _validate_ip(ip):
            self._show_err("IP 地址格式不正确（如 192.168.1.100）")
            return
        result = {
            "name": name,
            "ip": ip,
            "port": self.ed_port.value(),
            "username": self.ed_user.text().strip() or "admin",
            "password": self.ed_pass.text(),
            "ssl": bool(self.chk_ssl.isChecked()),
        }
        if self._on_save:
            self._on_save(result)
        self.accept()

    def _show_err(self, msg: str) -> None:
        self.hint.setText(msg)
        QMessageBox.warning(self, "输入错误", msg)

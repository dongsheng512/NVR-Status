"""应用入口：QApplication、全局字体、异常钩子与日志文件。"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from config_store import app_data_dir

try:
    from PySide6.QtCore import QSysInfo
except Exception:  # pragma: no cover
    QSysInfo = None


def _set_qt_attrs() -> None:
    # 高 DPI 默认启用；设置组织/应用名便于 QSettings
    QCoreApplication.setOrganizationName("NVRStatus")
    QCoreApplication.setApplicationName("NVRStatus")
    QCoreApplication.setApplicationVersion("2.0.0")
    try:
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    except Exception:
        pass


def _logs_dir() -> str:
    path = os.path.join(app_data_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path


def _install_exception_hook() -> None:
    def hook(exc_type, exc_value, exc_tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(_logs_dir(), f"crash_{stamp}.log")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass
        print(text, file=sys.stderr)
        try:
            app = QApplication.instance()
            if app is not None:
                QMessageBox.critical(
                    None,
                    "程序异常",
                    f"发生未捕获异常，已写入日志：\n{path}\n\n{traceback.format_exception_only(exc_type, exc_value)[-1]}",
                )
        except Exception:
            pass

    sys.excepthook = hook


def _install_qt_message_hook() -> None:
    """将 Qt 警告/错误写入 logs/qt.log，便于无 console 包诊断。"""
    from PySide6.QtCore import qInstallMessageHandler  # type: ignore

    def handler(mode, context, message) -> None:
        try:
            path = os.path.join(_logs_dir(), "qt.log")
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] {mode} {message}\n")
        except Exception:
            pass

    qInstallMessageHandler(handler)


def main() -> None:
    _set_qt_attrs()
    _install_exception_hook()
    _install_qt_message_hook()

    app = QApplication(sys.argv)
    app.setApplicationName("NVRStatus")
    app.setApplicationVersion("2.0.0")

    from ui import theme
    from ui.main_window import MainWindow

    theme.init_app_font(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

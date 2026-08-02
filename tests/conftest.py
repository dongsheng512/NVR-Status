"""pytest 共享夹具：无头 QApplication（offscreen）。

非 Qt 用例（导出 / 覆盖解析 / lookback 换算）无需依赖本夹具。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app

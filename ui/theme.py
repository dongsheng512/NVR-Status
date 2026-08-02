"""主题：浅色 / 深色 / 跟随系统，集中管理状态色与字体。"""

from __future__ import annotations

import os
import sys
import tempfile
from enum import Enum
from typing import Dict, List, Tuple

from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication


class ThemeMode(Enum):
    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"


_CURRENT_MODE = ThemeMode.SYSTEM


def display_name(mode: ThemeMode) -> str:
    return {
        ThemeMode.LIGHT: "浅色",
        ThemeMode.DARK: "深色",
        ThemeMode.SYSTEM: "跟随系统",
    }[mode]


# 状态色（light / dark 两套），统一在 theme 层，避免散落硬编码色值
STATUS_COLORS: Dict[str, Dict[str, str]] = {
    "ok": {"light": "#1a7f37", "dark": "#6dcf7a"},
    "warn": {"light": "#b26a00", "dark": "#fcb24a"},
    "error": {"light": "#c62828", "dark": "#ff6b6b"},
    "info": {"light": "#24292f", "dark": "#c9d1d9"},
    "step": {"light": "#0969da", "dark": "#58a6ff"},
    "muted": {"light": "#6e7781", "dark": "#8b949e"},
    "sep": {"light": "#afb8c1", "dark": "#484f58"},
}

# 状态 dot / bar 颜色
STATE_DOT: Dict[str, Tuple[str, str]] = {
    "ready": ("#9ca3af", "#6b7280"),
    "running": ("#2563eb", "#3b82f6"),
    "ok": ("#16a34a", "#22c55e"),
    "warn": ("#d97706", "#f59e0b"),
    "error": ("#dc2626", "#ef4444"),
}

# 表格斑马纹背景
ZEBRA_ODD = {"light": "#f6f8fa", "dark": "#2e2e2e"}
ZEBRA_EVEN = {"light": "#ffffff", "dark": "#242424"}

# 日志区/表格区背景
PANEL_BG = {"light": "#ffffff", "dark": "#242424"}
PANEL_ALT = {"light": "#f6f8fa", "dark": "#1a1a1a"}
BORDER = {"light": "#d0d7de", "dark": "#3a3a3a"}

# 文本色
TEXT_PRIMARY = {"light": "#1f2328", "dark": "#e6e6e6"}
TEXT_SECONDARY = {"light": "#6e7781", "dark": "#9aa4b2"}


def ui_color(key: str, dark: bool) -> str:
    return (STATUS_COLORS[key]["dark"] if dark else STATUS_COLORS[key]["light"])


_CHEVRON_CACHE: Dict[str, str] = {}


def _chevron_path(dark: bool, disabled: bool = False, direction: str = "down") -> str:
    """生成并缓存箭头 SVG（down/up），返回可被 QSS url() 引用的文件路径。"""
    key = f"{direction}_{'dark' if dark else 'light'}_{'dis' if disabled else 'on'}"
    if key in _CHEVRON_CACHE:
        return _CHEVRON_CACHE[key]
    color = "#5b6168" if disabled else ("#adbac7" if dark else "#57606a")
    # down: V；up: ∧
    d = "M2 6l4-4 4 4" if direction == "up" else "M2 2l4 4 4-4"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="8" viewBox="0 0 12 8">'
        f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )
    path = os.path.join(tempfile.gettempdir(), f"nvr_chevron_{key}.svg")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
        path = path.replace("\\", "/")
    except OSError:
        path = ""
    _CHEVRON_CACHE[key] = path
    return path


def mono_font(size: int = 11) -> QFont:
    """跨平台等宽字体（Windows / macOS / Linux 回退）。"""
    families = []
    if sys.platform == "darwin":
        families = ["Menlo"]
    elif sys.platform == "win32":
        families = ["Consolas", "Microsoft YaHei UI"]
    else:
        families = ["DejaVu Sans Mono", "Noto Sans Mono"]
    return QFont(",".join(families), size)


def ui_font(size: int = 10) -> QFont:
    families = []
    if sys.platform == "win32":
        families = ["Microsoft YaHei UI"]
    elif sys.platform == "darwin":
        families = ["PingFang SC"]
    else:
        families = ["Noto Sans CJK SC", "WenQuanYi Micro Hei"]
    return QFont(",".join(families), size)


def effective_dark() -> bool:
    """当前生效是否为深色（含跟随系统）。"""
    global _CURRENT_MODE
    if _CURRENT_MODE == ThemeMode.DARK:
        return True
    if _CURRENT_MODE == ThemeMode.LIGHT:
        return False
    scheme = QGuiApplication.styleHints().colorScheme()
    return scheme.value == 1  # Qt.ColorScheme.Dark


def apply_theme(app: QApplication, mode: ThemeMode) -> None:
    """按模式应用调色板 + QSS。"""
    global _CURRENT_MODE
    _CURRENT_MODE = mode
    dark = effective_dark()
    pal = QPalette()

    bg = "#1e1f22" if dark else "#f5f6f8"
    panel = PANEL_BG["dark" if dark else "light"]
    border = BORDER["dark" if dark else "light"]
    text = TEXT_PRIMARY["dark" if dark else "light"]
    text_sec = TEXT_SECONDARY["dark" if dark else "light"]

    from PySide6.QtGui import QColor as _C

    pal.setColor(QPalette.ColorRole.Window, _C(bg))
    pal.setColor(QPalette.ColorRole.Base, _C(panel))
    pal.setColor(QPalette.ColorRole.AlternateBase, _C(PANEL_ALT["dark" if dark else "light"]))
    pal.setColor(QPalette.ColorRole.WindowText, _C(text))
    pal.setColor(QPalette.ColorRole.Text, _C(text))
    pal.setColor(QPalette.ColorRole.PlaceholderText, _C(text_sec))
    pal.setColor(QPalette.ColorRole.Button, _C(bg))
    pal.setColor(QPalette.ColorRole.ButtonText, _C(text))
    pal.setColor(QPalette.ColorRole.Highlight, _C("#3b82f6"))
    pal.setColor(QPalette.ColorRole.HighlightedText, _C("#ffffff"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, _C(panel))
    pal.setColor(QPalette.ColorRole.ToolTipText, _C(text))
    pal.setColor(QPalette.ColorRole.Link, _C("#3b82f6"))
    pal.setColor(QPalette.ColorRole.Midlight, _C(bg))
    pal.setColor(QPalette.ColorRole.Dark, _C(border))

    app.setPalette(pal)

    btn_bg = "#3a3d42" if dark else "#eef1f4"
    btn_hover = "#464a50" if dark else "#e2e6ea"
    btn_pressed = "#2c2f33" if dark else "#d5dade"
    border_c = border
    group_hint = "#1e3a5f" if dark else "#eff6ff"
    select_bg = "#1e3a5f" if dark else "#dbe9ff"
    arrow = _chevron_path(dark)
    arrow_up = _chevron_path(dark, direction="up")
    arrow_disabled = _chevron_path(dark, disabled=True)

    toggle_bg = "#1e3a5f" if dark else "#e8f1fd"
    toggle_hover = "#2a4a78" if dark else "#dbe9ff"
    toggle_pressed = "#18324e" if dark else "#c9deff"
    toggle_fg = "#79b8ff" if dark else "#1d4ed8"
    toggle_border = "#2d6eb8" if dark else "#9dc4ff"

    qss = f"""
    QMainWindow, QDialog {{ background: {bg}; }}
    QWidget {{ color: {text}; font-size: 13px; }}

    QFrame#Card, QGroupBox {{
        background: {panel};
        border: 1px solid {border_c};
        border-radius: 8px;
    }}

    QGroupBox {{
        margin-top: 10px;
        padding: 10px 8px 8px 8px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
        background: {panel};
    }}

    /* 扫描设置：更轻的表单卡片，接近系统偏好设置密度 */
    QGroupBox#ScanSettings {{
        font-weight: 600;
        font-size: 12px;
        margin-top: 8px;
        padding: 6px 4px 4px 4px;
        border-radius: 6px;
    }}
    QGroupBox#ScanSettings::title {{
        left: 8px;
        padding: 0 3px;
        color: {text_sec};
        font-weight: 600;
    }}

    /* 表单输入：紧凑、扁平，避免 SpinBox 被全局样式撑大 */
    QSpinBox, QDoubleSpinBox, QLineEdit {{
        background: {panel};
        color: {text};
        border: 1px solid {border_c};
        border-radius: 4px;
        padding: 1px 4px;
        min-height: 22px;
        max-height: 26px;
        selection-background-color: #3b82f6;
        selection-color: #ffffff;
    }}
    QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{
        border: 1px solid #3b82f6;
    }}
    QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled {{
        color: {text_sec};
        background: {btn_bg};
    }}
    QSpinBox::up-button, QDoubleSpinBox::up-button,
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        subcontrol-origin: border;
        width: 16px;
        border: none;
        background: transparent;
    }}
    QSpinBox::up-button, QDoubleSpinBox::up-button {{
        subcontrol-position: top right;
    }}
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        subcontrol-position: bottom right;
    }}
    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
        background: {btn_hover};
    }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
        width: 7px; height: 5px;
        image: url({arrow_up});
    }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
        width: 7px; height: 5px;
        image: url({arrow});
    }}

    QPushButton {{
        background: {btn_bg};
        border: 1px solid {border_c};
        border-radius: 6px;
        padding: 6px 12px;
    }}
    QPushButton:hover {{ background: {btn_hover}; }}
    QPushButton:pressed {{ background: {btn_pressed}; }}
    QPushButton:disabled {{ color: {text_sec}; background: {btn_bg}; }}
    QPushButton:focus {{
        border: 1px solid #3b82f6;
    }}
    QPushButton#FormField {{
        border-radius: 4px;
        padding: 2px 8px;
        font-weight: 400;
        min-height: 22px;
        max-height: 24px;
    }}
    QPushButton#FormField[role="primary"] {{
        background: #3b82f6; color: #ffffff;
        border: 1px solid #3b82f6;
        padding: 2px 8px;
        font-weight: 500;
        min-height: 22px;
        max-height: 24px;
    }}
    QPushButton#FormField[role="primary"]:hover {{ background: #2f6fe0; }}
    QPushButton#FormField[role="primary"]:disabled {{
        background: #9db8df; color: #f0f4f8; border: 1px solid #9db8df;
    }}

    /* 分段选择：单位（分/时/天）、主题（浅/深/系统） */
    QFrame#LookbackUnitSeg, QFrame#ThemeSeg, QFrame[role="segment"] {{
        background: {btn_bg};
        border: 1px solid {border_c};
        border-radius: 6px;
    }}
    QPushButton#SegmentBtn {{
        background: transparent;
        border: none;
        border-radius: 4px;
        padding: 2px 6px;
        font-size: 12px;
        font-weight: 400;
        color: {text_sec};
        min-height: 22px;
        max-height: 24px;
    }}
    QPushButton#SegmentBtn:hover:!checked {{
        background: {btn_hover};
        color: {text};
    }}
    QPushButton#SegmentBtn:checked {{
        background: {panel};
        color: {text};
        font-weight: 600;
        border: 1px solid {border_c};
    }}
    QPushButton#SegmentBtn:pressed {{
        background: {btn_pressed};
    }}

    QToolButton {{
        background: transparent;
        border: none;
        border-radius: 6px;
        padding: 6px 10px;
        text-align: left;
        font-weight: 600;
        color: {text};
    }}
    QToolButton:hover {{ background: {btn_hover}; }}
    QToolButton:pressed {{ background: {btn_pressed}; }}

    QPushButton[role="primary"] {{
        background: #3b82f6; color: #ffffff;
        border: 1px solid #3b82f6;
    }}
    QPushButton[role="primary"]:hover {{ background: #2f6fe0; }}
    QPushButton[role="primary"]:disabled {{
        background: #9db8df; color: #f0f4f8; border: 1px solid #9db8df;
    }}
    QPushButton[role="danger"] {{
        background: #b91c1c; color: #ffffff; border: 1px solid #b91c1c;
    }}
    QPushButton[role="danger"]:hover {{ background: #991b1b; }}

    QPushButton[role="toggle"] {{
        background: {toggle_bg}; color: {toggle_fg};
        border: 1px solid {toggle_border}; border-radius: 6px;
        padding: 6px 12px; font-weight: 600; text-align: left;
    }}
    QPushButton[role="toggle"]:hover {{ background: {toggle_hover}; }}
    QPushButton[role="toggle"]:pressed {{ background: {toggle_pressed}; }}

    QPlainTextEdit, QTextEdit {{
        background: {panel};
        border: 1px solid {border_c};
        border-radius: 6px;
        padding: 4px 6px;
        selection-background-color: #3b82f6;
        selection-color: #ffffff;
    }}
    QTextEdit#WarnBox {{
        padding: 2px 6px;
        border-radius: 6px;
        font-size: 12px;
    }}

    QComboBox {{
        background: {panel};
        border: 1px solid {border_c};
        border-radius: 6px;
        padding: 4px 26px 4px 8px;
        min-height: 22px;
        selection-background-color: {select_bg};
        selection-color: {text};
    }}
    QComboBox:focus {{ border: 1px solid #3b82f6; }}
    QComboBox:disabled {{ color: {text_sec}; }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 22px;
        border: none;
        background: transparent;
    }}
    QComboBox::down-arrow {{
        image: url({arrow});
        width: 12px;
        height: 8px;
    }}
    QComboBox::down-arrow:disabled {{ image: url({arrow_disabled}); }}

    /* 下拉列表：紧凑，接近系统菜单行高 */
    QComboBox QAbstractItemView {{
        background: {panel};
        border: 1px solid {border_c};
        outline: 0;
        padding: 2px;
        selection-background-color: {select_bg};
        selection-color: {text};
    }}
    QComboBox QAbstractItemView::item {{
        min-height: 23px;
        padding: 3px 8px;
    }}
    QComboBox QAbstractItemView::item:hover {{
        background: {group_hint};
    }}
    QComboBox QAbstractItemView::item:selected {{
        background: {select_bg};
        color: {text};
    }}

    /* 扫描目标：选项间距相对默认 +1px */
    QComboBox#ScanTargetCombo {{
        padding: 4px 26px 4px 8px;
        min-height: 24px;
    }}
    QComboBox#ScanTargetCombo QAbstractItemView {{
        padding: 2px;
    }}
    QComboBox#ScanTargetCombo QAbstractItemView::item {{
        min-height: 24px;
        padding: 4px 8px;
    }}

    QHeaderView::section {{
        background: {btn_bg};
        border: none;
        border-right: 1px solid {border_c};
        border-bottom: 1px solid {border_c};
        padding: 6px 8px;
        font-weight: 600;
    }}
    QTableView {{ gridline-color: {border_c}; background: {panel}; }}
    QTableView::item {{ padding: 2px 6px; }}
    QTableView::item:hover {{ background: {group_hint}; }}
    QTableView::item:selected {{ background: {select_bg}; }}

    QProgressBar {{
        border: 1px solid {border_c};
        border-radius: 6px;
        background: {btn_bg};
        text-align: center;
        height: 14px;
    }}
    QProgressBar::chunk {{ background: #3b82f6; border-radius: 5px; }}

    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {text_sec}; border-radius: 5px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {text}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: {text_sec}; border-radius: 5px; min-width: 30px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    QToolTip {{
        background: {panel}; color: {text};
        border: 1px solid {border_c}; padding: 4px;
    }}

    QSplitter::handle {{ background: transparent; }}
    QSplitter::handle:hover {{ background: #3b82f6; }}

    QMenu {{ background: {panel}; border: 1px solid {border_c}; }}
    QMenu::item {{ padding: 6px 24px; }}
    QMenu::item:selected {{ background: #3b82f6; color: #ffffff; }}
    """
    app.setStyleSheet(qss)


def status_color(color_key: str, dark: bool) -> QColor:
    return QColor(ui_color(color_key, dark))


def init_app_font(app: QApplication) -> None:
    app.setFont(ui_font())

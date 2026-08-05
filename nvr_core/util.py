"""nvr_core 公共工具：纯函数 / 常量 / 异常（无 ISAPI 依赖）。

B2 拆分：原 hikvision_status.py 顶部的 Colors、数值转换、时间解析、
路径定位、工具探测、文件名清洗、ScanCancelled 异常。
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from datetime import datetime, timezone
from typing import Dict, Optional


class Colors:
    """终端颜色工具类"""

    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'

    # 前景色
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'

    # 亮色前景色
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'
    BRIGHT_WHITE = '\033[97m'

    # 背景色
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'

    @staticmethod
    def colorize(text: str, color: str) -> str:
        """为文本添加颜色"""
        return f"{color}{text}{Colors.RESET}"

    @staticmethod
    def success(text: str) -> str:
        """成功状态"""
        return Colors.colorize(text, Colors.BRIGHT_GREEN)

    @staticmethod
    def warning(text: str) -> str:
        """警告状态"""
        return Colors.colorize(text, Colors.BRIGHT_YELLOW)

    @staticmethod
    def error(text: str) -> str:
        """错误状态"""
        return Colors.colorize(text, Colors.BRIGHT_RED)

    @staticmethod
    def info(text: str) -> str:
        """信息文本"""
        return Colors.colorize(text, Colors.BRIGHT_CYAN)

    @staticmethod
    def label(text: str) -> str:
        """标签文本"""
        return Colors.colorize(text, Colors.BRIGHT_BLUE)

    @staticmethod
    def section(text: str) -> str:
        """章节标题"""
        return Colors.colorize(text, Colors.BOLD + Colors.CYAN)


class ScanCancelled(Exception):
    """巡检取消信号（GUI 取消钩子，业务循环检查处抛出）。"""


def _to_int(value: Optional[str], default: int = 0) -> int:
    """安全转换为整数,空串/非数字回退默认值"""
    try:
        return int(value) if value else default
    except (TypeError, ValueError):
        return default


def _to_float(value: Optional[str], default: float = 0.0) -> float:
    """安全转换为浮点数,空串/非数字回退默认值"""
    try:
        return float(value) if value else default
    except (TypeError, ValueError):
        return default


def _parse_hik_time(value: Optional[str]) -> Optional[datetime]:
    """解析海康时间字符串为 UTC aware datetime。"""
    if not value:
        return None
    text = value.strip()
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        if re.search(r"[+-]\d{2}:\d{2}$", text):
            return datetime.fromisoformat(text).astimezone(timezone.utc)
        # 无时区时按设备本地难以确定,按 UTC 解释
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _project_dir() -> str:
    """脚本/可执行包所在项目根目录。"""
    # PyInstaller 打包后: 资源在 _MEIPASS, 可写目录用可执行文件旁
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # 本文件位于 <项目>/nvr_core/util.py → 项目根为其上级
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resource_dir() -> str:
    """只读资源目录(打包后为 _MEIPASS)。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return _project_dir()


def _tool_bin_dirs() -> list[str]:
    """可能存放 ffmpeg/ffprobe 的目录（开发树 / onedir / macOS .app）。"""
    dirs: list[str] = []

    def _add(path: str) -> None:
        if not path:
            return
        p = os.path.normpath(path)
        if p not in dirs:
            dirs.append(p)

    # 开发布局 & 通用
    _add(os.path.join(_resource_dir(), "bin"))
    _add(os.path.join(_project_dir(), "bin"))
    _add(os.path.join(_project_dir(), "ffmpeg", "bin"))
    _add(os.path.join(_project_dir(), "_internal", "bin"))

    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        # Windows / Linux onedir: <app>/_internal/bin 或 <app>/bin
        _add(os.path.join(exe_dir, "bin"))
        _add(os.path.join(exe_dir, "_internal", "bin"))

        # macOS .app: Contents/MacOS/<exe>
        #   二进制在 Contents/Frameworks/bin（真实文件）
        #   Contents/Resources/bin 常为指向 Frameworks 的符号链接
        #   _MEIPASS 在不同 PyInstaller 版本可能是 Frameworks 或 Resources
        contents = os.path.dirname(exe_dir)  # .../Contents
        _add(os.path.join(contents, "Frameworks", "bin"))
        _add(os.path.join(contents, "Resources", "bin"))
        _add(os.path.join(contents, "MacOS", "bin"))
        _add(os.path.join(contents, "_internal", "bin"))

        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            _add(os.path.join(meipass, "bin"))
            parent = os.path.dirname(meipass)
            _add(os.path.join(parent, "bin"))
            _add(os.path.join(parent, "Frameworks", "bin"))
            _add(os.path.join(parent, "Resources", "bin"))

    return dirs


def _is_runnable_binary(path: str) -> bool:
    """判断路径是否可作为外部工具调用。

    不用强依赖 os.X_OK：部分 macOS / 解压场景下 +x 检测会失败，
    但文件仍可执行；必要时尝试补可执行位。
    """
    if not path or not os.path.isfile(path):
        return False
    if os.name == "nt":
        return True
    if os.access(path, os.X_OK):
        return True
    try:
        mode = os.stat(path).st_mode
        os.chmod(path, mode | 0o111)
    except OSError:
        pass
    return os.access(path, os.X_OK) or os.access(path, os.R_OK)


def _which_tools() -> Dict[str, Optional[str]]:
    """定位 ffmpeg/ffprobe: 优先捆绑 bin/, 再 PATH。"""
    names = ("ffmpeg", "ffprobe")
    found: Dict[str, Optional[str]] = {n: None for n in names}
    for d in _tool_bin_dirs():
        for n in names:
            if found[n]:
                continue
            for exe in (n, f"{n}.exe"):
                p = os.path.join(d, exe)
                # 解析符号链接，避免 .app 内 Resources→Frameworks 断链误判
                try:
                    p = os.path.realpath(p)
                except OSError:
                    pass
                if _is_runnable_binary(p):
                    found[n] = p
                    break
    for n in names:
        if not found[n]:
            found[n] = shutil.which(n)
    return found


def _safe_filename(text: str, max_len: int = 60) -> str:
    """生成适合文件系统的安全文件名片段。"""
    s = (text or "").strip()
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", s)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_+", "_", s).strip("._")
    if not s:
        s = "unknown"
    return s[:max_len]

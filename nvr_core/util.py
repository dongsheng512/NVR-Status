"""nvr_core 公共工具：纯函数 / 常量 / 异常（无 ISAPI 依赖）。

B2 拆分：原 hikvision_status.py 顶部的 Colors、数值转换、时间解析、
路径定位、工具探测、文件名清洗、ScanCancelled 异常。
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# 深度抽检窗不得晚于「现在 - 10 分钟」，避开仍在写入/刚封口的回放（易 RTSP 超时）。
AV_SAMPLE_MIN_AGE = timedelta(minutes=10)


def latest_av_sample_instant(now: Optional[datetime] = None) -> datetime:
    """抽检结束时刻上限（UTC）。"""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    return now - AV_SAMPLE_MIN_AGE


def alternate_clip_times(
    win_s: datetime,
    win_e: datetime,
    seconds: int,
    avoid_s: datetime,
    cutoff: datetime,
    min_gap: timedelta = timedelta(minutes=30),
) -> Optional[Tuple[datetime, datetime]]:
    """在同一繁忙窗内另选一个距初检至少 30 分钟的抽检点。"""
    hard_e = min(win_e, cutoff)
    sec = max(3, int(seconds))
    gap = min_gap.total_seconds()
    if (hard_e - win_s).total_seconds() < sec + gap:
        return None

    def _ok(clip_s: datetime, clip_e: datetime) -> bool:
        if clip_s < win_s or clip_e > hard_e or clip_e <= clip_s:
            return False
        return abs((clip_s - avoid_s).total_seconds()) >= gap

    for delta in (
        timedelta(hours=-2),
        timedelta(hours=2),
        timedelta(hours=-1),
        timedelta(hours=1),
        timedelta(hours=-3),
        timedelta(minutes=-90),
    ):
        clip_s = avoid_s + delta
        clip_e = clip_s + timedelta(seconds=sec)
        if _ok(clip_s, clip_e):
            return clip_s, clip_e

    span = hard_e - win_s
    mid = win_s + span / 2
    if avoid_s >= mid:
        clip_s = win_s
        clip_e = clip_s + timedelta(seconds=sec)
    else:
        clip_e = hard_e
        clip_s = clip_e - timedelta(seconds=sec)
    if _ok(clip_s, clip_e):
        return clip_s, clip_e
    return None


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


def parse_channel_list(value: Any) -> Optional[frozenset]:
    """解析「只抽检这些通道」的过滤条件 → frozenset[int]；None/空 → None(=不过滤)。

    接受 CLI 字符串（"31,64" / "31 64" / "31、64"）、配置档案里的数组
    （[31, 64]）以及单个 int。

    非空但解析不出合法通道号时抛 ValueError（并指出是哪个 token）：
    静默退化成「不过滤」会让用户以为只抽了指定通道、实际抽了全部；
    静默丢掉落单的 token 则会让人以为检查过某通道、其实没查。两种误导
    都比直接报错更糟。
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool 是 int 子类，先挡掉以免 True→通道 1
        raise ValueError(f"无法解析的通道列表: {value!r}")
    if isinstance(value, int):
        items: list = [value]
    elif isinstance(value, str):
        items = [p for p in re.split(r"[,，、;；\s]+", value) if p]
        if not items:  # 空串/纯空白 = 未指定，视同不过滤
            return None
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        raise ValueError(f"无法解析的通道列表: {value!r}")

    out = set()
    for it in items:
        n = _to_int(str(it).strip(), default=0)
        if n <= 0:
            raise ValueError(f"通道号无效: {it!r}")
        out.add(n)
    return frozenset(out)


def parse_instant_list(
    value: Any, *, days_ago: int = 0, now: Optional[datetime] = None
) -> Optional[List[datetime]]:
    """解析「定点抽检时刻」→ 本地时刻列表(UTC)。

    接受 "19:10" / "19:10,18:40" / "19:10、18:40" 等；None/空 → None(=不定点，
    沿用繁忙时段逻辑)。时刻落在 `days_ago` 天前的当天(与 --busy-days-ago 同一口径)。

    非法 token 抛 ValueError 并指出是哪个(与 parse_channel_list 同一套理念)；
    **未来时刻直接报错**——那一段录像还不存在，静默换成「现在−10分钟」会让人
    以为自己查的是 HH:MM，实际查的是另一个时刻。
    """
    if value is None:
        return None
    if isinstance(value, str):
        tokens = [p for p in re.split(r"[,，、;；\s]+", value) if p]
        if not tokens:
            return None
    elif isinstance(value, datetime):
        tokens, direct = None, [value]
    elif isinstance(value, (list, tuple)):
        if value and all(isinstance(v, datetime) for v in value):
            tokens, direct = None, list(value)
        else:
            direct = []
            tokens = [str(v) for v in value if str(v).strip()]
            if not tokens:
                return None
    else:
        raise ValueError(f"无法解析的定点时刻列表: {value!r}")

    if now is None:
        now = datetime.now().astimezone()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone()

    if tokens is None:  # 已是 datetime：只做校验/去重/转 UTC
        out = []
        seen = set()
        for dt in direct:
            local = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            local = local.astimezone()
            if local > now:
                raise ValueError(f"定点时刻 {local:%m-%d %H:%M} 在未来, 该时刻的录像还不存在")
            key = local.timestamp()
            if key not in seen:
                seen.add(key)
                out.append(local.astimezone(timezone.utc))
        return out

    base = (now - timedelta(days=max(0, int(days_ago)))).date()

    out: List[datetime] = []
    seen = set()
    for tok in tokens:
        m = re.fullmatch(r"(\d{1,2})[:：](\d{2})", str(tok).strip())
        if not m:
            raise ValueError(f"定点时刻无效(应为 HH:MM): {tok!r}")
        hh, mm = int(m.group(1)), int(m.group(2))
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError(f"定点时刻超出范围: {tok!r}")
        local = datetime(
            base.year, base.month, base.day, hh, mm, tzinfo=now.tzinfo
        )
        if local > now:
            raise ValueError(
                f"定点时刻 {tok} 在未来({base:%Y-%m-%d}), 该时刻的录像还不存在"
            )
        key = local.timestamp()
        if key not in seen:
            seen.add(key)
            out.append(local.astimezone(timezone.utc))
    return out


def _parse_hik_time(value: Optional[str], tz=None) -> Optional[datetime]:
    """解析海康时间字符串为 UTC aware datetime。

    tz: 无时区数字串的基准时区(通常传设备时区)。设备按本地墙钟上报时,
    按 UTC 解释会把 endTime 虚大数小时,导致停录数小时仍误报「正常」;
    None 时保持旧行为按 UTC。
    """
    if not value:
        return None
    text = value.strip()
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        if re.search(r"[+-]\d{2}:\d{2}$", text):
            return datetime.fromisoformat(text).astimezone(timezone.utc)
        # 无时区:设备本地墙钟(与写侧 _fmt_rtsp_time 约定一致)
        return datetime.fromisoformat(text).replace(
            tzinfo=tz or timezone.utc
        ).astimezone(timezone.utc)
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

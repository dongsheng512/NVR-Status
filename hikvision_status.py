#!/usr/bin/env python3
"""
海康威视NVR/DVR状态查询脚本
查询设备信息、摄像头连接状态、录像计划/音频、近期落盘与硬盘状态
可选:短时RTSP抽检音视频轨(不下载整段录像,避免影响NVR存储与录像)
使用ISAPI接口(requests + HTTPDigestAuth)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
from requests.auth import HTTPDigestAuth

# 抑制 HTTPS 自签证书的告警
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass


# ANSI 颜色代码
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
    return os.path.dirname(os.path.abspath(__file__))


def _resource_dir() -> str:
    """只读资源目录(打包后为 _MEIPASS)。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


def _which_tools() -> Dict[str, Optional[str]]:
    """定位 ffmpeg/ffprobe: 优先捆绑 bin/, 再 PATH。"""
    names = ("ffmpeg", "ffprobe")
    found: Dict[str, Optional[str]] = {n: None for n in names}
    candidates = [
        os.path.join(_resource_dir(), "bin"),
        os.path.join(_project_dir(), "bin"),
        os.path.join(_project_dir(), "ffmpeg", "bin"),
    ]
    for d in candidates:
        for n in names:
            if found[n]:
                continue
            for exe in (n, f"{n}.exe"):
                p = os.path.join(d, exe)
                if os.path.isfile(p) and os.access(p, os.X_OK if os.name != "nt" else os.F_OK):
                    found[n] = p
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


class HikvisionNVR:
    # 深度抽检硬限制:保护NVR回放并发与带宽
    AV_SECONDS_MAX = 12
    AV_WORKERS_MAX = 3
    # 默认保存根目录名(位于项目文件夹下)
    AV_SAVE_ROOT_NAME = "av_samples"

    def __init__(
        self,
        ip: str,
        port: int = 80,
        username: str = "admin",
        password: str = "",
        use_ssl: bool = False,
        lookback_minutes: int = 60,
        check_disk_recording: bool = True,
        search_workers: int = 8,
        deep_av_check: bool = False,
        av_seconds: int = 6,
        av_workers: int = 2,
        av_limit: Optional[int] = None,
        silence_db: float = -80.0,
        busy_start_hour: int = 10,
        busy_end_hour: int = 18,
        av_save: bool = False,
        av_save_root: Optional[str] = None,
        quiet: bool = False,
        progress_callback=None,
    ):
        self.ip = ip
        self.port = port
        self.username = username
        self.password = password
        self.use_ssl = use_ssl
        self.lookback_minutes = max(1, lookback_minutes)
        self.check_disk_recording = check_disk_recording
        self.search_workers = max(1, search_workers)
        # GUI 等场景: quiet 抑制终端输出; progress_callback(msg: str) 汇报进度
        self.quiet = quiet
        self.progress_callback = progress_callback
        # 深度音视频抽检(默认关闭;仅短时RTSP,不整段下载)
        self.deep_av_check = deep_av_check
        self.av_seconds = max(3, min(int(av_seconds), self.AV_SECONDS_MAX))
        self.av_workers = max(1, min(int(av_workers), self.AV_WORKERS_MAX))
        self.av_limit = av_limit if av_limit is None or av_limit > 0 else None
        self.silence_db = silence_db
        # 抽检优先时段(本地时间):默认 10:00-18:00 人流较多
        self.busy_start_hour = max(0, min(23, int(busy_start_hour)))
        self.busy_end_hour = max(1, min(24, int(busy_end_hour)))
        if self.busy_end_hour <= self.busy_start_hour:
            self.busy_start_hour, self.busy_end_hour = 10, 18
        # 保存抽检片段:默认不保存;开启后写入 项目/av_samples/<时间戳>/
        self.av_save = bool(av_save)
        if self.av_save and not self.deep_av_check:
            # 保存必须依赖深度抽检拉流
            self.deep_av_check = True
        root = av_save_root or os.path.join(_project_dir(), self.AV_SAVE_ROOT_NAME)
        self.av_save_root = os.path.abspath(root)
        self.av_save_dir: Optional[str] = None  # 本次运行的时间戳子目录
        self.base_url = f"{'https' if use_ssl else 'http'}://{ip}:{port}"

        self.session = requests.Session()
        self.session.auth = HTTPDigestAuth(username, password)
        self.session.headers.update({"User-Agent": "hikvision_status/1.0"})
        if use_ssl:
            # 自签证书:不校验
            self.session.verify = False

        # 端点缓存:全流程每个端点只请求一次
        self._cache: Dict[str, Optional[ET.Element]] = {}
        self._recording_cache: Optional[List[Dict]] = None
        # 线程本地 Session,用于并发 CMSearch
        self._thread_local = threading.local()
        self._tools = _which_tools()
        self._save_lock = threading.Lock()
        # 设备时区缓存(从设备时间串解析,默认东八区)
        self._device_tz: Optional[timezone] = None

    def _log(self, msg: str, force: bool = False) -> None:
        """进度/日志: quiet 时仅回调; 否则打印并回调。"""
        if self.progress_callback:
            try:
                self.progress_callback(msg)
            except TypeError:
                # 兼容仅接受关键字参数的回调
                try:
                    self.progress_callback(msg=msg)
                except Exception:
                    pass
            except Exception:
                pass
        if not self.quiet or force:
            # 去掉 ANSI 便于 GUI 日志区
            plain = re.sub(r"\033\[[0-9;]*m", "", msg)
            print(msg if not self.quiet else plain)

    def _progress(
        self,
        msg: str = "",
        *,
        current: Optional[int] = None,
        total: Optional[int] = None,
        phase: str = "",
        overall: Optional[float] = None,
    ) -> None:
        """向 GUI 汇报查询进度（通道完成数 / 总进度 0~1）。"""
        if self.progress_callback:
            try:
                self.progress_callback(
                    msg,
                    current=current,
                    total=total,
                    phase=phase,
                    overall=overall,
                )
            except TypeError:
                if msg:
                    try:
                        self.progress_callback(msg)
                    except Exception:
                        pass
            except Exception:
                pass
        if msg and (not self.quiet):
            plain = re.sub(r"\033\[[0-9;]*m", "", msg)
            print(plain)

    def _get_device_tz(self) -> timezone:
        """获取设备时区:优先解析设备当前时间中的偏移,否则用本机本地时区。"""
        if self._device_tz is not None:
            return self._device_tz
        # 尝试设备状态里的 currentDeviceTime, 如 2026-07-29T12:36:13+08:00
        try:
            status = self.get_system_status()
            raw = status.get("当前时间") or ""
            m = re.search(r"([+-])(\d{2}):(\d{2})$", raw)
            if m:
                sign = 1 if m.group(1) == "+" else -1
                hours = int(m.group(2))
                mins = int(m.group(3))
                self._device_tz = timezone(sign * timedelta(hours=hours, minutes=mins))
                return self._device_tz
        except Exception:
            pass
        # 回退:本机本地时区(通常与设备同在东八区)
        local = datetime.now().astimezone()
        self._device_tz = local.tzinfo if local.tzinfo else timezone(timedelta(hours=8))
        return self._device_tz  # type: ignore[return-value]

    def _fmt_rtsp_time(self, dt: datetime) -> str:
        """格式化为海康 RTSP starttime/endtime。

        注意:设备回放 URI 中的数字按**设备本地墙钟**解析(不是 UTC)。
        若误写 UTC 的 05:43,画面 OSD 会显示 05:43 而非本地 13:43。
        后缀仍保留 Z(设备接受),但时分秒必须是本地时间。
        """
        local = dt.astimezone(self._get_device_tz())
        return local.strftime("%Y%m%dT%H%M%S") + "Z"

    def _thread_session(self) -> requests.Session:
        """获取当前线程专用 Session(并发检索时避免共享 Session)。"""
        sess = getattr(self._thread_local, "session", None)
        if sess is None:
            sess = requests.Session()
            sess.auth = HTTPDigestAuth(self.username, self.password)
            sess.headers.update({"User-Agent": "hikvision_status/1.0"})
            if self.use_ssl:
                sess.verify = False
            self._thread_local.session = sess
        return sess

    def _get(self, endpoint: str, tag: str = "", quiet: bool = False) -> str:
        """GET /ISAPI<endpoint>,返回原始文本。失败时打印可见警告并返回空串。"""
        url = f"{self.base_url}/ISAPI{endpoint}"
        try:
            resp = self.session.get(url, timeout=10)
        except requests.exceptions.Timeout:
            if not quiet and not self.quiet:
                print(f"  {Colors.error('⚠️ ' + (tag or endpoint) + '获取失败: 请求超时')}")
            return ""
        except requests.exceptions.RequestException as e:
            if not quiet and not self.quiet:
                print(f"  {Colors.error('⚠️ ' + (tag or endpoint) + '获取失败: ' + str(e))}")
            return ""
        if resp.status_code != 200:
            if not quiet and not self.quiet:
                print(f"  {Colors.error('⚠️ ' + (tag or endpoint) + f'获取失败: HTTP {resp.status_code}')}")
            return ""
        return resp.text

    def _post(
        self,
        endpoint: str,
        data: str,
        tag: str = "",
        quiet: bool = False,
        timeout: int = 15,
        use_thread_session: bool = False,
    ) -> Tuple[int, str]:
        """POST /ISAPI<endpoint>,返回 (status_code, text)。"""
        url = f"{self.base_url}/ISAPI{endpoint}"
        sess = self._thread_session() if use_thread_session else self.session
        try:
            resp = sess.post(
                url,
                data=data.encode("utf-8"),
                timeout=timeout,
                headers={"Content-Type": "application/xml"},
            )
        except requests.exceptions.Timeout:
            if not quiet and not self.quiet:
                print(f"  {Colors.error('⚠️ ' + (tag or endpoint) + '请求失败: 请求超时')}")
            return -1, ""
        except requests.exceptions.RequestException as e:
            if not quiet and not self.quiet:
                print(f"  {Colors.error('⚠️ ' + (tag or endpoint) + '请求失败: ' + str(e))}")
            return -1, ""
        return resp.status_code, resp.text or ""

    def _parse(self, endpoint: str, tag: str = "") -> Optional[ET.Element]:
        """请求并解析XML。带缓存:同一端点只请求一次。失败返回None。"""
        if endpoint in self._cache:
            return self._cache[endpoint]
        text = self._get(endpoint, tag)
        if not text:
            self._cache[endpoint] = None
            return None
        # 移除命名空间以便于解析
        text = re.sub(r'\s+xmlns="[^"]+"', '', text)
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            if not self.quiet:
                print(f"  {Colors.error('⚠️ ' + (tag or endpoint) + '解析失败: ' + str(e))}")
            root = None
        self._cache[endpoint] = root
        return root

    def get_device_info(self) -> Dict:
        """获取设备基本信息"""
        root = self._parse("/System/deviceInfo", "设备信息")
        if root is None:
            return {}
        return {
            "设备名称": root.findtext("deviceName", "未知"),
            "设备ID": root.findtext("deviceID", "未知"),
            "型号": root.findtext("model", "未知"),
            "序列号": root.findtext("serialNumber", "未知"),
            "固件版本": root.findtext("firmwareVersion", "未知"),
            "MAC地址": root.findtext("macAddress", "未知"),
            "设备类型": root.findtext("deviceType", "未知"),
        }

    def get_storage_status(self) -> List[Dict]:
        """获取硬盘状态"""
        root = self._parse("/ContentMgmt/storage", "硬盘状态")
        if root is None:
            return []
        drives = []
        for hdd in root.findall(".//hdd"):
            capacity = _to_int(hdd.findtext('capacity'))
            free_space = _to_int(hdd.findtext('freeSpace'))
            used_space = capacity - free_space
            usage_rate = (used_space / capacity * 100) if capacity > 0 else 0

            drive = {
                "盘符": hdd.findtext("hddName", "未知"),
                "容量TB": f"{capacity / 1024 / 1024:.1f}",
                "剩余空间TB": f"{free_space / 1024 / 1024:.1f}",
                "已用空间TB": f"{used_space / 1024 / 1024:.1f}",
                "使用率": f"{usage_rate:.1f}%",
                "状态": hdd.findtext("status", "未知"),
                "类型": hdd.findtext("hddType", "未知"),
                "属性": hdd.findtext("property", "未知"),
                "制造商": hdd.findtext("manufacturer", "未知"),
            }
            drives.append(drive)
        return drives

    def get_system_status(self) -> Dict:
        """获取系统运行状态(内存、运行时长等)"""
        root = self._parse("/System/status", "系统状态")
        if root is None:
            return {}
        memory_usage = _to_float(root.findtext(".//memoryUsage"))
        memory_available = _to_float(root.findtext(".//memoryAvailable"))
        total_memory = memory_usage + memory_available
        memory_usage_rate = (memory_usage / total_memory * 100) if total_memory > 0 else 0

        up_time = _to_int(root.findtext("deviceUpTime"))
        up_days = up_time // 86400
        up_hours = (up_time % 86400) // 3600
        up_minutes = (up_time % 3600) // 60

        return {
            "当前时间": root.findtext("currentDeviceTime", "未知"),
            "运行时长": f"{up_days}天{up_hours}小时{up_minutes}分钟",
            "内存使用": f"{memory_usage:.1f} MB",
            "内存可用": f"{memory_available:.1f} MB",
            "内存使用率": f"{memory_usage_rate:.1f}%",
        }

    def get_alarm_status(self) -> List[Dict]:
        """获取报警状态(复用已缓存的storage)"""
        root = self._parse("/ContentMgmt/storage", "硬盘状态")
        if root is None:
            return []
        alarms = []
        for hdd in root.findall(".//hdd"):
            status = hdd.findtext("status", "unknown")
            if status != "ok":
                alarms.append({
                    "类型": "硬盘异常",
                    "盘符": hdd.findtext("hddName", "未知"),
                    "状态": status,
                    "描述": "硬盘状态异常,可能已满或故障"
                })
        return alarms

    def get_cameras(self) -> List[Dict]:
        """获取摄像头真实连接状态。

        优先使用 InputProxy 接口(可获取在线/离线、名称、IP、型号);
        设备不支持时回退到 Streaming channels(仅统计已配置通道数)。
        """
        cameras = self._get_input_proxy_cameras()
        if cameras is not None:
            return cameras
        return self._get_streaming_channels()

    def _get_input_proxy_cameras(self) -> Optional[List[Dict]]:
        """通过 InputProxy 接口获取摄像头列表(含真实在线状态)。不支持时返回None。"""
        info_root = self._parse("/ContentMgmt/InputProxy/channels", "摄像头列表")
        # 接口不支持(模拟通道NVR)时回退
        if info_root is None or info_root.tag == "ResponseStatus":
            return None

        status_root = self._parse("/ContentMgmt/InputProxy/channels/status", "摄像头状态")

        # 按 id 合并在线状态
        online_map: Dict[str, Dict] = {}
        if status_root is not None and status_root.tag != "ResponseStatus":
            for s in status_root.findall("InputProxyChannelStatus"):
                cid = s.findtext("id", "")
                online_map[cid] = {
                    "在线": s.findtext("online", "unknown"),
                    "检测状态": s.findtext("chanDetectResult", "unknown"),
                }

        cameras: List[Dict] = []
        for ch in info_root.findall("InputProxyChannel"):
            cid = ch.findtext("id", "未知")
            online_info = online_map.get(cid, {})
            cameras.append({
                "id": cid,
                "名称": ch.findtext("name", "未知"),
                "IP": ch.findtext(".//ipAddress", "未知"),
                "型号": ch.findtext(".//model", "未知"),
                "在线": online_info.get("在线", "unknown"),
                "检测状态": online_info.get("检测状态", "unknown"),
            })

        # 按通道ID数值排序
        cameras.sort(key=lambda c: _to_int(c["id"], default=1 << 30))
        return cameras

    def _get_streaming_channels(self) -> List[Dict]:
        """回退:统计已配置的流通道(无法判断真实在线)"""
        root = self._parse("/ContentMgmt/Streaming/channels", "摄像头通道")
        if root is None:
            return []
        cameras: List[Dict] = []
        for channel in root.findall(".//StreamingChannel"):
            cid = channel.findtext("id", "未知")
            # 主码流通道(id以01结尾)代表一个物理摄像头
            if cid.endswith("01"):
                cameras.append({
                    "id": cid[:-2] if len(cid) > 2 else cid,
                    "名称": "未知",
                    "IP": "未知",
                    "型号": "未知",
                    "在线": channel.findtext("enabled", "unknown"),
                    "检测状态": "已配置",
                })
        cameras.sort(key=lambda c: _to_int(c["id"], default=1 << 30))
        return cameras

    # 录像模式简称映射
    _REC_MODE = {
        "CMR": "连续录像",
        "MDR": "移动侦测",
        "ADR": "报警录像",
        "AE": "事件触发",
    }

    @staticmethod
    def _physical_channel(tr: ET.Element) -> str:
        """从 Track 节点解析物理通道号(优先 SrcChannel)。"""
        src = tr.findtext(".//SrcChannel")
        if src:
            return src
        raw = tr.findtext("Channel") or tr.findtext("id") or "未知"
        tid = _to_int(raw, default=-1)
        # 主码流 track 常见编码: 101/201/.../6401 -> 通道 1/2/.../64
        if tid >= 100 and tid % 100 == 1:
            return str(tid // 100)
        return raw

    @staticmethod
    def _save_audio(tr: ET.Element) -> Optional[bool]:
        """解析 SaveAudio 扩展字段。None 表示接口未返回该字段。"""
        for el in tr.iter():
            if el.tag == "SaveAudio":
                if el.text is None:
                    return None
                return el.text.strip().lower() == "true"
        return None

    def _search_track_recent(self, track_id: str, lookback_minutes: int) -> Dict:
        """CMSearch 查询某 track 近 lookback_minutes 分钟是否有录像片段。"""
        empty = {
            "ok": False,
            "status": "未知",
            "detail": "",
            "segments": 0,
            "latest_end": None,
            "playback_uri": None,
            "seg_start": None,
            "seg_end": None,
        }
        now = datetime.now(timezone.utc)
        start = now - timedelta(minutes=lookback_minutes)
        end = now + timedelta(minutes=5)
        start_s = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_s = end.strftime("%Y-%m-%dT%H:%M:%SZ")
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<CMSearchDescription>
  <searchID>01234567-89AB-CDEF-0123-456789ABCDEF</searchID>
  <trackList>
    <trackID>{track_id}</trackID>
  </trackList>
  <timeSpanList>
    <timeSpan>
      <startTime>{start_s}</startTime>
      <endTime>{end_s}</endTime>
    </timeSpan>
  </timeSpanList>
  <maxResults>10</maxResults>
  <searchResultPostion>0</searchResultPostion>
</CMSearchDescription>"""

        code, text = self._post(
            "/ContentMgmt/search",
            body,
            tag=f"录像检索 track {track_id}",
            quiet=True,
            timeout=20,
            use_thread_session=True,
        )
        if code != 200 or not text:
            empty["detail"] = f"检索失败(HTTP {code})" if code > 0 else "检索失败"
            return empty

        text = re.sub(r'\s+xmlns="[^"]+"', '', text)
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            empty["detail"] = "检索结果解析失败"
            return empty

        if root.tag == "ResponseStatus":
            empty["detail"] = root.findtext("statusString", "检索失败")
            return empty

        matches = root.findall(".//searchMatchItem")
        latest_end: Optional[datetime] = None
        covers_now = False
        # 允许当前段 endTime 略早于 now 的容差(秒):连续录像切段/时钟偏差
        lag_grace = 900  # 15 分钟
        best_uri: Optional[str] = None
        best_st: Optional[datetime] = None
        best_et: Optional[datetime] = None
        best_score: Tuple[int, float] = (-1, -1.0)

        for item in matches:
            st = _parse_hik_time(item.findtext(".//startTime"))
            et = _parse_hik_time(item.findtext(".//endTime"))
            uri = unescape((item.findtext(".//playbackURI") or "").strip())
            if et is not None and (latest_end is None or et > latest_end):
                latest_end = et
            covers = False
            if st is not None and et is not None and st <= now <= et + timedelta(seconds=lag_grace):
                covers_now = True
                covers = True
            elif et is not None and et >= now - timedelta(seconds=lag_grace):
                covers_now = True
                covers = True
            # 优先选覆盖当前的片段,其次 end 最晚
            score = (2 if covers else 0, et.timestamp() if et else 0.0)
            if uri and score >= best_score:
                best_score = score
                best_uri = uri
                best_st, best_et = st, et

        if not matches:
            return {
                "ok": False,
                "status": "异常",
                "detail": f"近{lookback_minutes}分钟无录像片段",
                "segments": 0,
                "latest_end": None,
                "playback_uri": None,
                "seg_start": None,
                "seg_end": None,
            }

        if covers_now:
            detail = "近期有录像"
            if latest_end is not None:
                detail += f"(至 {latest_end.astimezone().strftime('%H:%M:%S')})"
            return {
                "ok": True,
                "status": "正常",
                "detail": detail,
                "segments": len(matches),
                "latest_end": latest_end,
                "playback_uri": best_uri,
                "seg_start": best_st,
                "seg_end": best_et,
            }

        # 有历史片段但未覆盖到当前:仍判异常(可能停录)
        end_txt = latest_end.astimezone().strftime("%Y-%m-%d %H:%M:%S") if latest_end else "未知"
        return {
            "ok": False,
            "status": "异常",
            "detail": f"仅有较早片段(最晚 {end_txt})",
            "segments": len(matches),
            "latest_end": latest_end,
            "playback_uri": best_uri,
            "seg_start": best_st,
            "seg_end": best_et,
        }

    def _busy_hours_window(self) -> Tuple[datetime, datetime, str]:
        """计算优先抽检的繁忙时段(本地 10:00-18:00 默认)。

        返回 (window_start_utc, window_end_utc, 说明)。
        - 若当前在繁忙时段内: 取 [当日 busy_start, min(现在, busy_end)]
        - 若当前早于 busy_start: 取昨日完整繁忙时段
        - 若当前晚于 busy_end: 取今日完整繁忙时段
        """
        local = datetime.now().astimezone()
        sh, eh = self.busy_start_hour, self.busy_end_hour

        def day_window(day: datetime) -> Tuple[datetime, datetime]:
            start = day.replace(hour=sh, minute=0, second=0, microsecond=0)
            # end_hour=18 表示 18:00 整点为止
            if eh >= 24:
                end = (day + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
            else:
                end = day.replace(hour=eh, minute=0, second=0, microsecond=0)
            return start, end

        today_s, today_e = day_window(local)

        if today_s <= local < today_e:
            win_s, win_e = today_s, min(local - timedelta(seconds=30), today_e)
            if win_e <= win_s:
                win_e = min(today_e, win_s + timedelta(minutes=5))
            label = (
                f"今日 {win_s.strftime('%H:%M')}-{win_e.strftime('%H:%M')} "
                f"(繁忙时段 {sh:02d}:00-{eh:02d}:00)"
            )
        elif local < today_s:
            yday = local - timedelta(days=1)
            win_s, win_e = day_window(yday)
            label = (
                f"昨日 {win_s.strftime('%m-%d %H:%M')}-{win_e.strftime('%H:%M')} "
                f"(当前未到 {sh:02d}:00)"
            )
        else:
            win_s, win_e = today_s, today_e
            label = (
                f"今日 {win_s.strftime('%H:%M')}-{win_e.strftime('%H:%M')} "
                f"(当前已过 {eh:02d}:00)"
            )

        return (
            win_s.astimezone(timezone.utc),
            win_e.astimezone(timezone.utc),
            label,
        )

    def _pick_busy_clip_times(self, seconds: int) -> Tuple[datetime, datetime, str]:
        """在繁忙时段窗口内选取短抽检起止时间(UTC)。

        优先取窗口内接近 14:00 的人流高峰;若当前在繁忙时段且已过 14:00,
        则取窗口末尾附近(更接近现在且仍在繁忙段)。
        """
        win_s, win_e, label = self._busy_hours_window()
        local = datetime.now().astimezone()
        # 目标锚点: 本地 14:00
        peak_local = local.replace(hour=14, minute=0, second=0, microsecond=0)
        # 若窗口是昨天, peak 也落在昨天
        win_s_local = win_s.astimezone()
        if win_s_local.date() != local.date():
            peak_local = win_s_local.replace(hour=14, minute=0, second=0, microsecond=0)
        peak = peak_local.astimezone(timezone.utc)

        sec = max(3, seconds)
        if win_s <= peak <= win_e:
            clip_e = min(peak + timedelta(seconds=sec), win_e)
            clip_s = clip_e - timedelta(seconds=sec)
            if clip_s < win_s:
                clip_s = win_s
                clip_e = min(win_e, clip_s + timedelta(seconds=sec))
            where = "14:00附近"
        else:
            # 取窗口末尾(当前在繁忙时段时即接近现在)
            clip_e = win_e
            clip_s = clip_e - timedelta(seconds=sec)
            if clip_s < win_s:
                clip_s = win_s
                clip_e = min(win_e, clip_s + timedelta(seconds=sec))
            where = "时段末尾"

        clip_label = (
            f"{label}; 抽检点 {clip_s.astimezone().strftime('%m-%d %H:%M:%S')}"
            f"({where})"
        )
        return clip_s, clip_e, clip_label

    def _search_track_in_range(
        self,
        track_id: str,
        start: datetime,
        end: datetime,
    ) -> Dict:
        """CMSearch 指定时间范围,返回含 playback_uri 的结果。"""
        empty = {
            "ok": False,
            "playback_uri": None,
            "seg_start": None,
            "seg_end": None,
            "detail": "",
        }
        # 查询窗口略放大,便于命中跨段录像
        q_start = (start - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        q_end = (end + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<CMSearchDescription>
  <searchID>01234567-89AB-CDEF-0123-456789ABCDEF</searchID>
  <trackList>
    <trackID>{track_id}</trackID>
  </trackList>
  <timeSpanList>
    <timeSpan>
      <startTime>{q_start}</startTime>
      <endTime>{q_end}</endTime>
    </timeSpan>
  </timeSpanList>
  <maxResults>10</maxResults>
  <searchResultPostion>0</searchResultPostion>
</CMSearchDescription>"""
        code, text = self._post(
            "/ContentMgmt/search",
            body,
            tag=f"繁忙时段检索 track {track_id}",
            quiet=True,
            timeout=20,
            use_thread_session=True,
        )
        if code != 200 or not text:
            empty["detail"] = f"检索失败(HTTP {code})" if code > 0 else "检索失败"
            return empty
        text = re.sub(r'\s+xmlns="[^"]+"', '', text)
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            empty["detail"] = "检索结果解析失败"
            return empty
        if root.tag == "ResponseStatus":
            empty["detail"] = root.findtext("statusString", "检索失败")
            return empty

        matches = root.findall(".//searchMatchItem")
        if not matches:
            empty["detail"] = "繁忙时段无录像片段"
            return empty

        # 选与目标 clip 重叠最好、或 end 最接近 clip 的片段
        best_uri = None
        best_st = None
        best_et = None
        best_score: Tuple[int, float] = (-1, -1.0)
        for item in matches:
            st = _parse_hik_time(item.findtext(".//startTime"))
            et = _parse_hik_time(item.findtext(".//endTime"))
            uri = unescape((item.findtext(".//playbackURI") or "").strip())
            if not uri:
                continue
            overlaps = 0
            if st is not None and et is not None and st <= end and et >= start:
                overlaps = 1
            score = (overlaps, et.timestamp() if et else 0.0)
            if score >= best_score:
                best_score = score
                best_uri = uri
                best_st, best_et = st, et

        if not best_uri:
            empty["detail"] = "繁忙时段无可用回放URI"
            return empty
        return {
            "ok": True,
            "playback_uri": best_uri,
            "seg_start": best_st,
            "seg_end": best_et,
            "detail": "ok",
        }

    def _build_short_rtsp(
        self,
        playback_uri: str,
        seg_start: Optional[datetime],
        seg_end: Optional[datetime],
        seconds: int,
        clip_start: Optional[datetime] = None,
        clip_end: Optional[datetime] = None,
    ) -> Optional[str]:
        """从完整 playbackURI 截取短窗口,并注入鉴权。绝不整段下载。

        若传入 clip_start/clip_end(UTC),优先使用(用于繁忙时段定点抽检);
        否则回退为片段末尾附近短窗。
        """
        if not playback_uri or not playback_uri.startswith("rtsp://"):
            return None
        now = datetime.now(timezone.utc)
        m = re.search(
            r"starttime=(\d{8}T\d{6}Z).*endtime=(\d{8}T\d{6}Z)",
            playback_uri,
            re.I,
        )
        if m:
            try:
                uri_s = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                uri_e = datetime.strptime(m.group(2), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                uri_s, uri_e = seg_start, seg_end
        else:
            uri_s, uri_e = seg_start, seg_end

        if uri_e is None:
            uri_e = now
        if uri_s is None:
            uri_s = uri_e - timedelta(minutes=5)

        if clip_start is not None and clip_end is not None:
            clip_s = max(uri_s, clip_start)
            clip_e = min(uri_e, clip_end)
            if clip_e <= clip_s:
                # 指定点不在该段内,夹到段内可用区域
                clip_e = min(uri_e, uri_s + timedelta(seconds=seconds))
                clip_s = uri_s
            # 保证至少接近请求时长
            if (clip_e - clip_s).total_seconds() < max(2, seconds // 2):
                clip_e = min(uri_e, clip_s + timedelta(seconds=seconds))
        else:
            # 取片段末尾附近短窗,避开正在写入的最前沿几秒
            clip_e = min(now - timedelta(seconds=3), uri_e - timedelta(seconds=2))
            if clip_e <= uri_s:
                clip_e = uri_e
            clip_s = clip_e - timedelta(seconds=seconds)
            if clip_s < uri_s:
                clip_s = uri_s
            if clip_e <= clip_s:
                clip_e = min(uri_e, clip_s + timedelta(seconds=seconds))

        short = playback_uri
        if m:
            # 必须用设备本地墙钟写入,不能用 UTC(否则 OSD 会显示成早上 5 点多)
            short = re.sub(
                r"starttime=\d{8}T\d{6}Z",
                f"starttime={self._fmt_rtsp_time(clip_s)}",
                short,
                flags=re.I,
            )
            short = re.sub(
                r"endtime=\d{8}T\d{6}Z",
                f"endtime={self._fmt_rtsp_time(clip_e)}",
                short,
                flags=re.I,
            )
        # 去掉 size,避免设备按完整段长度处理
        short = re.sub(r"[&?]size=\d+", "", short)
        short = short.replace("?&", "?").rstrip("?&")

        # rtsp://host/path -> rtsp://user:pass@host/path (密码 URL 编码)
        host_path = short[len("rtsp://"):]
        user = quote(self.username, safe="")
        pwd = quote(self.password, safe="")
        return f"rtsp://{user}:{pwd}@{host_path}"

    def _prepare_av_save_dir(self) -> Optional[str]:
        """创建本次抽检的保存目录: <项目>/av_samples/<YYYYMMDD_HHMMSS>/"""
        if not self.av_save:
            return None
        if self.av_save_dir and os.path.isdir(self.av_save_dir):
            return self.av_save_dir
        with self._save_lock:
            if self.av_save_dir and os.path.isdir(self.av_save_dir):
                return self.av_save_dir
            stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
            # 同一秒多次创建时追加序号
            base = os.path.join(self.av_save_root, stamp)
            path = base
            n = 1
            while os.path.exists(path):
                path = f"{base}_{n}"
                n += 1
            os.makedirs(path, exist_ok=True)
            self.av_save_dir = path
            return path

    def _save_clip_file(
        self,
        tmp_path: str,
        track_id: str,
        channel: str = "",
        name: str = "",
        clip_start: Optional[datetime] = None,
    ) -> Optional[str]:
        """将临时片段复制到保存目录,返回保存路径。"""
        save_dir = self._prepare_av_save_dir()
        if not save_dir or not tmp_path or not os.path.isfile(tmp_path):
            return None
        ch = _safe_filename(str(channel or track_id), 16)
        nm = _safe_filename(name or "cam", 40)
        tid = _safe_filename(str(track_id), 16)
        # 文件名带本地抽检时刻,便于与画面 OSD 核对
        if clip_start is not None:
            ttag = clip_start.astimezone(self._get_device_tz()).strftime("%H%M%S")
        else:
            ttag = datetime.now().astimezone(self._get_device_tz()).strftime("%H%M%S")
        fname = f"ch{ch}_{nm}_track{tid}_{ttag}.mkv"
        dest = os.path.join(save_dir, fname)
        # 重名则追加序号
        if os.path.exists(dest):
            stem, ext = os.path.splitext(fname)
            i = 1
            while os.path.exists(dest):
                dest = os.path.join(save_dir, f"{stem}_{i}{ext}")
                i += 1
        try:
            shutil.copy2(tmp_path, dest)
            return dest
        except OSError:
            return None

    def _probe_track_av(
        self,
        track_id: str,
        playback_uri: Optional[str],
        seg_start: Optional[datetime],
        seg_end: Optional[datetime],
        expect_audio: Optional[bool],
        clip_start: Optional[datetime] = None,
        clip_end: Optional[datetime] = None,
        sample_label: str = "",
        channel: str = "",
        name: str = "",
        save_clip_start: Optional[datetime] = None,
    ) -> Dict:
        """短时 RTSP 拉流到临时 mkv,用 ffprobe 检查音视频轨。

        安全策略:
        - 仅 RTSP 回放,时长严格限制(默认数秒)
        - 使用 -c copy,本地不重编码、不写 NVR 盘
        - 临时文件在本机 /tmp;默认用后必删
        - 若开启 av_save,则复制到项目 av_samples/<时间戳>/
        - 不调用 ContentMgmt/download 整段下载
        - 优先使用繁忙时段(默认 10:00-18:00)定点抽检
        """
        result = {
            "视频抽检": "未知",
            "音频抽检": "未知",
            "抽检详情": "",
            "video_codec": None,
            "audio_codec": None,
            "resolution": None,
            "mean_volume_db": None,
            "抽检时段": sample_label,
            "保存路径": None,
        }
        ffmpeg = self._tools.get("ffmpeg")
        ffprobe = self._tools.get("ffprobe")
        if not ffmpeg or not ffprobe:
            result["视频抽检"] = "未知"
            result["音频抽检"] = "未知"
            result["抽检详情"] = "本机未安装 ffmpeg/ffprobe"
            return result

        if not playback_uri:
            result["视频抽检"] = "跳过"
            result["音频抽检"] = "跳过"
            result["抽检详情"] = "无回放URI"
            return result

        rtsp = self._build_short_rtsp(
            playback_uri,
            seg_start,
            seg_end,
            self.av_seconds,
            clip_start=clip_start,
            clip_end=clip_end,
        )
        if not rtsp:
            result["抽检详情"] = "无法构造短时RTSP地址"
            return result

        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(prefix=f"nvr_av_{track_id}_", suffix=".mkv")
            os.close(fd)
            # 低干扰: TCP RTSP、严格短时长、stream copy(本地不重编码)
            cmd = [
                ffmpeg, "-y",
                "-hide_banner", "-loglevel", "error",
                "-rtsp_transport", "tcp",
                "-i", rtsp,
                "-t", str(self.av_seconds),
                "-map", "0",
                "-c", "copy",
                "-f", "matroska",
                tmp_path,
            ]
            timeout = self.av_seconds + 45
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
            if proc.returncode != 0 or size < 1024:
                err = (proc.stderr or "").strip().splitlines()
                err_s = err[-1] if err else f"rc={proc.returncode}"
                result["视频抽检"] = "异常"
                result["音频抽检"] = "异常"
                result["抽检详情"] = f"短时拉流失败({err_s[:120]})"
                return result

            probe_cmd = [
                ffprobe, "-v", "error",
                "-show_streams",
                "-show_format",
                "-of", "json",
                tmp_path,
            ]
            p2 = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
            if p2.returncode != 0:
                result["视频抽检"] = "异常"
                result["音频抽检"] = "未知"
                result["抽检详情"] = "ffprobe解析失败"
                return result

            data = json.loads(p2.stdout or "{}")
            streams = data.get("streams") or []
            vstreams = [s for s in streams if s.get("codec_type") == "video"]
            astreams = [s for s in streams if s.get("codec_type") == "audio"]

            if vstreams:
                vs = vstreams[0]
                w, h = vs.get("width"), vs.get("height")
                result["video_codec"] = vs.get("codec_name")
                result["resolution"] = f"{w}x{h}" if w and h else None
                if w and h and int(w) >= 160 and int(h) >= 120:
                    result["视频抽检"] = "正常"
                else:
                    result["视频抽检"] = "异常"
                    result["抽检详情"] = f"视频分辨率异常({result['resolution']})"
            else:
                result["视频抽检"] = "异常"
                result["抽检详情"] = "无视频轨"

            if astreams:
                as_ = astreams[0]
                result["audio_codec"] = as_.get("codec_name")
                result["音频抽检"] = "正常"
                # 粗测静音:仅当期望有音频时
                if expect_audio is not False:
                    vol_cmd = [
                        ffmpeg, "-hide_banner", "-nostats",
                        "-i", tmp_path,
                        "-t", str(min(self.av_seconds, 5)),
                        "-af", "volumedetect",
                        "-f", "null", "-",
                    ]
                    vp = subprocess.run(
                        vol_cmd, capture_output=True, text=True, timeout=40
                    )
                    mean_m = re.search(
                        r"mean_volume:\s*([-\d.]+)\s*dB",
                        vp.stderr or "",
                    )
                    if mean_m:
                        mean_db = float(mean_m.group(1))
                        result["mean_volume_db"] = mean_db
                        if mean_db <= self.silence_db:
                            result["音频抽检"] = "警告"
                            extra = f"疑似静音(mean {mean_db:.1f}dB)"
                            result["抽检详情"] = (
                                f"{result['抽检详情']}; {extra}" if result["抽检详情"]
                                else extra
                            )
            else:
                if expect_audio is True:
                    result["音频抽检"] = "异常"
                    extra = "配置含音频但文件无音频轨"
                    result["抽检详情"] = (
                        f"{result['抽检详情']}; {extra}" if result["抽检详情"]
                        else extra
                    )
                elif expect_audio is False:
                    result["音频抽检"] = "跳过"
                else:
                    result["音频抽检"] = "警告"
                    extra = "无音频轨"
                    result["抽检详情"] = (
                        f"{result['抽检详情']}; {extra}" if result["抽检详情"]
                        else extra
                    )

            if result["视频抽检"] == "正常" and result["音频抽检"] in ("正常", "跳过"):
                parts = []
                if result.get("resolution"):
                    parts.append(result["resolution"])
                if result.get("video_codec"):
                    parts.append(result["video_codec"])
                if result.get("audio_codec"):
                    parts.append(result["audio_codec"])
                if result.get("mean_volume_db") is not None:
                    parts.append(f"{result['mean_volume_db']:.1f}dB")
                if not result["抽检详情"]:
                    prefix = "短时抽检OK"
                    if sample_label:
                        # 只保留抽检点时间,避免详情过长
                        m = re.search(r"抽检点\s+([0-9\- :]+)", sample_label)
                        if m:
                            prefix = f"短时抽检OK@{m.group(1).strip()}"
                    result["抽检详情"] = prefix + " " + " ".join(parts)

            # 成功拉到有效片段后,可选保存到项目目录
            if self.av_save and size >= 1024:
                saved = self._save_clip_file(
                    tmp_path,
                    track_id=str(track_id),
                    channel=str(channel or ""),
                    name=str(name or ""),
                    clip_start=save_clip_start or clip_start,
                )
                if saved:
                    result["保存路径"] = saved
                else:
                    extra = "保存片段失败"
                    result["抽检详情"] = (
                        f"{result['抽检详情']}; {extra}" if result["抽检详情"]
                        else extra
                    )

            return result
        except subprocess.TimeoutExpired:
            result["视频抽检"] = "异常"
            result["音频抽检"] = "未知"
            result["抽检详情"] = "拉流超时"
            return result
        except Exception as e:
            result["视频抽检"] = "未知"
            result["音频抽检"] = "未知"
            result["抽检详情"] = f"抽检异常: {e}"
            return result
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _run_deep_av_checks(self, records: List[Dict]) -> None:
        """对通道做短时音视频抽检(低并发,默认仅落盘正常的通道)。

        抽检时间优先落在繁忙时段(默认本地 10:00-18:00),人流较多便于验证音视频。
        """
        if not self.deep_av_check:
            for r in records:
                r.setdefault("视频抽检", "跳过")
                r.setdefault("音频抽检", "跳过")
                r.setdefault("抽检详情", "未启用深度抽检")
            return

        # 已配置录像且落盘正常即可作为候选(繁忙时段会单独检索 URI)
        candidates = [
            r for r in records
            if r.get("已启用录像") and r.get("落盘状态") == "正常"
        ]
        for r in records:
            if r not in candidates:
                if not r.get("已启用录像"):
                    r["视频抽检"] = "跳过"
                    r["音频抽检"] = "跳过"
                    r["抽检详情"] = "未配置录像"
                elif r.get("落盘状态") != "正常":
                    r["视频抽检"] = "跳过"
                    r["音频抽检"] = "跳过"
                    r["抽检详情"] = "近期无录像/未知,跳过拉流"
                else:
                    r["视频抽检"] = "跳过"
                    r["音频抽检"] = "跳过"
                    r["抽检详情"] = "跳过"

        if self.av_limit is not None:
            candidates = candidates[: self.av_limit]

        if not candidates:
            self._log("深度抽检: 无可用通道(需近期有录像)")
            return

        clip_s, clip_e, sample_label = self._pick_busy_clip_times(self.av_seconds)
        save_dir = self._prepare_av_save_dir() if self.av_save else None
        self._log(
            f"深度音视频抽检: {len(candidates)} 通道 × {self.av_seconds}s RTSP"
            f", 并发 {self.av_workers} (仅短时回放,不写NVR盘)"
        )
        self._log(f"优先时段: {sample_label}")
        if save_dir:
            self._log(f"片段保存目录: {save_dir}")

        def _job(rec: Dict) -> Tuple[str, Dict]:
            tid = str(rec["track_id"])
            # 在繁忙时段窗口检索回放URI
            found = self._search_track_in_range(tid, clip_s, clip_e)
            uri = found.get("playback_uri") or rec.get("playback_uri")
            seg_s = found.get("seg_start") if found.get("ok") else rec.get("seg_start")
            seg_e = found.get("seg_end") if found.get("ok") else rec.get("seg_end")
            use_clip = (clip_s, clip_e) if found.get("ok") else (None, None)
            label = sample_label if found.get("ok") else (
                sample_label + "; 繁忙时段未命中,回退近期片段"
            )
            if not uri:
                return tid, {
                    "视频抽检": "跳过",
                    "音频抽检": "跳过",
                    "抽检详情": found.get("detail") or "无回放URI",
                    "抽检时段": label,
                    "保存路径": None,
                }
            res = self._probe_track_av(
                track_id=tid,
                playback_uri=uri,
                seg_start=seg_s,
                seg_end=seg_e,
                expect_audio=rec.get("录像含音频"),
                clip_start=use_clip[0],
                clip_end=use_clip[1],
                sample_label=label,
                channel=str(rec.get("通道") or ""),
                name=str(rec.get("名称") or ""),
                save_clip_start=use_clip[0] or clip_s,
            )
            return tid, res

        results: Dict[str, Dict] = {}
        total_av = len(candidates)
        done_av = 0
        # 深度抽检约占总进度 70%→96%
        deep_lo, deep_hi = 0.70, 0.96
        with ThreadPoolExecutor(max_workers=self.av_workers) as pool:
            futs = [pool.submit(_job, r) for r in candidates]
            for fut in as_completed(futs):
                tid, res = fut.result()
                results[tid] = res
                done_av += 1
                frac = done_av / total_av if total_av else 1.0
                overall = deep_lo + (deep_hi - deep_lo) * frac
                # 节流：每完成 1 路或每 5% 汇报，保证进度条跟手
                if (
                    done_av == 1
                    or done_av == total_av
                    or done_av % max(1, total_av // 20) == 0
                ):
                    self._progress(
                        f"深度抽检 {done_av}/{total_av}",
                        current=done_av,
                        total=total_av,
                        phase="deep",
                        overall=overall,
                    )

        saved_n = 0
        for r in candidates:
            res = results.get(str(r["track_id"]), {})
            r["视频抽检"] = res.get("视频抽检", "未知")
            r["音频抽检"] = res.get("音频抽检", "未知")
            r["抽检详情"] = res.get("抽检详情", "")
            r["抽检时段"] = res.get("抽检时段", sample_label)
            r["video_codec"] = res.get("video_codec")
            r["audio_codec"] = res.get("audio_codec")
            r["resolution"] = res.get("resolution")
            r["mean_volume_db"] = res.get("mean_volume_db")
            r["保存路径"] = res.get("保存路径")
            if r.get("保存路径"):
                saved_n += 1

        if self.av_save:
            if save_dir and saved_n:
                self._log(f"已保存 {saved_n} 个抽检片段 → {save_dir}")
            elif save_dir:
                self._log(f"已创建目录但未保存到文件: {save_dir}")

    def get_recording_status(self) -> List[Dict]:
        """获取各通道录像计划、是否含音频、近期落盘是否正常。

        通过 /ContentMgmt/record/tracks 解析:
        - ScheduleAction/Actions/Record=true 表示该时段已配置录像
        - ActionRecordingMode 表示录像模式(CMR连续/MDR动测/ADR报警/AE事件)
        - CustomExtension/SaveAudio 表示是否保存音频
        并通过 CMSearch 检查近 lookback_minutes 是否有实际录像。

        注:Track 的顶层 Enable 字段在网络摄像机NVR上恒为 false,不可信,
        必须看 ScheduleAction 里的 Record 标志。
        """
        if self._recording_cache is not None:
            return self._recording_cache

        root = self._parse("/ContentMgmt/record/tracks", "录像计划")
        if root is None:
            self._recording_cache = []
            return []

        cameras = self.get_cameras()
        name_map = {c["id"]: c.get("名称", "未知") for c in cameras}
        online_map = {c["id"]: c.get("在线", "unknown") for c in cameras}

        records: List[Dict] = []
        for tr in root.findall("Track"):
            track_id = tr.findtext("id") or tr.findtext("Channel") or "未知"
            channel = self._physical_channel(tr)
            actions = tr.findall(".//Actions")
            rec_actions = [a for a in actions if a.findtext("Record") == "true"]
            modes: Dict[str, int] = {}
            for a in actions:
                m = a.findtext("ActionRecordingMode", "")
                if m:
                    modes[m] = modes.get(m, 0) + 1
            total = len(actions)
            enabled = bool(rec_actions)
            main_mode = max(modes, key=modes.get) if modes else tr.findtext("DefaultRecordingMode", "未知")
            save_audio = self._save_audio(tr)

            records.append({
                "track_id": track_id,
                "通道": channel,
                "名称": name_map.get(channel, "未知"),
                "在线": online_map.get(channel, "unknown"),
                "已启用录像": enabled,
                "录像模式": self._REC_MODE.get(main_mode, main_mode),
                "录像时段": f"{len(rec_actions)}/{total}" if total else "0/0",
                "录像含音频": save_audio,  # True/False/None
                "落盘状态": "跳过",
                "落盘详情": "未检查",
                "playback_uri": None,
                "seg_start": None,
                "seg_end": None,
                "视频抽检": "跳过",
                "音频抽检": "跳过",
                "抽检详情": "未启用深度抽检",
                "录像是否正常": None,  # 综合后填充
            })

        records.sort(key=lambda r: _to_int(r["通道"], default=1 << 30))

        # 深度抽检依赖 URI,若开启则强制做落盘检索
        need_search = self.check_disk_recording or self.deep_av_check

        # 并发 CMSearch 检查近期落盘
        # 落盘阶段进度区间：有深度抽检时 28%→70%，否则 28%→92%
        disk_lo = 0.28
        disk_hi = 0.70 if self.deep_av_check else 0.92
        if need_search and records:
            total_n = len(records)
            self._log(
                f"正在检查近 {self.lookback_minutes} 分钟是否有录像"
                f"({total_n} 通道, 并发 {self.search_workers})..."
            )
            self._progress(
                f"近期录像检查 0/{total_n}",
                current=0,
                total=total_n,
                phase="disk",
                overall=disk_lo,
            )
            results: Dict[str, Dict] = {}

            def _job(tid: str) -> Tuple[str, Dict]:
                return tid, self._search_track_recent(tid, self.lookback_minutes)

            done_n = 0
            with ThreadPoolExecutor(max_workers=self.search_workers) as pool:
                futures = [pool.submit(_job, r["track_id"]) for r in records]
                for fut in as_completed(futures):
                    tid, res = fut.result()
                    results[tid] = res
                    done_n += 1
                    frac = done_n / total_n if total_n else 1.0
                    overall = disk_lo + (disk_hi - disk_lo) * frac
                    if (
                        done_n == 1
                        or done_n == total_n
                        or done_n % max(1, total_n // 25) == 0
                    ):
                        self._progress(
                            f"近期录像检查 {done_n}/{total_n}",
                            current=done_n,
                            total=total_n,
                            phase="disk",
                            overall=overall,
                        )

            for r in records:
                res = results.get(r["track_id"], {
                    "ok": False,
                    "status": "未知",
                    "detail": "未返回结果",
                })
                r["落盘状态"] = res.get("status", "未知")
                r["落盘详情"] = res.get("detail", "")
                r["playback_uri"] = res.get("playback_uri")
                r["seg_start"] = res.get("seg_start")
                r["seg_end"] = res.get("seg_end")
            self._progress(
                f"近期录像检查完成 {total_n}/{total_n}",
                current=total_n,
                total=total_n,
                phase="disk",
                overall=disk_hi,
            )
        else:
            for r in records:
                r["落盘状态"] = "跳过"
                r["落盘详情"] = "已跳过近期录像检查"
            self._progress(
                "已跳过近期录像检查",
                phase="disk",
                overall=disk_hi,
            )

        # 可选:短时 RTSP 音视频抽检
        if self.deep_av_check:
            self._run_deep_av_checks(records)

        # 综合「录像是否正常」
        for r in records:
            r["录像是否正常"] = self._judge_recording_ok(r)

        self._recording_cache = records
        return records

    @staticmethod
    def _judge_recording_ok(r: Dict) -> str:
        """综合计划 + 落盘 + 深度抽检,给出通道录像是否正常。

        返回: 正常 / 异常 / 未知 / 未配置 / 跳过
        跳过: 未做落盘检索时不判定录像是否正常。
        """
        if not r.get("已启用录像"):
            return "未配置"

        disk = r.get("落盘状态")
        # 未做落盘检查时，不给出录像正常/异常结论
        if disk == "跳过":
            return "跳过"
        if disk == "异常":
            return "异常"
        if disk not in ("正常",):
            return "未知"

        # 深度抽检:视频轨失败直接异常
        v = r.get("视频抽检")
        if v == "异常":
            return "异常"
        if v == "未知" and r.get("抽检详情"):
            # 工具缺失等
            if disk == "正常":
                return "正常"  # 落盘正常但抽检未能执行,不降为未知录像
            return "未知"

        return "正常"

    def get_health_summary(self) -> Dict:
        """获取设备健康状态汇总(复用已缓存数据)"""
        health = {
            "健康状态": "良好",
            "预警信息": [],
            "统计": {},
        }
        # 严重度排序:良好 < 警告 < 严重
        severity_rank = {"良好": 0, "警告": 1, "严重": 2}
        worst = "良好"

        def raise_to(level: str):
            nonlocal worst
            if severity_rank[level] > severity_rank[worst]:
                worst = level

        # 系统状态:内存
        status = self.get_system_status()
        if status.get("内存使用率"):
            mem_rate = _to_float(status["内存使用率"].replace("%", ""))
            if mem_rate > 90:
                raise_to("严重")
                health["预警信息"].append("内存使用率过高")
            elif mem_rate > 80:
                raise_to("警告")
                health["预警信息"].append("内存使用率偏高")

        # 硬盘状态
        drives = self.get_storage_status()
        bad_drives = [d for d in drives if d["状态"] not in ("ok", "sleep", "idle")]
        if bad_drives:
            raise_to("严重")
            health["预警信息"].append(f"{len(bad_drives)}块硬盘状态异常")

        full_drives = [d for d in drives if _to_float(d["使用率"].replace("%", "")) > 95]
        if full_drives:
            # 循环覆盖场景下满盘常见,降为警告;若同时有故障盘则已是严重
            raise_to("警告")
            health["预警信息"].append(
                f"{len(full_drives)}块硬盘空间已满/即将用尽(若开启循环覆盖仍可继续录像)"
            )

        sleeping_drives = [d for d in drives if d["状态"] in ["sleep", "idle"]]
        if sleeping_drives:
            health["预警信息"].append(f"{len(sleeping_drives)}块硬盘处于休眠状态")

        # 摄像头离线检查
        cameras = self.get_cameras()
        offline = [c for c in cameras if c.get("在线") == "false"]
        if offline:
            raise_to("严重")
            names = "、".join(c["名称"] for c in offline if c["名称"] != "未知")
            health["预警信息"].append(
                f"{len(offline)}个摄像头离线" + (f"({names})" if names else "")
            )

        # 录像:计划 / 音频 / 落盘
        records = self.get_recording_status()
        # 是否实际查询了落盘（未查则录像综合状态也为跳过，预警区不展示相关结论）
        disk_checked = bool(self.check_disk_recording or self.deep_av_check)
        stats = {
            "通道总数": len(records),
            "计划已配置": 0,
            "计划未配置": 0,
            "录像正常": 0,
            "录像异常": 0,
            "录像未知": 0,
            "录像跳过": 0,
            "含音频": 0,
            "不含音频": 0,
            "音频未知": 0,
            "落盘正常": 0,
            "落盘异常": 0,
            "落盘未知": 0,
            "落盘跳过": 0,
            "落盘已检查": disk_checked,
            "录像已检查": disk_checked,  # 录像正常与否依赖落盘检索
            "视频抽检正常": 0,
            "视频抽检异常": 0,
            "音频抽检正常": 0,
            "音频抽检异常": 0,
            "音频抽检警告": 0,
            "摄像头在线": sum(1 for c in cameras if c.get("在线") == "true"),
            "摄像头离线": len(offline),
            "摄像头总数": len(cameras),
            "深度抽检": self.deep_av_check,
        }

        if records:
            for r in records:
                if r["已启用录像"]:
                    stats["计划已配置"] += 1
                else:
                    stats["计划未配置"] += 1

                ok = r.get("录像是否正常")
                if ok == "跳过":
                    stats["录像跳过"] += 1
                elif ok == "正常":
                    stats["录像正常"] += 1
                elif ok in ("异常", "未配置"):
                    stats["录像异常"] += 1
                else:
                    stats["录像未知"] += 1

                sa = r.get("录像含音频")
                if sa is True:
                    stats["含音频"] += 1
                elif sa is False:
                    stats["不含音频"] += 1
                else:
                    stats["音频未知"] += 1

                disk = r.get("落盘状态")
                if disk == "跳过":
                    stats["落盘跳过"] += 1
                elif disk == "正常":
                    stats["落盘正常"] += 1
                elif disk == "异常":
                    stats["落盘异常"] += 1
                else:
                    stats["落盘未知"] += 1

                if r.get("视频抽检") == "正常":
                    stats["视频抽检正常"] += 1
                elif r.get("视频抽检") == "异常":
                    stats["视频抽检异常"] += 1
                if r.get("音频抽检") == "正常":
                    stats["音频抽检正常"] += 1
                elif r.get("音频抽检") == "异常":
                    stats["音频抽检异常"] += 1
                elif r.get("音频抽检") == "警告":
                    stats["音频抽检警告"] += 1

            # 计划/音频来自配置查询，快速模式仍可预警
            if stats["计划未配置"]:
                raise_to("严重")
                health["预警信息"].append(f"{stats['计划未配置']}个通道未配置录像计划")

            if stats["不含音频"]:
                raise_to("警告")
                health["预警信息"].append(f"{stats['不含音频']}个通道未开启录像音频(SaveAudio=false)")

            # 落盘 / 录像综合结论：仅在实际查询后写入预警
            if disk_checked:
                if stats["落盘异常"]:
                    raise_to("严重")
                    bad = [r for r in records if r.get("落盘状态") == "异常"]
                    sample = "、".join(
                        f"{r['通道']}"
                        + (f"({r['名称']})" if r.get("名称") and r["名称"] != "未知" else "")
                        for r in bad[:5]
                    )
                    more = f" 等{len(bad)}路" if len(bad) > 5 else f"({sample})" if sample else ""
                    if len(bad) <= 5 and sample:
                        health["预警信息"].append(
                            f"{stats['落盘异常']}个通道近期无录像: {sample}"
                        )
                    else:
                        health["预警信息"].append(
                            f"{stats['落盘异常']}个通道近期无录像{more}"
                        )

                if stats["落盘未知"]:
                    raise_to("警告")
                    health["预警信息"].append(
                        f"{stats['落盘未知']}个通道近期录像状态未知(检索失败)"
                    )

            if self.deep_av_check:
                if stats["视频抽检异常"]:
                    raise_to("严重")
                    health["预警信息"].append(
                        f"{stats['视频抽检异常']}个通道短时视频抽检异常"
                    )
                if stats["音频抽检异常"]:
                    raise_to("严重")
                    health["预警信息"].append(
                        f"{stats['音频抽检异常']}个通道短时音频抽检异常(无音轨)"
                    )
                if stats["音频抽检警告"]:
                    raise_to("警告")
                    health["预警信息"].append(
                        f"{stats['音频抽检警告']}个通道音频疑似静音/电平过低"
                    )

        health["统计"] = stats
        health["健康状态"] = worst
        return health


def print_status(nvr: HikvisionNVR, *, verbose: bool = False, device_name: str = ""):
    """采集并打印单台 NVR 状态（Rich 报告式布局）。"""
    from cli_report import collect_status, render_reports

    report = collect_status(nvr, device_name=device_name)
    if not report.get("info"):
        report["error"] = "无法获取设备信息，请检查 IP/账号/网络"
    render_reports([report], verbose=verbose)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="海康威视NVR/DVR状态查询工具")
    parser.add_argument("-i", "--ip", help="NVR/DVR的IP地址（单机直连时必填）")
    parser.add_argument("-p", "--port", type=int, default=80, help="端口号,默认80")
    parser.add_argument("-u", "--username", default="admin", help="用户名,默认admin")
    parser.add_argument("-w", "--password", help="密码（单机直连时必填）")
    parser.add_argument("-s", "--ssl", action="store_true", help="使用HTTPS")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="输出全部通道明细与设备详情（默认仅摘要+异常通道）",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=60,
        help="检查近期录像落盘的时间窗口(分钟),默认60",
    )
    parser.add_argument(
        "--no-search",
        action="store_true",
        help="跳过CMSearch落盘检查(仅查计划与音频配置,更快)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="落盘检查并发数,默认8",
    )
    parser.add_argument(
        "--deep-av-check",
        action="store_true",
        help="启用深度音视频抽检:短时RTSP拉流(默认关闭,不干扰日常录像)",
    )
    parser.add_argument(
        "--av-seconds",
        type=int,
        default=6,
        help=f"每路抽检时长(秒),默认6,最大{HikvisionNVR.AV_SECONDS_MAX}",
    )
    parser.add_argument(
        "--av-workers",
        type=int,
        default=2,
        help=f"抽检并发数,默认2,最大{HikvisionNVR.AV_WORKERS_MAX}(保护NVR)",
    )
    parser.add_argument(
        "--av-limit",
        type=int,
        default=None,
        help="最多抽检通道数(用于抽样);默认全部落盘正常通道",
    )
    parser.add_argument(
        "--silence-db",
        type=float,
        default=-80.0,
        help="音频 mean_volume 低于该值标为警告(dB),默认-80",
    )
    parser.add_argument(
        "--busy-start",
        type=int,
        default=10,
        help="深度抽检优先时段开始小时(本地时间,默认10)",
    )
    parser.add_argument(
        "--busy-end",
        type=int,
        default=18,
        help="深度抽检优先时段结束小时(本地时间,默认18)",
    )
    parser.add_argument(
        "--av-save",
        action="store_true",
        help="保存抽检视频片段到项目目录 av_samples/<当前时间>/ (会自动启用深度抽检)",
    )
    parser.add_argument(
        "--av-save-root",
        default=None,
        help="抽检片段保存根目录,默认: 项目目录/av_samples",
    )
    return parser


def nvr_from_args(args: argparse.Namespace, *, quiet: bool = False) -> HikvisionNVR:
    deep = bool(getattr(args, "deep_av_check", False) or getattr(args, "av_save", False))
    if deep and not quiet:
        tools = _which_tools()
        if not tools.get("ffmpeg") or not tools.get("ffprobe"):
            print(
                Colors.warning(
                    "警告: 未找到 ffmpeg/ffprobe,深度抽检将无法执行。"
                    "请安装: brew install ffmpeg"
                )
            )
    return HikvisionNVR(
        ip=args.ip,
        port=int(getattr(args, "port", None) or 80),
        username=getattr(args, "username", None) or "admin",
        password=getattr(args, "password", None) or "",
        use_ssl=bool(getattr(args, "ssl", False)),
        lookback_minutes=int(getattr(args, "lookback", None) or 60),
        check_disk_recording=not bool(getattr(args, "no_search", False)),
        search_workers=int(getattr(args, "workers", None) or 8),
        deep_av_check=deep,
        av_seconds=int(getattr(args, "av_seconds", None) or 6),
        av_workers=int(getattr(args, "av_workers", None) or 2),
        av_limit=getattr(args, "av_limit", None),
        silence_db=float(getattr(args, "silence_db", None) if getattr(args, "silence_db", None) is not None else -80.0),
        busy_start_hour=int(getattr(args, "busy_start", None) or 10),
        busy_end_hour=int(getattr(args, "busy_end", None) or 18),
        av_save=bool(getattr(args, "av_save", False)),
        av_save_root=getattr(args, "av_save_root", None),
        quiet=quiet,
    )


def main():
    parser = build_arg_parser()
    args = parser.parse_args()

    if not args.ip or not args.password:
        parser.error("单机模式需要 -i/--ip 与 -w/--password；多设备请使用 ./nvr")

    nvr = nvr_from_args(args)
    print_status(nvr, verbose=bool(args.verbose))


if __name__ == "__main__":
    main()

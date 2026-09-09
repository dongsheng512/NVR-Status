"""ISAPI 客户端：会话 / 请求 / 解析 / 缓存 / 取消 / 设备基础信息。

B2 拆分：原 HikvisionNVR 中与「连接与基础端点」相关的部分。
存储 / 录像 / 抽检 / 健康 为独立 mixin，组合成 HikvisionNVR。
"""

from __future__ import annotations

import os
import re
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests
from requests.auth import HTTPDigestAuth

from nvr_core.util import (
    Colors,
    ScanCancelled,
    _project_dir,
    _to_float,
    _to_int,
    _which_tools,
)


class ISAPIClient:
    """ISAPI 会话与设备基础信息。业务 mixin 以 self 组合使用。"""

    AV_SECONDS_MAX = 12
    AV_WORKERS_MAX = 3
    SEARCH_WORKERS_MAX = 16
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
        busy_days_ago: int = 0,
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
        # 落盘检查窗口最长 30 天
        self.lookback_minutes = max(1, min(int(lookback_minutes), 30 * 24 * 60))
        self.check_disk_recording = check_disk_recording
        self.search_workers = max(1, min(int(search_workers), self.SEARCH_WORKERS_MAX))
        # GUI 等场景: quiet 抑制终端输出; progress_callback(msg: str) 汇报进度
        self.quiet = quiet
        self.progress_callback = progress_callback
        # GUI 取消钩子: 通过 cancel() 置位, 循环处抛 ScanCancelled
        self._cancelled = threading.Event()
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
        # 0=今天, 1=昨天, … 指定抽检落在哪一天的繁忙时段
        self.busy_days_ago = max(0, min(30, int(busy_days_ago)))
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
        # NVR 常见自签证书:SSL 模式跳过校验(内网工具,风险见 README 安全说明),
        # 首次请求时会向用户输出一次性提示
        self.session.verify = not use_ssl
        self._ssl_notice_shown = False
        # 线程本地/注册表: close() 时统一回收连接池
        self._sessions_lock = threading.Lock()
        self._all_sessions: List[requests.Session] = [self.session]
        # 线程本地 Session,用于并发 CMSearch
        self._thread_local = threading.local()
        if use_ssl:
            # 告警抑制仅在确有 HTTPS 时做(进程级,尽量晚、尽量少)
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass

        # 端点缓存:全流程每个端点只请求一次
        self._cache: Dict[str, Optional[ET.Element]] = {}
        self._recording_cache: Optional[List[Dict]] = None
        self._cameras_cache: Optional[List[Dict]] = None
        self._tools = _which_tools()
        self._save_lock = threading.Lock()
        # 设备时区缓存(从设备时间串解析,默认东八区)
        self._device_tz: Optional[timezone] = None

    def close(self) -> None:
        """关闭全部会话(含线程本地),释放连接池。多设备队列逐台调用。"""
        with self._sessions_lock:
            sessions, self._all_sessions = self._all_sessions, []
        for sess in sessions:
            try:
                sess.close()
            except Exception:
                pass

    def _log(self, msg: str, force: bool = False) -> None:
        """进度/日志: quiet 时仅回调; 否则打印并回调。"""
        self._check_cancel()
        if self.progress_callback:
            try:
                self.progress_callback(msg)
            except ScanCancelled:
                raise
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
        self._check_cancel()
        if self.progress_callback:
            try:
                self.progress_callback(
                    msg,
                    current=current,
                    total=total,
                    phase=phase,
                    overall=overall,
                )
            except ScanCancelled:
                raise
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

    def cancel(self) -> None:
        """请求取消巡检:业务循环检查点将抛出 ScanCancelled。"""
        self._cancelled.set()

    def _check_cancel(self) -> None:
        """在业务循环/汇报处检查取消标志。"""
        if self._cancelled.is_set():
            raise ScanCancelled("巡检已取消")

    def _get_device_tz(self) -> timezone:
        """获取设备时区:优先解析设备当前时间中的偏移,否则用本机本地时区。

        仅在成功解析设备偏移时缓存;回退值不缓存,网络恢复后可重试,
        避免本机时区错误被固化为后续 RTSP 时间改写的基准。
        """
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
        # 回退:本机本地时区(通常与设备同在东八区),不缓存
        local = datetime.now().astimezone()
        return local.tzinfo if local.tzinfo else timezone(timedelta(hours=8))

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
            sess.verify = not self.use_ssl
            self._thread_local.session = sess
            with self._sessions_lock:
                self._all_sessions.append(sess)
        return sess

    def _get(self, endpoint: str, tag: str = "", quiet: bool = False) -> Optional[str]:
        """GET /ISAPI<endpoint>。

        返回: 正文文本; ""=HTTP 非 200(端点不支持/拒绝,重试无意义);
        None=瞬时网络失败(超时/连接错误,调用方不应负缓存)。
        """
        if self.use_ssl and not self._ssl_notice_shown:
            self._ssl_notice_shown = True
            self._log("⚠️ HTTPS 模式已跳过证书校验(自签证书),仅限可信内网使用", force=True)
        url = f"{self.base_url}/ISAPI{endpoint}"
        try:
            resp = self.session.get(url, timeout=10)
        except requests.exceptions.Timeout:
            self._warn_fail(tag or endpoint, "请求超时", quiet)
            return None
        except requests.exceptions.RequestException as e:
            self._warn_fail(tag or endpoint, str(e), quiet)
            return None
        if resp.status_code != 200:
            self._warn_fail(tag or endpoint, f"HTTP {resp.status_code}", quiet)
            return ""
        return resp.text

    def _warn_fail(self, tag: str, reason: str, quiet: bool) -> None:
        """端点失败提示: CLI 打印,GUI 经回调可见(quiet 探测除外)。"""
        if quiet:
            return
        self._log(f"⚠️ {tag}获取失败: {reason}", force=True)

    def _post(
        self,
        endpoint: str,
        data: str,
        tag: str = "",
        quiet: bool = False,
        timeout: int = 15,
        use_thread_session: bool = False,
    ) -> Tuple[int, str]:
        """POST /ISAPI<endpoint>,返回 (status_code, text)。失败码 -1。"""
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
            if not quiet:
                self._log(f"⚠️ {tag or endpoint}请求失败: 请求超时", force=True)
            return -1, ""
        except requests.exceptions.RequestException as e:
            if not quiet:
                self._log(f"⚠️ {tag or endpoint}请求失败: {e}", force=True)
            return -1, ""
        return resp.status_code, resp.text or ""

    _XML_MAX_BYTES = 8 * 1024 * 1024

    @staticmethod
    def _reject_dtd(text: str) -> bool:
        """设备 XML 中出现 DOCTYPE/ENTITY 时拒绝解析(防实体扩展攻击)。"""
        head = text[:4096].lower()
        return "<!doctype" in head or "<!entity" in head

    def _safe_fromstring(self, text: str, tag: str = "") -> Optional[ET.Element]:
        """解析设备返回的 XML:拒绝 DTD/实体、限制大小。"""
        if self._reject_dtd(text):
            if not self.quiet:
                print(f"  {Colors.error('⚠️ ' + (tag or 'XML') + '含 DOCTYPE/ENTITY, 已拒绝解析')}")
            return None
        if len(text.encode("utf-8", "ignore")) > self._XML_MAX_BYTES:
            if not self.quiet:
                print(f"  {Colors.error('⚠️ ' + (tag or 'XML') + '响应过大, 已拒绝解析')}")
            return None
        try:
            return ET.fromstring(text)
        except ET.ParseError:
            return None

    def _parse(self, endpoint: str, tag: str = "") -> Optional[ET.Element]:
        """请求并解析XML。带缓存:同一端点只请求一次。失败返回None。

        仅「确定性失败」入负缓存(HTTP 非200=端点不支持、解析失败);
        瞬时网络失败(超时等)不缓存,后续调用可重试。
        """
        if endpoint in self._cache:
            return self._cache[endpoint]
        text = self._get(endpoint, tag)
        if text is None:
            # 瞬时失败:不入缓存
            return None
        root: Optional[ET.Element]
        if not text:
            root = None
        else:
            # 移除命名空间以便于解析
            text = re.sub(r'\s+xmlns="[^"]+"', '', text)
            root = self._safe_fromstring(text, tag or endpoint)
        self._cache[endpoint] = root
        return root

    def _parse_endpoint_quiet(self, endpoint: str) -> Optional[ET.Element]:
        """静默解析端点（不打失败日志），用于能力探测。"""
        if endpoint in self._cache:
            return self._cache[endpoint]
        text = self._get(endpoint, tag="", quiet=True)
        if text is None:
            return None
        root: Optional[ET.Element]
        if not text:
            root = None
        else:
            text = re.sub(r'\s+xmlns="[^"]+"', "", text)
            root = self._safe_fromstring(text)
        self._cache[endpoint] = root
        return root

    # ---------- 设备基础信息 ----------

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
        """获取摄像头真实连接状态(带缓存:一次巡检内只请求一次)。

        优先使用 InputProxy 接口(可获取在线/离线、名称、IP、型号);
        设备不支持时回退到 Streaming channels(仅统计已配置通道数)。
        """
        if self._cameras_cache is not None:
            return self._cameras_cache
        cameras = self._get_input_proxy_cameras()
        if cameras is not None:
            self._cameras_cache = cameras
            return cameras
        self._cameras_cache = self._get_streaming_channels()
        return self._cameras_cache

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

"""CLI 与 GUI 共用的巡检编排。

B3：将「设备+选项 → HikvisionNVR」与「NVR → 统一结果 dict」两段编排
从 ui/scan_worker 与 cli_report/nvr 收敛到此处，消除逻辑分叉。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from nvr_core.nvr import HikvisionNVR
from nvr_core.util import Colors, ScanCancelled, _which_tools

# 进度回调约定: progress_callback(msg: str = "", *, current=None, total=None, phase="", overall=None)
ProgressCallback = Callable[..., None]


def _notify(progress_callback: Optional[ProgressCallback], quiet: bool, msg: str) -> None:
    """进度通知: 有回调走回调(带 init 相位); 否则 CLI 未静默时打印。"""
    if progress_callback is not None:
        try:
            progress_callback(msg, phase="init", overall=0.02)
        except ScanCancelled:
            raise
        except Exception:
            pass
    elif not quiet:
        print(Colors.warning(msg))


def build_nvr(
    device: Dict[str, Any],
    options: Optional[Dict[str, Any]] = None,
    *,
    quiet: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
    default_save_root: Optional[str] = None,
) -> HikvisionNVR:
    """按统一 schema 从设备/选项构造 HikvisionNVR。

    device: {ip, port, username, password, ssl, name?}
    options: 与配置档案 scan_options 同键
      lookback / no_search / workers / deep_av_check / av_seconds / av_workers /
      av_limit / silence_db / busy_start / busy_end / busy_days_ago / av_save / av_save_root
    default_save_root: 未显式指定 av_save_root 时的保存根目录（GUI 传 app data 目录）。
    """
    opt = options or {}

    # 深度抽检 / 保存片段：缺 ffmpeg 时自动降级
    want_deep = bool(opt.get("deep_av_check") or opt.get("av_save"))
    tools = _which_tools()
    has_ff = bool(tools.get("ffmpeg") and tools.get("ffprobe"))
    if want_deep and not has_ff:
        _notify(
            progress_callback,
            quiet,
            "未找到 ffmpeg/ffprobe，已跳过深度抽检，仅做状态与近期录像检查。"
            "请安装: brew install ffmpeg",
        )
        want_deep = False
    want_save = bool(opt.get("av_save")) and has_ff and want_deep

    lookback = int(opt.get("lookback") or 60)
    busy_days_ago = max(0, min(30, int(opt.get("busy_days_ago") or 0)))
    # 抽检指定历史日时，落盘检查窗口至少覆盖到该日
    if want_deep and busy_days_ago > 0:
        need = (busy_days_ago + 1) * 24 * 60
        if lookback < need:
            lookback = need
            _notify(
                progress_callback,
                quiet,
                f"已按抽检日自动扩大落盘检查窗口至近 {busy_days_ago + 1} 天",
            )

    av_limit = opt.get("av_limit") or 0
    save_root = (opt.get("av_save_root") or "").strip() or default_save_root
    return HikvisionNVR(
        ip=device["ip"],
        port=int(device.get("port") or 80),
        username=device.get("username") or "admin",
        password=device.get("password") or "",
        use_ssl=bool(device.get("ssl")),
        lookback_minutes=lookback,
        check_disk_recording=not bool(opt.get("no_search")),
        search_workers=int(opt.get("workers") or 8),
        deep_av_check=want_deep,
        av_seconds=int(opt.get("av_seconds") or 6),
        av_workers=int(opt.get("av_workers") or 2),
        av_limit=int(av_limit) if av_limit and int(av_limit) > 0 else None,
        silence_db=float(opt.get("silence_db") or -80),
        busy_start_hour=int(opt.get("busy_start") or 10),
        busy_end_hour=int(opt.get("busy_end") or 18),
        busy_days_ago=busy_days_ago,
        av_save=want_save,
        av_save_root=save_root,
        quiet=quiet,
        progress_callback=progress_callback,
    )


def run_nvr(
    nvr: HikvisionNVR,
    *,
    device_name: str = "",
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """在 NVR 上执行完整巡检编排，返回统一结果 dict。

    结果键（CLI 与 GUI 共用）：
      device_name / ip / info / sys_status / health / alarms / cameras / records /
      drives / disk_overwrite / lookback_minutes / deep_av_check / deep_av(别名) /
      av_seconds / av_workers / busy_start_hour / busy_end_hour / av_save /
      av_save_dir / check_disk_recording / error
    """

    def progress(msg: str = "", **kw: Any) -> None:
        if progress_callback is not None:
            try:
                progress_callback(msg, **kw)
            except ScanCancelled:
                raise
            except Exception:
                pass

    progress(f"连接 {device_name or nvr.ip}...", phase="connect", overall=0.03)
    info = nvr.get_device_info()
    if not info:
        return _error_report(
            nvr,
            device_name=device_name or nvr.ip,
            error="无法获取设备信息，请检查 IP/账号/网络",
        )

    progress(
        f"设备: {info.get('型号', '')} 固件 {info.get('固件版本', '')}",
        phase="device",
        overall=0.10,
    )
    progress("读取系统状态…", phase="sys", overall=0.14)
    sys_status = nvr.get_system_status()
    progress("获取摄像头列表…", phase="cameras", overall=0.18)
    cameras = nvr.get_cameras()
    progress(
        f"摄像头 {len(cameras)} 路，开始录像/近期录像检查...",
        phase="recording",
        overall=0.26,
        current=0,
        total=max(len(cameras), 1),
    )
    records = nvr.get_recording_status()
    progress("汇总健康状态…", phase="health", overall=0.97)
    health = nvr.get_health_summary()
    progress("读取硬盘状态与循环覆盖…", phase="storage", overall=0.99)
    drives = nvr.get_storage_status()
    # get_health_summary 内已查询过循环覆盖；结果挂在 health["循环覆盖"]
    disk_overwrite = (health or {}).get("循环覆盖") or nvr.get_disk_overwrite_status(drives)
    progress("巡检完成", phase="done", overall=1.0)

    return {
        "device_name": device_name or (info.get("设备名称") or nvr.ip),
        "ip": nvr.ip,
        "info": info or {},
        "sys_status": sys_status or {},
        "health": health or {},
        "alarms": nvr.get_alarm_status(),
        "cameras": cameras or [],
        "records": records or [],
        "drives": drives or [],
        "disk_overwrite": disk_overwrite or {},
        "lookback_minutes": nvr.lookback_minutes,
        "deep_av_check": nvr.deep_av_check,
        "deep_av": nvr.deep_av_check,
        "av_seconds": nvr.av_seconds,
        "av_workers": nvr.av_workers,
        "busy_start_hour": nvr.busy_start_hour,
        "busy_end_hour": nvr.busy_end_hour,
        "av_save": nvr.av_save,
        "av_save_dir": nvr.av_save_dir,
        "check_disk_recording": nvr.check_disk_recording,
        "error": None,
    }


def _error_report(nvr: HikvisionNVR, *, device_name: str, error: str) -> Dict[str, Any]:
    return {
        "device_name": device_name or nvr.ip,
        "ip": nvr.ip,
        "info": {},
        "sys_status": {},
        "health": {},
        "alarms": [],
        "cameras": [],
        "records": [],
        "drives": [],
        "disk_overwrite": {},
        "lookback_minutes": nvr.lookback_minutes,
        "deep_av_check": nvr.deep_av_check,
        "deep_av": nvr.deep_av_check,
        "av_seconds": nvr.av_seconds,
        "av_workers": nvr.av_workers,
        "busy_start_hour": nvr.busy_start_hour,
        "busy_end_hour": nvr.busy_end_hour,
        "av_save": nvr.av_save,
        "av_save_dir": nvr.av_save_dir,
        "check_disk_recording": nvr.check_disk_recording,
        "error": error,
    }


def scan(
    device: Dict[str, Any],
    options: Optional[Dict[str, Any]] = None,
    *,
    device_name: str = "",
    quiet: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
    default_save_root: Optional[str] = None,
) -> Dict[str, Any]:
    """构造 NVR 并执行巡检（build_nvr + run_nvr 的便捷入口）。"""
    nvr = build_nvr(
        device,
        options,
        quiet=quiet,
        progress_callback=progress_callback,
        default_save_root=default_save_root,
    )
    return run_nvr(nvr, device_name=device_name, progress_callback=progress_callback)


def scan_queue(
    devices: list,
    options: Optional[Dict[str, Any]] = None,
    *,
    quiet: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
    default_save_root: Optional[str] = None,
    on_nvr: Optional[Callable[[HikvisionNVR], None]] = None,
    on_device: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> list:
    """多设备队列巡检（B5）：顺序遍历，逐台 build_nvr + run_nvr。

    进度语义：每台设备内部 0~1 的 overall 映射到整体 [i/N, (i+1)/N)，
    因此 GUI 无需区分单机/队列，只要把回调接上即可。
    取消：progress_callback 抛出 ScanCancelled 会沿调用链中止队列。

    on_nvr: 每台 NVR 构建完成后回调（GUI 用于记录当前 NVR 以支持取消）。
    on_device: 每台巡检完成（含失败结果 dict）后回调，可用于逐台刷新 UI。
    返回：每台设备的统一结果 dict 列表（顺序与 devices 一致）。
    """

    devices = list(devices or [])
    total = max(len(devices), 1)
    results: list = []
    for i, dev in enumerate(devices):
        name = str(dev.get("name") or dev.get("ip") or f"设备{i + 1}")

        def _wrapped(msg: str = "", **kw: Any) -> None:
            if kw.get("overall") is not None:
                try:
                    kw["overall"] = min(1.0, (i + float(kw["overall"])) / total)
                except (TypeError, ValueError):
                    pass
            if progress_callback is not None:
                try:
                    progress_callback(msg, **kw)
                except ScanCancelled:
                    raise
                except Exception:
                    pass

        if progress_callback is not None:
            try:
                progress_callback(
                    f"—— [{i + 1}/{total}] {name} ——",
                    phase="init",
                    overall=i / total,
                )
            except ScanCancelled:
                raise
            except Exception:
                pass

        nvr = build_nvr(
            dev,
            options,
            quiet=quiet,
            progress_callback=_wrapped,
            default_save_root=default_save_root,
        )
        if on_nvr is not None:
            on_nvr(nvr)
        report = run_nvr(nvr, device_name=name, progress_callback=_wrapped)
        results.append(report)
        if on_device is not None:
            on_device(report)
    return results

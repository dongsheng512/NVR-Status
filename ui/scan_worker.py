"""后台巡检线程：复用 HikvisionNVR，仅通过 Qt Signal 回主线程。

信号语义对齐 v1 消息：log / progress / done / error。

A1 节流：深抽检期间业务回调可能高频触发（每通道/每段），
用 _SignalThrottle 按固定间隔合并 emit，避免大量排队信号压垮 UI 线程。
"""

from __future__ import annotations

import threading
import traceback
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from config_store import default_av_save_root
from hikvision_status import HikvisionNVR, ScanCancelled


class _SignalThrottle:
    """按固定间隔批量转发 log / progress，降低对 UI 线程的冲击。

    日志逐条缓存、进度只保留最新一帧；flush() 时一次性 emit。
    线程退出必须调用 close() 停止后台合并线程。
    """

    def __init__(
        self,
        emit_log: Callable[[str], None],
        emit_progress: Callable[[Dict[str, Any]], None],
        interval: float = 0.08,
    ):
        self._emit_log = emit_log
        self._emit_progress = emit_progress
        self._interval = interval
        self._lock = threading.Lock()
        self._logs: List[str] = []
        self._progress: Optional[Dict[str, Any]] = None
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="signal-throttle"
        )
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            self.flush()

    def add_log(self, msg: str) -> None:
        if not msg:
            return
        with self._lock:
            self._logs.append(msg)

    def add_progress(self, data: Dict[str, Any]) -> None:
        with self._lock:
            self._progress = data

    def flush(self) -> None:
        with self._lock:
            logs, prog = self._logs, self._progress
            self._logs = []
            self._progress = None
        for m in logs:
            try:
                self._emit_log(m)
            except Exception:
                pass
        if prog is not None:
            try:
                self._emit_progress(prog)
            except Exception:
                pass

    def close(self) -> None:
        self._stop.set()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=0.3)


class ScanWorker(QObject):
    """后台扫描器。通过 moveToThread 或普通线程 + emit 均可。"""

    log_line = Signal(str)
    progress_update = Signal(dict)
    scan_finished = Signal(dict)
    scan_failed = Signal(str)
    scan_cancelled = Signal()

    def __init__(self, device: Dict[str, Any], options: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.device = dict(device)
        self.options = dict(options or {})
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._nvr: Optional[HikvisionNVR] = None
        self._throttle = _SignalThrottle(
            emit_log=self.log_line.emit,
            emit_progress=self.progress_update.emit,
        )

    def cancel(self) -> None:
        self._cancel.set()
        if self._nvr is not None:
            self._nvr.cancel()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def start(self) -> None:
        """在独立线程中执行 run，信号自动排到主线程。"""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        try:
            self._scan()
        except ScanCancelled:
            self._throttle.flush()
            self.scan_cancelled.emit()
        except Exception as e:
            self._throttle.flush()
            if self._cancel.is_set():
                self.scan_cancelled.emit()
            else:
                self.scan_failed.emit(traceback.format_exc())
        finally:
            self._throttle.close()

    def _progress(self, msg: str = "", **kw: Any) -> None:
        if self._cancel.is_set():
            raise ScanCancelled("巡检已取消")
        if msg:
            self._throttle.add_log(msg)
        if msg or kw:
            data = {
                "msg": msg or "",
                "current": kw.get("current"),
                "total": kw.get("total"),
                "phase": kw.get("phase") or "",
                "overall": kw.get("overall"),
            }
            self._throttle.add_progress(data)

    def _scan(self) -> None:
        from nvr_core.scan_runner import build_nvr, run_nvr

        opt = self.options
        save_root = (opt.get("av_save_root") or "").strip() or default_av_save_root()
        nvr = build_nvr(
            self.device,
            opt,
            quiet=True,
            progress_callback=self._progress,
            default_save_root=save_root,
        )
        self._nvr = nvr

        report = run_nvr(
            nvr,
            device_name=self.device.get("name") or self.device.get("ip"),
            progress_callback=self._progress,
        )
        if report.get("error"):
            self._throttle.flush()
            self.scan_failed.emit(report["error"])
            return

        self._throttle.flush()
        self.scan_finished.emit(report)


class QueueScanWorker(QObject):
    """多设备队列巡检（B5）：顺序遍历设备，逐台执行 scan_runner。

    进度复用 scan_runner.scan_queue 的整体映射（每台内部 0~1 → 整体 i/N）。
    信号：log_line / progress_update / device_finished(每台结果) /
          queue_finished(全部结果) / scan_failed / scan_cancelled。
    """

    log_line = Signal(str)
    progress_update = Signal(dict)
    device_finished = Signal(dict)
    queue_finished = Signal(list)
    scan_failed = Signal(str)
    scan_cancelled = Signal()

    def __init__(
        self,
        devices: List[Dict[str, Any]],
        options: Dict[str, Any],
        parent=None,
    ):
        super().__init__(parent)
        self.devices = [dict(d) for d in devices]
        self.options = dict(options or {})
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._nvr: Optional[HikvisionNVR] = None
        self._throttle = _SignalThrottle(
            emit_log=self.log_line.emit,
            emit_progress=self.progress_update.emit,
        )

    def cancel(self) -> None:
        self._cancel.set()
        if self._nvr is not None:
            self._nvr.cancel()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        from nvr_core.scan_runner import scan_queue

        try:
            save_root = (
                self.options.get("av_save_root") or ""
            ).strip() or default_av_save_root()
            results = scan_queue(
                self.devices,
                self.options,
                quiet=True,
                progress_callback=self._progress,
                default_save_root=save_root,
                on_nvr=lambda nvr: setattr(self, "_nvr", nvr),
                on_device=lambda report: (
                    self._throttle.flush(),
                    self.device_finished.emit(report),
                ),
            )
            self._throttle.flush()
            self.queue_finished.emit(list(results))
        except ScanCancelled:
            self._throttle.flush()
            self.scan_cancelled.emit()
        except Exception as e:
            self._throttle.flush()
            if self._cancel.is_set():
                self.scan_cancelled.emit()
            else:
                self.scan_failed.emit(traceback.format_exc())
        finally:
            self._throttle.close()

    def _progress(self, msg: str = "", **kw: Any) -> None:
        if self._cancel.is_set():
            raise ScanCancelled("巡检已取消")
        if msg:
            self._throttle.add_log(msg)
        if msg or kw:
            data = {
                "msg": msg or "",
                "current": kw.get("current"),
                "total": kw.get("total"),
                "phase": kw.get("phase") or "",
                "overall": kw.get("overall"),
            }
            self._throttle.add_progress(data)

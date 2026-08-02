"""A4-5 取消巡检契约 + 信号节流批量器。"""

from __future__ import annotations

import time

import pytest

from hikvision_status import HikvisionNVR, ScanCancelled
from ui.scan_worker import ScanWorker, _SignalThrottle


def test_nvr_cancel_raises_scan_cancelled():
    nvr = HikvisionNVR(
        ip="127.0.0.1", port=80, username="a", password="b", quiet=True
    )
    assert not nvr._cancelled.is_set()
    nvr.cancel()
    assert nvr._cancelled.is_set()
    with pytest.raises(ScanCancelled):
        nvr._check_cancel()


def test_worker_cancel_progress_raises():
    w = ScanWorker({"ip": "127.0.0.1", "name": "n"}, {})
    try:
        w.cancel()
        assert w.cancelled
        with pytest.raises(ScanCancelled):
            w._progress("x")
    finally:
        w._throttle.close()


def _drain_events(qapp, seconds=0.5):
    """跨线程 emit 是队列投递，需主线程转事件循环才能送达。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def test_worker_cancel_before_start_emits_cancelled(qapp):
    """取消置位后启动：不得 emit scan_finished，只 emit scan_cancelled（无网络）。"""
    w = ScanWorker({"ip": "127.0.0.1", "name": "n"}, {"no_search": True})
    events = []
    w.scan_cancelled.connect(lambda: events.append("cancelled"))
    w.scan_finished.connect(lambda _d: events.append("finished"))
    w.scan_failed.connect(lambda _e: events.append("failed"))
    w.cancel()
    w.start()
    w.join(timeout=10)
    w._throttle.close()
    _drain_events(qapp)
    assert events == ["cancelled"]


def test_signal_throttle_batches_and_keeps_latest_progress():
    logs, progs = [], []
    t = _SignalThrottle(logs.append, progs.append, interval=0.03)
    try:
        for i in range(5):
            t.add_log(f"m{i}")
            t.add_progress({"i": i})
        t.flush()
        assert logs == ["m0", "m1", "m2", "m3", "m4"]  # 日志逐条保留
        assert progs == [{"i": 4}]                       # 进度只保留最新一帧
        # 合并线程随后周期 flush，close 安全退出
    finally:
        t.close()
    assert not t._thread.is_alive()

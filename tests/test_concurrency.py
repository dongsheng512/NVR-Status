"""并发正确性：端点缓存单飞、设备时区只探测一次。

CMSearch / 深度抽检的线程池会并发触达 _parse（经 _get_device_tz），
而 requests.Session 非线程安全、"查缓存→请求→写缓存" 亦非原子。
这些用例锁定修复后的行为：同一端点只发一次请求。
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta

from hikvision_status import HikvisionNVR

_WORKERS = 8


def _nvr() -> HikvisionNVR:
    return HikvisionNVR(
        ip="192.168.1.64",
        port=80,
        username="admin",
        password="x",
        quiet=True,
    )


def _run_concurrently(fn, n: int = _WORKERS):
    """barrier 同步起跑，尽量让所有线程同时进入，放大竞态窗口。"""
    out = []
    out_lock = threading.Lock()
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()
        r = fn()
        with out_lock:
            out.append(r)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


def test_parse_requests_endpoint_once_under_concurrency(monkeypatch):
    """8 线程同时 _parse 同一端点，底层只允许发一次请求。"""
    n = _nvr()
    calls = []
    calls_lock = threading.Lock()

    def fake_get(endpoint, tag="", quiet=False):
        with calls_lock:
            calls.append(endpoint)
        time.sleep(0.05)
        return (
            "<ResponseStatus><statusCode>1</statusCode>"
            "<statusString>OK</statusString></ResponseStatus>"
        )

    monkeypatch.setattr(n, "_get", fake_get)
    results = _run_concurrently(lambda: n._parse("/System/status", "系统状态"))

    assert calls == ["/System/status"], "同一端点被重复请求"
    assert len(results) == _WORKERS
    assert all(r is not None for r in results)
    assert all((r.findtext("statusString") or "") == "OK" for r in results)


def test_parse_endpoint_quiet_single_flight(monkeypatch):
    """能力探测走 _parse_endpoint_quiet，同样只发一次请求。"""
    n = _nvr()
    calls = []

    def fake_get(endpoint, tag="", quiet=False):
        calls.append(endpoint)
        time.sleep(0.05)
        return "<ResponseStatus><statusCode>1</statusCode></ResponseStatus>"

    monkeypatch.setattr(n, "_get", fake_get)
    _run_concurrently(lambda: n._parse_endpoint_quiet("/ContentMgmt/Storage/quota"))

    assert calls == ["/ContentMgmt/Storage/quota"]


def test_device_tz_probed_once_under_concurrency(monkeypatch):
    """时区探测只触发一次设备状态查询，且并发结果一致为 +08:00。"""
    n = _nvr()
    probes = []
    probes_lock = threading.Lock()

    def fake_status():
        with probes_lock:
            probes.append(1)
        time.sleep(0.05)
        return {"当前时间": "2026-09-10T17:00:00+08:00"}

    monkeypatch.setattr(n, "get_system_status", fake_status)
    tzs = _run_concurrently(n._get_device_tz)

    assert len(probes) == 1, "设备时区被重复探测"
    assert len(tzs) == _WORKERS
    assert all(tz.utcoffset(None) == timedelta(hours=8) for tz in tzs)


def test_device_tz_fallback_not_cached(monkeypatch):
    """探测不到偏移时回退本机时区，且不缓存（后续仍可重试）。"""
    n = _nvr()
    probes = []

    def fake_status():
        probes.append(1)
        return {"当前时间": "2026-09-10 17:00:00"}  # 无 ±HH:MM 偏移

    monkeypatch.setattr(n, "get_system_status", fake_status)
    first = n._get_device_tz()
    second = n._get_device_tz()

    assert first is not None and second is not None
    assert len(probes) == 2, "回退值不应被缓存"
    assert n._device_tz is None

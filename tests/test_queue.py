"""B5-1 多设备队列 scan_queue：整体进度映射、逐台回调、失败继续、取消中止。"""

from __future__ import annotations

import pytest

from hikvision_status import ScanCancelled
from nvr_core import scan_runner

DEV_A = {"name": "A", "ip": "10.0.0.1"}
DEV_B = {"name": "B", "ip": "10.0.0.2"}


def _fake_run_report(name: str, error: str = "") -> dict:
    return {
        "device_name": name,
        "ip": "x",
        "info": {},
        "sys_status": {},
        "health": {"健康状态": "良好", "统计": {}},
        "alarms": [],
        "cameras": [],
        "records": [],
        "drives": [],
        "disk_overwrite": {},
        "error": error,
    }


@pytest.fixture
def fake_runner(monkeypatch):
    """把 build_nvr/run_nvr 换成记录调用的桩，避免触碰网络。"""

    calls = {"nvr": [], "report": []}

    def fake_build(device, options=None, **kw):
        nvr = object()
        calls["nvr"].append((device["name"], options))
        if kw.get("on_nvr"):  # scan_queue 内部不使用 on_nvr
            kw["on_nvr"](nvr)
        return nvr

    def fake_run(nvr, *, device_name="", progress_callback=None, **kw):
        if progress_callback is not None:
            progress_callback("done", phase="done", overall=1.0)
        calls["report"].append(device_name)
        return _fake_run_report(device_name)

    monkeypatch.setattr(scan_runner, "build_nvr", fake_build)
    monkeypatch.setattr(scan_runner, "run_nvr", fake_run)
    return calls


def test_scan_queue_sequential_all_devices(fake_runner):
    seen = []
    results = scan_runner.scan_queue(
        [DEV_A, DEV_B],
        {"lookback": 60},
        on_device=lambda r: seen.append(r["device_name"]),
    )
    assert [r["device_name"] for r in results] == ["A", "B"]
    # 逐台回调顺序一致
    assert seen == ["A", "B"]


def test_scan_queue_overall_maps_to_global(fake_runner):
    frames = []
    scan_runner.scan_queue(
        [DEV_A, DEV_B],
        {},
        progress_callback=lambda msg, **kw: frames.append(kw.get("overall")),
    )
    # 第 1 台内部 1.0 → 0.5，第 2 台内部 1.0 → 1.0
    assert frames == [0.0, 0.5, 0.5, 1.0]


def test_scan_queue_failure_continues(monkeypatch):
    def fake_run(nvr, *, device_name="", progress_callback=None, **kw):
        if progress_callback is not None:
            progress_callback("done", phase="done", overall=1.0)
        return _fake_run_report(device_name, error="连接失败" if device_name == "A" else "")

    monkeypatch.setattr(scan_runner, "build_nvr", lambda *a, **k: object())
    monkeypatch.setattr(scan_runner, "run_nvr", fake_run)
    results = scan_runner.scan_queue([DEV_A, DEV_B], {})
    assert len(results) == 2
    assert results[0]["error"]
    assert not results[1]["error"]


def test_scan_queue_cancel_aborts(fake_runner):
    def boom(msg="", **kw):
        raise ScanCancelled("取消")

    with pytest.raises(ScanCancelled):
        scan_runner.scan_queue([DEV_A, DEV_B], {}, progress_callback=boom)

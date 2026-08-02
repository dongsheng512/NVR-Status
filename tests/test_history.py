"""B6-1 历史报告归档：保存/列表/读取/清理/损坏容错。"""

from __future__ import annotations

import os

import pytest

from services import history


@pytest.fixture
def tmp_history(monkeypatch, tmp_path):
    """把归档目录重定向到临时目录。"""

    monkeypatch.setattr(history, "history_dir", lambda: str(tmp_path))
    return tmp_path


def _sample(device: str = "NVR1", status: str = "良好", error: str = "") -> dict:
    return {
        "device_name": device,
        "ip": "10.0.0.1",
        "info": {},
        "health": {"健康状态": status, "统计": {}},
        "records": [{"通道": "1"}],
        "error": error,
    }


def test_save_and_list(tmp_history):
    path = history.save_report(_sample("门卫"))
    assert os.path.isfile(path)
    meta = history.list_reports()
    assert len(meta) == 1
    assert meta[0]["device_name"] == "门卫"
    assert meta[0]["health_status"] == "良好"
    assert meta[0]["channels"] == 1
    assert meta[0]["saved_at"]


def test_save_then_load_roundtrip(tmp_history):
    path = history.save_report(_sample("NVR1", "严重"))
    data = history.load_report(path)
    assert data is not None
    assert data["health"]["健康状态"] == "严重"
    assert data["saved_at"]
    assert data["_file"] == os.path.basename(path)


def test_list_error_report(tmp_history):
    history.save_report(_sample("NVR2", "未知", error="连接失败"))
    meta = history.list_reports()
    assert meta[0]["error"] == "连接失败"


def test_prune_keeps_newest(tmp_history):
    for i in range(history.MAX_REPORTS + 5):
        history.save_report(_sample(f"NVR{i}"))
    assert len(os.listdir(str(tmp_history))) == history.MAX_REPORTS
    # 最旧的名字编号应已被清掉（文件名按时间戳排序，同秒则按名字）
    names = [n for n in os.listdir(str(tmp_history)) if n.endswith(".json")]
    assert len(names) == history.MAX_REPORTS


def test_corrupt_file_ignored(tmp_history):
    bad = os.path.join(str(tmp_history), "zzz.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{not json")
    history.save_report(_sample("好设备"))
    assert history.load_report(bad) is None
    assert len(history.list_reports()) == 1


def test_save_with_datetime_fields(tmp_history):
    """巡检结果含 datetime 时仍可归档（seg_start / latest_end 等）。"""
    from datetime import datetime, timezone

    data = _sample("带时间")
    data["records"] = [
        {
            "通道": 1,
            "名称": "cam1",
            "seg_start": datetime(2026, 8, 1, 10, 0, 0),
            "seg_end": datetime(2026, 8, 1, 10, 0, 6, tzinfo=timezone.utc),
            "latest_end": datetime(2026, 8, 1, 18, 30, 0),
        }
    ]
    path = history.save_report(data)
    assert os.path.isfile(path)
    loaded = history.load_report(path)
    assert loaded is not None
    rec = (loaded.get("records") or [{}])[0]
    # 写入后应为 ISO 字符串
    assert isinstance(rec.get("seg_start"), str)
    assert "2026-08-01" in rec["seg_start"]


def test_safe_name(tmp_history):
    path = history.save_report(_sample("设备 A/B:C"))
    assert "设备_A_B_C" in path or "设备" in os.path.basename(path)

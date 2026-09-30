"""CLI 报告要能看出实际抽检时段（--av-at 的落点/前移）。"""

from __future__ import annotations

import io
from typing import Any, Dict

from rich.console import Console

import cli_report


def _report() -> Dict[str, Any]:
    return {
        "device_name": "测试NVR",
        "ip": "10.0.0.8",
        "info": {"型号": "DS-7608", "固件版本": "V4"},
        "sys_status": {},
        "health": {"健康状态": "良好", "统计": {}, "预警信息": []},
        "alarms": [],
        "cameras": [{"id": "31", "IP": "10.0.0.31"}],
        "drives": [],
        "disk_overwrite": {},
        "records": [
            {
                "通道": "31",
                "名称": "前门",
                "在线": "true",
                "录像含音频": True,
                "录像是否正常": "正常",
                "落盘状态": "正常",
                "已启用录像": True,
                "视频抽检": "正常",
                "音频抽检": "正常",
                "抽检详情": "",
                "抽检时段": "定点抽检 09-11 16:20:00(抽检点 09-11 16:20:00)",
            }
        ],
        "deep_av": True,
        "error": None,
    }


def _render(monkeypatch, report: Dict[str, Any], *, verbose: bool = False) -> str:
    buf = io.StringIO()
    monkeypatch.setattr(
        cli_report, "console", Console(file=buf, width=140, no_color=True)
    )
    cli_report.render_device_report(report, verbose=verbose)
    return buf.getvalue()


def test_report_shows_probe_instant_even_when_all_ok(monkeypatch):
    """全部正常（非 verbose 只打一行结论）时也必须给出抽检时段。"""
    out = _render(monkeypatch, _report())
    assert "抽检时段" in out
    assert "定点抽检 09-11 16:20:00" in out


def test_report_shows_shifted_note(monkeypatch):
    rep = _report()
    rep["records"][0]["抽检时段"] = (
        "定点抽检 09-11 13:40:00(距现在不足10分钟,已前移至 09-11 13:35:00)"
    )
    out = _render(monkeypatch, rep, verbose=True)
    assert "已前移至" in out

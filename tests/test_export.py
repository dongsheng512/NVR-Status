"""A4-1 导出 CSV/TXT 最小回归。"""

from __future__ import annotations

import csv
import os

from services import export_report


def _sample_data() -> dict:
    return {
        "device_name": "NVR-1",
        "info": {"型号": "DS-7616", "固件版本": "V5.5.71"},
        "health": {
            "健康状态": "良好",
            "统计": {
                "摄像头总数": 2,
                "摄像头在线": 2,
                "摄像头离线": 0,
                "录像正常": 2,
                "录像异常": 0,
                "含音频": 2,
                "落盘正常": 2,
                "落盘异常": 0,
            },
            "预警信息": [],
        },
        "records": [
            {
                "通道": 1,
                "名称": "大门",
                "在线": "true",
                "录像含音频": True,
                "落盘状态": "正常",
                "落盘详情": "近 60 分钟有录像",
                "录像是否正常": "正常",
            },
            {
                "通道": 2,
                "名称": "车库",
                "在线": "false",
                "录像含音频": False,
                "落盘状态": "跳过",
                "落盘详情": "未检查",
                "录像是否正常": "未知",
            },
        ],
        "deep_av": False,
        "disk_overwrite": {"label": "已开启", "detail": "ISAPI ...", "source": "/ContentMgmt/storage"},
        "drives": [{"盘符": "C", "状态": "ok", "使用率": "42%", "已用空间TB": 1, "容量TB": 3, "剩余空间TB": 2}],
    }


def test_export_csv_fields_and_utf8_sig(tmp_path):
    path = os.path.join(tmp_path, "r.csv")
    export_report.export_csv(path, _sample_data())
    with open(path, "rb") as f:
        raw = f.read()
    assert raw.startswith(b"\xef\xbb\xbf")  # utf-8-sig BOM，Excel 友好
    rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
    header = ["通道", "名称", "在线", "含音频", "近期录像", "录像"]
    assert any(r[:6] == header for r in rows)
    assert rows[0][0] == "# NVR 巡检报告"
    assert ["近期有录像", "2"] in rows  # 统计项键名映射


def test_export_csv_empty_records(tmp_path):
    data = _sample_data()
    data["records"] = []
    path = os.path.join(tmp_path, "empty.csv")
    export_report.export_csv(path, data)
    with open(path, "rb") as f:
        assert f.read().startswith(b"\xef\xbb\xbf")
    # 空结果不抛异常，表头仍存在


def test_export_txt_contains_rows(tmp_path):
    path = os.path.join(tmp_path, "r.txt")
    export_report.export_txt(path, _sample_data(), "健康：良好")
    text = open(path, encoding="utf-8").read()
    assert "健康：良好" in text
    assert "大门" in text
    assert "\t".join(["1", "大门", "是", "是", "正常", "正常"]) in text

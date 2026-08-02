"""A4-3 循环覆盖解析（ISAPI XML 布尔 / 策略 / 推断分支）。"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from hikvision_status import HikvisionNVR


def _nvr():
    """不触发 __init__（避免 Session/时区探测），仅用解析方法。"""
    return HikvisionNVR.__new__(HikvisionNVR)


def test_parse_boolish():
    n = _nvr()
    assert n._parse_boolish("true") is True
    assert n._parse_boolish("false") is False
    assert n._parse_boolish("enable") is True
    assert n._parse_boolish("off") is False
    assert n._parse_boolish("garbage") is None


def test_parse_overwrite_strategy():
    n = _nvr()
    assert n._parse_overwrite_strategy("覆盖") is True
    assert n._parse_overwrite_strategy("不覆盖") is False
    assert n._parse_overwrite_strategy("循环") is True
    assert n._parse_overwrite_strategy("停止录像") is False
    assert n._parse_overwrite_strategy("overwrite") is True
    assert n._parse_overwrite_strategy("stop") is False
    assert n._parse_overwrite_strategy("") is None


def test_xml_scan_overwrite_bool_tag():
    root = ET.fromstring(
        "<Storage><enableOverWrite>true</enableOverWrite></Storage>"
    )
    enabled, tag, raw = _nvr()._xml_scan_overwrite(root)
    assert (enabled, tag, raw) == (True, "enableOverWrite", "true")


def test_xml_scan_overwrite_strategy_tag():
    root = ET.fromstring(
        '<Record><hddFullStrategy>停止录像</hddFullStrategy></Record>'
    )
    enabled, tag, raw = _nvr()._xml_scan_overwrite(root)
    assert (enabled, raw) == (False, "停止录像")


def test_xml_scan_overwrite_none_on_empty():
    assert _nvr()._xml_scan_overwrite(ET.fromstring("<a/>")) is None
    assert _nvr()._xml_scan_overwrite(None) is None


def test_disk_overwrite_infer_full_ok():
    """使用率≥95% 且状态 ok → 推断已开启。"""
    n = _nvr()
    # 绕过网络：缓存清空，所有端点探测返回 None，走「满盘推断」分支
    n._cache = {}
    n._parse = lambda *a, **k: None
    n._parse_endpoint_quiet = lambda ep: None
    drives = [{"盘符": "C", "使用率": "98%", "状态": "ok"}]
    res = n.get_disk_overwrite_status(drives)
    assert res["enabled"] is True
    assert res["source"] == "infer"
    assert res["label"] == "推断已开启"


def test_disk_overwrite_infer_full_bad():
    n = _nvr()
    n._cache = {}
    n._parse = lambda *a, **k: None
    n._parse_endpoint_quiet = lambda ep: None
    drives = [{"盘符": "C", "使用率": "99%", "状态": "error"}]
    res = n.get_disk_overwrite_status(drives)
    assert res["enabled"] is False
    assert res["label"] == "推断未开启"


def test_disk_overwrite_unknown_without_full_disks():
    n = _nvr()
    n._cache = {}
    n._parse = lambda *a, **k: None
    n._parse_endpoint_quiet = lambda ep: None
    drives = [{"盘符": "C", "使用率": "42%", "状态": "ok"}]
    res = n.get_disk_overwrite_status(drives)
    assert res["enabled"] is None
    assert res["label"] == "未知"

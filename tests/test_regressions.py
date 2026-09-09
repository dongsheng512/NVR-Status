"""2026-09 代码审查修复的回归测试。

覆盖: 主题深色判断、日志裁剪、CSV 公式注入、配置损坏留档、
原子写入、瞬时失败不缓存、RTSP 凭据掩码、0 值配置。
"""

from __future__ import annotations

import json
import os
from datetime import timedelta, timezone

from config_store import ConfigStore
from nvr_core.av_probe import AVProbeMixin
from nvr_core.isapi_client import ISAPIClient
from nvr_core.scan_runner import build_nvr
from nvr_core.util import _parse_hik_time
from services import export_report


def _listdir_prefix(tmp_path, prefix: str):
    return [fn for fn in os.listdir(tmp_path) if fn.startswith(prefix)]


# ---------- 主题 ----------

def test_theme_light_dark_mapping(qapp):
    from ui import theme

    theme.apply_theme(qapp, theme.ThemeMode.LIGHT)
    assert theme.effective_dark() is False
    assert theme.canvas_hex() == theme.CANVAS_BG["light"]
    theme.apply_theme(qapp, theme.ThemeMode.DARK)
    assert theme.effective_dark() is True
    assert theme.canvas_hex() == theme.CANVAS_BG["dark"]


# ---------- 日志裁剪 ----------

def test_log_trim_keeps_lines_separate(qapp):
    from ui.panels.log_panel import LogPanel

    panel = LogPanel()
    for i in range(200):
        panel.log(f"line-{i}", level="info")
    text = panel.log_box.toPlainText()
    lines = [l for l in text.split("\n") if l.strip()]
    # 超限后新日志不得拼进上一行
    assert lines[-1].endswith("line-199")
    assert all(len(l) < 200 for l in lines[-5:])


# ---------- CSV 公式注入 ----------

def test_csv_formula_injection_escaped(tmp_path):
    data = {
        "device_name": "NVR",
        "deep_av": False,
        "health": {"健康状态": "良好", "预警信息": [], "统计": {}},
        "records": [
            {"通道": "1", "名称": '=HYPERLINK("http://evil","x")', "落盘详情": "=cmd|..."},
        ],
    }
    path = tmp_path / "r.csv"
    export_report.export_csv(str(path), data)
    raw = path.read_text(encoding="utf-8-sig")
    lines = [l for l in raw.splitlines() if "=HYPERLINK" in l or "=cmd" in l]
    assert lines, "注入样例应出现在文件中"
    for l in lines:
        assert "'=HYPERLINK" in l or "'=cmd" in l


# ---------- 配置存储 ----------

def _store(tmp_path):
    return ConfigStore(str(tmp_path / "profiles.json"))


def test_save_leaves_no_tmp_and_roundtrips(tmp_path):
    store = _store(tmp_path)
    prof = store.get_profile()
    prof["devices"][0]["ip"] = "10.0.0.9"
    store.update_profile(None, prof)
    leftovers = _listdir_prefix(tmp_path, "profiles.json")
    assert not [fn for fn in leftovers if "tmp" in fn]
    again = ConfigStore(str(tmp_path / "profiles.json"))
    assert again.get_profile()["devices"][0]["ip"] == "10.0.0.9"


def test_corrupt_json_backed_up_not_silently_lost(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = ConfigStore(str(path))
    # 重置为默认,但坏文件必须留档
    backups = _listdir_prefix(tmp_path, "profiles.json.corrupt-")
    assert backups, "损坏文件应留档"
    assert store.get_active_name() == "默认"


def test_transient_read_error_keeps_original_file(tmp_path, monkeypatch):
    path = tmp_path / "profiles.json"
    good = _store(tmp_path)
    good.update_profile(None, good.get_profile())
    original = json.loads(path.read_text(encoding="utf-8"))

    # 模拟读取被占用(OSError): 不覆盖、不留档、不丢失
    import config_store as cs

    def locked_load(fp, *a, **kw):
        raise PermissionError("locked")

    monkeypatch.setattr(cs.json, "load", locked_load)
    store = ConfigStore(str(path))
    assert not _listdir_prefix(tmp_path, "profiles.json.corrupt-")
    monkeypatch.undo()
    # 文件内容未被破坏
    assert json.loads(path.read_text(encoding="utf-8")) == original


# ---------- ISAPI 缓存 / 工具 ----------

def _client():
    return ISAPIClient(ip="1.2.3.4", password="x")


def test_transient_failure_not_negative_cached(monkeypatch):
    c = _client()
    calls = {"n": 0}

    def fake_get(endpoint, tag="", quiet=False):
        calls["n"] += 1
        return None  # 瞬时网络失败

    monkeypatch.setattr(c, "_get", fake_get)
    assert c._parse("/System/status") is None
    assert c._parse("/System/status") is None
    assert calls["n"] == 2, "瞬时失败不应入负缓存"


def test_http_error_is_negative_cached(monkeypatch):
    c = _client()
    calls = {"n": 0}

    def fake_get(endpoint, tag="", quiet=False):
        calls["n"] += 1
        return ""  # HTTP 非 200,端点不支持

    monkeypatch.setattr(c, "_get", fake_get)
    assert c._parse("/Foo/Bar") is None
    assert c._parse("/Foo/Bar") is None
    assert calls["n"] == 1, "确定性失败应入负缓存"


def test_dtd_xml_rejected(monkeypatch):
    c = _client()

    def fake_get(endpoint, tag="", quiet=False):
        return '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe "x">]><a>&xxe;</a>'

    monkeypatch.setattr(c, "_get", fake_get)
    assert c._parse("/System/status") is None


def test_rtsp_credential_masking():
    plain = "rtsp://admin:Pass%401@1.2.3.4/ISAPI/x rtsp://admin:Pass@1.2.3.4:554/y"
    out = AVProbeMixin()._mask_credentials(plain)
    assert "admin:Pass" not in out
    assert "***:***@1.2.3.4" in out


# ---------- 时间解析 / 0 值配置 ----------

def test_parse_hik_time_naive_uses_device_tz():
    tz = timezone(timedelta(hours=8))
    dt = _parse_hik_time("2026-09-01T12:00:00", tz)
    assert dt is not None
    assert dt.utcoffset() == timedelta(0)
    assert dt.hour == 4  # 12:00+08 → 04:00Z


def test_build_nvr_zero_values_not_swallowed():
    nvr = build_nvr(
        {"ip": "1.2.3.4"},
        {"silence_db": 0, "busy_start": 0, "busy_end": 18},
        quiet=True,
    )
    assert nvr.silence_db == 0.0
    assert nvr.busy_start_hour == 0
    nvr.close()


def test_search_workers_clamped():
    nvr = build_nvr({"ip": "1.2.3.4"}, {"workers": 100}, quiet=True)
    assert nvr.search_workers <= ISAPIClient.SEARCH_WORKERS_MAX
    nvr.close()

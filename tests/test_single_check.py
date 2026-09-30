"""单路深度抽检（结果表选中通道 → 单独重跑音视频抽检）。

覆盖两层：
1. `scan_runner.run_single_av_check`：选项强制（deep / 锁定通道 / 不落盘 /
   不限路数）、记录副本语义（原记录不动）、异常路径；
2. 通道表交互：模型按身份回写字段、按钮随选择/运行状态启停。
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from nvr_core import scan_runner
from nvr_core.scan_runner import run_single_av_check


# ------------------------------------------------- run_single_av_check


class _StubNVR:
    """记录收到的选项；_run_deep_av_checks 只改抽检字段。"""

    def __init__(self, capture: Dict[str, Any]):
        self._capture = capture

    def _run_deep_av_checks(self, recs: List[Dict[str, Any]]) -> None:
        self._capture["checked_channels"] = [
            str(r.get("通道")) for r in recs
        ]
        for r in recs:
            r["视频抽检"] = "正常"
            r["音频抽检"] = "正常"
            r["抽检详情"] = "单路复查"

    def close(self) -> None:
        self._capture["closed"] = True


def _rec(ch: str = "31", tid: str = "3101") -> Dict[str, Any]:
    return {
        "通道": ch,
        "track_id": tid,
        "名称": "前门",
        "已启用录像": True,
        "落盘状态": "正常",
        "视频抽检": "未知",
        "音频抽检": "未知",
        "抽检详情": "拉流超时",
    }


def _patch_build(monkeypatch, capture: Dict[str, Any]) -> None:
    def fake_build(device, opt, **kw):
        capture["opt"] = dict(opt)
        return _StubNVR(capture)

    monkeypatch.setattr(scan_runner, "build_nvr", fake_build)


def test_single_check_forces_options_and_locks_channel(monkeypatch):
    capture: Dict[str, Any] = {}
    _patch_build(monkeypatch, capture)
    device = {"ip": "1.2.3.4", "password": "pw"}
    opts = {
        "deep_av_check": False,
        "av_save": True,
        "av_limit": 2,
        "av_channels": "64",
        "av_seconds": 6,
    }
    out = run_single_av_check([_rec()], device, opts)

    opt = capture["opt"]
    assert opt["deep_av_check"] is True, "单路抽检必须强制开深度"
    assert opt["av_channels"] == "31", "只查选中通道,不能沿用上次过滤"
    assert opt["av_limit"] is None, "单路复查不该被 av_limit 截断"
    assert opt["av_save"] is False, "单路复查不落盘,保持行为可预期"
    assert opt["av_seconds"] == 6, "其余选项按档案透传"
    assert capture["checked_channels"] == ["31"]
    assert capture["closed"] is True, "用完必须释放连接"
    assert out[0]["视频抽检"] == "正常"


def test_single_check_returns_copies_original_untouched(monkeypatch):
    capture: Dict[str, Any] = {}
    _patch_build(monkeypatch, capture)
    rec = _rec()
    original_snapshot = dict(rec)

    run_single_av_check([rec], {"ip": "x", "password": "y"}, {})

    assert rec == original_snapshot, "原记录是 UI 正在显示的数据,不能就地改"


def test_single_check_multiple_selections_share_one_connection(monkeypatch):
    capture: Dict[str, Any] = {}
    _patch_build(monkeypatch, capture)
    recs = [_rec("31", "3101"), _rec("64", "6401")]

    out = run_single_av_check(recs, {"ip": "x", "password": "y"}, {})

    assert capture["opt"]["av_channels"] == "31,64"
    assert capture["checked_channels"] == ["31", "64"]
    assert [r["通道"] for r in out] == ["31", "64"]


def test_single_check_empty_and_channelless_inputs():
    assert run_single_av_check([], {"ip": "x", "password": "y"}, {}) == []
    with pytest.raises(ValueError, match="通道号"):
        run_single_av_check([{"名称": "无通道号"}], {"ip": "x"}, {})


def test_single_check_propagates_connection_error(monkeypatch):
    def fake_build(device, opt, **kw):
        raise RuntimeError("连接失败")

    monkeypatch.setattr(scan_runner, "build_nvr", fake_build)
    with pytest.raises(RuntimeError, match="连接失败"):
        run_single_av_check([_rec()], {"ip": "x", "password": "y"}, {})


# ------------------------------------------------- 通道表交互


def _make_table(qapp):
    from ui.widgets.channel_table import ChannelTableView

    t = ChannelTableView()
    recs = [_rec("31", "3101"), _rec("64", "6401")]
    t.set_records(recs, True)
    t.set_result_actions_enabled(True)
    return t, recs


def test_model_updates_row_by_identity(qapp):
    t, recs = _make_table(qapp)
    changed = []
    t.model.dataChanged.connect(lambda tl, br, *a: changed.append(tl.row()))

    assert t.model.update_record_fields(recs[1], {"音频抽检": "正常"})
    assert recs[1]["音频抽检"] == "正常"
    assert changed == [1], "只刷新被更新的那一行"

    # 同内容但不同对象的 dict 不能误命中
    impostor = dict(recs[1])
    assert not t.model.update_record_fields(impostor, {"音频抽检": "异常"})
    assert recs[1]["音频抽检"] == "正常"


def test_single_check_button_follows_selection_and_state(qapp):
    t, recs = _make_table(qapp)

    # 有结果、未选择 → 禁用
    assert not t.btn_single_check.isEnabled()
    # 选中一行 → 启用
    t.view.selectRow(0)
    assert t.btn_single_check.isEnabled()
    # 运行中 → 禁用;结束后恢复
    t.set_single_check_running(True)
    assert not t.btn_single_check.isEnabled()
    t.set_single_check_running(False)
    assert t.btn_single_check.isEnabled()
    # 巡检中(结果动作禁用) → 禁用
    t.set_result_actions_enabled(False)
    assert not t.btn_single_check.isEnabled()


# ------------------------------------------------- 结果面板回写


def test_results_panel_applies_single_check_result(qapp):
    """渲染结果 → 回写单路抽检字段 → 行更新且切出抽检列。"""
    from ui.main_window import MainWindow

    w = MainWindow()
    try:
        recs = [
            dict(_rec("31", "3101"), 名称="前门"),
            dict(_rec("64", "6401"), 名称="后门"),
        ]
        report = {
            "device_name": "测试NVR",
            "ip": "10.0.0.8",
            "info": {},
            "health": {"健康状态": "良好", "统计": {}, "预警信息": []},
            "records": recs,
            "deep_av": True,
        }
        w.results_panel.render_result(report)
        assert w.results_panel.channel_table.model.deep() is True

        target = recs[0]
        updated = [
            dict(
                target,
                视频抽检="正常",
                音频抽检="正常",
                抽检详情="单路复查; 多时刻: 13:40=正常/正常",
            )
        ]
        w.results_panel.apply_single_check_result([target], updated)

        assert target["音频抽检"] == "正常", "原记录(表格数据源)应被回写"
        assert "单路复查" in target["抽检详情"]
        # 抽检后回写不触碰健康汇总(单路复查是诊断动作)
        assert w.results_panel.summary_label.text().startswith("健康：良好")
    finally:
        w.close()
        w.deleteLater()


def test_begin_single_check_ui_expands_log(qapp):
    """单路抽检开始时运行日志自动展开（收起状态下也要打开）。"""
    from ui.main_window import MainWindow

    w = MainWindow()
    try:
        w.log_panel.set_expanded(False)
        assert w.log_panel.expanded is False

        w._begin_single_check_ui("通道31")

        assert w.log_panel.expanded is True, "单路抽检应自动打开运行日志"
        assert w.results_panel.channel_table._single_check_running is True
    finally:
        w._finish_single_worker()
        w.close()
        w.deleteLater()


# ------------------------------------------------- 审查修复的回归


def test_single_check_rejects_non_numeric_channel(monkeypatch):
    """通道号非法(如 Track 解析失败留下的「未知」)必须直接报错。

    绝不能落到 build_nvr 的「过滤无效 → 抽检全部候选通道」——那等于点
    一路却拉了整机的流。
    """
    called = {"build": False}

    def fake_build(*a, **k):
        called["build"] = True
        raise AssertionError("非法通道不该走到 build_nvr")

    monkeypatch.setattr(scan_runner, "build_nvr", fake_build)
    rec = dict(_rec(), 通道="未知")
    with pytest.raises(ValueError, match="通道号无效"):
        run_single_av_check([rec], {"ip": "x", "password": "y"}, {})
    assert called["build"] is False


def test_single_check_normalizes_channel_tokens(monkeypatch):
    """通道号归一化后再下传(前导零、字符串数字都能对上物理通道)。"""
    capture: Dict[str, Any] = {}
    _patch_build(monkeypatch, capture)
    run_single_av_check([dict(_rec(), 通道="031")], {"ip": "x", "password": "y"}, {})
    assert capture["opt"]["av_channels"] == "31"


def test_single_check_exposes_nvr_for_cancel(monkeypatch):
    """nvr 构建后必须回调出去,否则取消只能置标志、打不断在途拉流。"""
    holder: Dict[str, Any] = {}

    class StubNVR:
        def __init__(self) -> None:
            self.cancelled = False

        def _run_deep_av_checks(self, recs):
            holder["has_nvr"] = holder["hook_nvr"] is not None

        def cancel(self):
            self.cancelled = True

        def close(self):
            pass

    monkeypatch.setattr(scan_runner, "build_nvr", lambda *a, **k: StubNVR())
    run_single_av_check(
        [_rec()],
        {"ip": "x", "password": "y"},
        {},
        on_nvr=lambda nvr: holder.update(hook_nvr=nvr),
    )
    assert holder.get("has_nvr") is True


def test_single_check_worker_holds_nvr_and_reports_cancel(monkeypatch, qapp):
    """worker 层：nvr 在跑之前就挂到 self._nvr，取消能到达设备实例。"""
    from nvr_core.util import ScanCancelled
    from ui import scan_worker as sw

    seen: Dict[str, Any] = {}

    class StubNVR:
        def _run_deep_av_checks(self, recs):
            seen["worker_nvr"] = worker._nvr
            raise ScanCancelled()

        def cancel(self):
            seen["cancelled"] = True

        def close(self):
            pass

    monkeypatch.setattr(scan_runner, "build_nvr", lambda *a, **k: StubNVR())
    worker = sw.SingleCheckWorker({"ip": "x", "password": "y"}, {}, [_rec()], None)
    signals: List[str] = []
    worker.scan_cancelled.connect(lambda: signals.append("cancelled"))
    worker.scan_failed.connect(lambda e: signals.append("failed"))

    worker._run()  # 同步执行,避免线程时序

    assert seen.get("worker_nvr") is not None, "取消必须能拿到 nvr 实例"
    assert signals == ["cancelled"], "取消应上报 cancelled 而不是 failed"


def test_start_single_check_end_to_end_with_stub_worker(qapp, monkeypatch):
    """整条链路：按 IP 定位设备 → 起 worker → 完成回写行 → 状态复位。"""
    from PySide6.QtCore import QObject, Signal

    from ui import main_window as mw

    started: List[Any] = []

    class StubWorker(QObject):
        log_line = Signal(str)
        progress_update = Signal(dict)
        check_finished = Signal(list)
        scan_failed = Signal(str)
        scan_cancelled = Signal()

        def __init__(self, device, options, recs, parent=None):
            super().__init__(parent)
            self.device, self.options, self.recs = device, dict(options), list(recs)
            self._thread = None
            started.append(self)

        def start(self):
            # 同步"完成"：走真实的 finished 处理链
            self.check_finished.emit(
                [
                    dict(r, 视频抽检="正常", 音频抽检="正常", 抽检详情="单路复查")
                    for r in self.recs
                ]
            )

        def cancel(self):
            pass

    monkeypatch.setattr(mw, "SingleCheckWorker", StubWorker)
    monkeypatch.setattr(mw, "_which_tools", lambda: {"ffmpeg": "f", "ffprobe": "fp"})

    w = mw.MainWindow()
    try:
        rec = dict(_rec("31", "3101"), 名称="前门")
        report = {
            "device_name": "测试NVR",
            "ip": "10.0.0.8",
            "info": {},
            "health": {"健康状态": "良好", "统计": {}, "预警信息": []},
            "records": [rec],
            "deep_av": True,
        }
        w.results_panel.render_result(report)
        # 结果里的 IP 对应档案中的设备（绕过表单/磁盘配置）
        w.left_panel.form_to_profile_dict = lambda name: {  # type: ignore[method-assign]
            "devices": [
                {"name": "测试NVR", "ip": "10.0.0.8", "password": "pw"},
            ],
            "scan_options": {"av_seconds": 5},
        }
        w.log_panel.set_expanded(False)

        w._start_single_check([rec])

        assert len(started) == 1, "应当起一个单路抽检 worker"
        assert started[0].device["ip"] == "10.0.0.8"
        assert rec["视频抽检"] == "正常", "完成后必须回写到结果集里的原记录"
        assert rec["抽检详情"] == "单路复查"
        assert w.single_worker is None, "完成后应释放 worker"
        assert w.log_panel.expanded is True, "单路抽检时日志自动打开"
        assert w.results_panel.channel_table._single_check_running is False
        assert w.results_panel.channel_table.btn_single_check.isEnabled() is False, (
            "本用例未在表里选行，按钮应保持禁用"
        )
    finally:
        w.close()
        w.deleteLater()


def test_start_single_check_aborts_when_device_missing(qapp, monkeypatch):
    """结果对应的设备不在当前档案里 → 明确拒绝，不猜设备乱连。"""
    from ui import main_window as mw

    shown: List[str] = []
    monkeypatch.setattr(
        mw.QMessageBox, "warning", lambda *a, **k: shown.append(str(a[2]))
    )
    monkeypatch.setattr(mw, "_which_tools", lambda: {"ffmpeg": "f", "ffprobe": "fp"})

    w = mw.MainWindow()
    try:
        w.results_panel.render_result(
            {
                "device_name": "旧设备",
                "ip": "192.168.9.9",
                "info": {},
                "health": {"健康状态": "良好", "统计": {}, "预警信息": []},
                "records": [dict(_rec("31", "3101"))],
                "deep_av": True,
            }
        )
        w.left_panel.form_to_profile_dict = lambda name: {  # type: ignore[method-assign]
            "devices": [{"name": "别的", "ip": "10.0.0.8", "password": "pw"}],
            "scan_options": {},
        }

        w._start_single_check([dict(_rec("31", "3101"))])

        assert shown and "找不到结果对应的设备" in shown[0]
        assert w.single_worker is None
        assert w.log_panel.expanded is False, "没真正开始就不该动日志面板"
    finally:
        w.close()
        w.deleteLater()

"""主窗口：顶栏档案 + 左配置/右结果 + 底部状态栏。

B1：面板细节已拆到 ui/panels/（left_panel / results_panel / log_panel），
本文件只负责组装与主窗级编排。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from config_store import ConfigStore
from hikvision_status import _which_tools
from services import export_report, history
from ui import theme
from ui.panels.left_panel import (
    LEFT_PANEL_MIN_WIDTH,
    LeftPanel,
    _lookback_minutes_of,
    _lookback_value_for_minutes,
)
from ui.panels.log_panel import LOG_MAX_LINES, LogPanel
from ui.panels.results_panel import (
    ChannelDetailDialog,
    MetricCard,
    ResultsExpandWindow,
    ResultsPanel,
)
from ui.scan_worker import QueueScanWorker, ScanWorker
from ui.widgets.profile_bar import ProfileBar
from ui.widgets.status_bar import StatusBar

APP_TITLE = "NVR 状态巡检"
APP_VERSION = "2.0.0"

__all__ = [
    "APP_TITLE",
    "APP_VERSION",
    "LOG_MAX_LINES",
    "LEFT_PANEL_MIN_WIDTH",
    "MainWindow",
    "MetricCard",
    "ChannelDetailDialog",
    "ResultsExpandWindow",
    "_lookback_minutes_of",
    "_lookback_value_for_minutes",
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} v{APP_VERSION}")
        # B8: 最小尺寸，避免小屏/压缩时布局崩坏
        self.setMinimumSize(1000, 700)
        self._icon_path = self._find_icon()
        if self._icon_path:
            self.setWindowIcon(QIcon(self._icon_path))

        self.store = ConfigStore()
        self.settings = QSettings("NVRStatus", "NVRStatus")
        self.worker: Optional[ScanWorker] = None
        self._theme_mode = self._load_theme_mode()

        self._build_ui()
        self._load_geometry()
        self._apply_theme(self._theme_mode)

        QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_system_scheme_changed)

        self._reload_profiles_ui()
        self._load_active_profile_to_form()
        self._check_ffmpeg()
        self.log_panel.log("就绪。配置设备后点击「快速巡检」或「深度巡检」。", level="info")

    def _on_system_scheme_changed(self, *_args) -> None:
        if self._theme_mode == theme.ThemeMode.SYSTEM:
            self._apply_theme(theme.ThemeMode.SYSTEM)

    # ---------- 图标 / 主题 ----------

    def _find_icon(self) -> Optional[str]:
        candidates = []
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            candidates.append(os.path.join(sys._MEIPASS, "assets", "app_logo.png"))  # type: ignore[attr-defined]
            candidates.append(os.path.join(os.path.dirname(sys.executable), "assets", "app_logo.png"))
        candidates.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "app_logo.png"))
        for p in candidates:
            if p and os.path.isfile(p):
                return p
        return None

    def _load_theme_mode(self) -> theme.ThemeMode:
        s = self.settings.value("ui/theme", "system")
        for m in theme.ThemeMode:
            if m.value == s:
                return m
        return theme.ThemeMode.SYSTEM

    def _apply_theme(self, mode: theme.ThemeMode) -> None:
        self._theme_mode = mode
        theme.apply_theme(QApplication.instance(), mode)
        self.left_panel.set_theme_mode(mode)
        self._sync_theme_widgets()

    def _sync_theme_widgets(self) -> None:
        dark = theme.effective_dark()
        self.left_panel.set_dark(dark)
        self.results_panel.set_dark(dark)
        self.log_panel.set_dark(dark)
        self.status_bar.set_dark(dark)
        dlg = getattr(self, "_history_dialog", None)
        if dlg is not None:
            dlg.set_dark(dark)

    def _on_theme_selected(self, text: str) -> None:
        for m in theme.ThemeMode:
            if theme.display_name(m) == text:
                self._theme_mode = m
                self.settings.setValue("ui/theme", m.value)
                self._apply_theme(m)
                return

    # ---------- 布局 ----------

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(8)

        # 顶栏：档案
        self.profile_bar = ProfileBar()
        root.addWidget(self.profile_bar)

        # 主体：左配置 | 右结果
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(6)
        self.splitter.setChildrenCollapsible(False)
        root.addWidget(self.splitter, 1)

        self.left_panel = LeftPanel()
        self.results_panel = ResultsPanel()
        self.log_panel = LogPanel()

        right_root = QWidget()
        right = QVBoxLayout(right_root)
        right.setContentsMargins(6, 0, 0, 0)
        right.setSpacing(6)
        right.addWidget(self.results_panel, 1)
        right.addWidget(self.log_panel, 0)

        self.splitter.addWidget(self.left_panel)
        self.splitter.addWidget(right_root)
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([LEFT_PANEL_MIN_WIDTH + 20, 860])

        # 底部状态栏
        self.status_bar = StatusBar()
        root.addWidget(self.status_bar)

        self.setCentralWidget(central)

        self._connect_signals()
        self._restore_splitter()

    def _connect_signals(self) -> None:
        bar = self.profile_bar
        bar.profile_changed.connect(self._on_profile_change)
        bar.new_requested.connect(self._profile_new)
        bar.save_as_requested.connect(self._profile_save_as)
        bar.rename_requested.connect(self._profile_rename)
        bar.delete_requested.connect(self._profile_delete)
        bar.import_requested.connect(self._profile_import)
        bar.export_requested.connect(self._profile_export)

        left = self.left_panel
        left.scan_requested.connect(self._start_scan)
        left.cancel_requested.connect(self._cancel_scan)
        left.save_profile_requested.connect(self._save_form_to_profile)
        left.history_requested.connect(self._open_history)
        left.theme_selected.connect(self._on_theme_selected)
        left.log_requested.connect(self._log)

        results = self.results_panel
        results.export_requested.connect(self._export_result)
        results.log_requested.connect(self._log)

    # ---------- 窗体状态记忆 ----------

    def _load_geometry(self) -> None:
        self.resize(1240, 820)
        geo = self.settings.value("win/geometry")
        if geo is not None:
            try:
                self.restoreGeometry(geo)
            except Exception:
                pass
            else:
                if not self.settings.value("win/placed", False, type=bool):
                    self._center_window()
        else:
            self._center_window()
        st = self.settings.value("win/state")
        if st is not None:
            try:
                self.restoreState(st)
            except Exception:
                pass

    def _center_window(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = self.frameGeometry()
        geo.moveCenter(screen.availableGeometry().center())
        self.move(geo.topLeft())
        self.settings.setValue("win/placed", True)

    def _restore_splitter(self) -> None:
        if self.settings.contains("ui/splitter"):
            sizes = self.settings.value("ui/splitter")
            if isinstance(sizes, list) and len(sizes) == 2:
                try:
                    left, right = int(sizes[0]), int(sizes[1])
                    # 历史记忆若把左侧压得过窄，强制抬到最小宽度，避免内容被裁切
                    if left < LEFT_PANEL_MIN_WIDTH:
                        delta = LEFT_PANEL_MIN_WIDTH - left
                        left = LEFT_PANEL_MIN_WIDTH
                        right = max(200, right - delta)
                    self.splitter.setSizes([left, right])
                except (TypeError, ValueError):
                    pass

    def closeEvent(self, event) -> None:
        self.settings.setValue("ui/theme", self._theme_mode.value)
        self.settings.setValue("win/geometry", self.saveGeometry())
        self.settings.setValue("win/state", self.saveState())
        sizes = self.splitter.sizes()
        if sizes:
            self.settings.setValue("ui/splitter", list(sizes))
        if self.worker is not None:
            self.worker.cancel()
        super().closeEvent(event)

    # ---------- 档案 ----------

    def _reload_profiles_ui(self) -> None:
        names = self.store.list_profiles() or ["默认"]
        active = self.store.get_active_name()
        if active not in names:
            active = names[0]
        self.profile_bar.set_profiles(names, active)

    def _on_profile_change(self, name: str) -> None:
        if name and name in self.store.list_profiles():
            self.store.set_active(name)
            self._load_active_profile_to_form()
            self.log_panel.log(f"已切换配置档案: {name}", level="info")

    def _load_active_profile_to_form(self) -> None:
        prof = self.store.get_profile()
        prof["devices"] = self.store.resolve_devices(prof.get("name") or self.store.get_active_name())
        self.left_panel.load_profile_to_form(prof)

    def _save_form_to_profile(self) -> bool:
        try:
            prof = self.left_panel.form_to_profile_dict(self.store.get_active_name())
            for d in prof["devices"]:
                if not d.get("ip"):
                    QMessageBox.warning(self, "错误", "设备 IP 不能为空")
                    return False
            self.store.update_profile(None, prof)
            self.log_panel.log(f"已保存档案「{self.store.get_active_name()}」", level="ok")
            return True
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return False

    def _profile_new(self) -> None:
        name, ok = QInputDialog.getText(self, "新建配置档案", "新档案名称:")
        name = (name or "").strip()
        if not ok or not name:
            return
        created = self.store.create_profile(name)
        self._reload_profiles_ui()
        self.profile_bar.set_active(created)
        self._load_active_profile_to_form()
        self.log_panel.log(f"已新建档案: {created}", level="ok")

    def _profile_save_as(self) -> None:
        name, ok = QInputDialog.getText(self, "另存配置", "另存为档案名称:")
        name = (name or "").strip()
        if not ok or not name:
            return
        if not self._save_form_to_profile():
            return
        created = self.store.create_profile(name, clone_from=self.store.get_active_name())
        self.store.set_active(created)
        self._reload_profiles_ui()
        self.profile_bar.set_active(created)
        self._load_active_profile_to_form()
        self._save_form_to_profile()
        self.log_panel.log(f"已另存为: {created}", level="ok")

    def _profile_rename(self) -> None:
        old = self.store.get_active_name()
        new, ok = QInputDialog.getText(self, "重命名", f"将「{old}」重命名为:")
        new = (new or "").strip()
        if not ok or not new:
            return
        if self.store.rename_profile(old, new):
            self._reload_profiles_ui()
            self.profile_bar.set_active(new)
            self.log_panel.log(f"已重命名: {old} → {new}", level="ok")
        else:
            QMessageBox.warning(self, "失败", "重命名失败（名称冲突或非法）")

    def _profile_delete(self) -> None:
        name = self.store.get_active_name()
        if not QMessageBox.question(self, "确认", f"删除配置档案「{name}」？") == QMessageBox.StandardButton.Yes:
            return
        if self.store.delete_profile(name):
            self._reload_profiles_ui()
            self._load_active_profile_to_form()
            self.log_panel.log(f"已删除档案: {name}", level="ok")
        else:
            QMessageBox.warning(self, "失败", "无法删除（至少保留一个档案）")

    def _profile_export(self) -> None:
        name = self.store.get_active_name()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出配置",
            f"{name}.json",
            "JSON (*.json)",
        )
        if not path:
            return
        self._save_form_to_profile()
        try:
            self.store.export_profile(name, path)
            self.log_panel.log(f"已导出: {path}", level="ok")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _profile_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入配置",
            "",
            "JSON (*.json);;所有文件 (*.*)",
        )
        if not path:
            return
        try:
            name = self.store.import_profile(path)
            self._reload_profiles_ui()
            self.profile_bar.set_active(name)
            self._load_active_profile_to_form()
            self.log_panel.log(f"已导入档案: {name}", level="ok")
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))

    # ---------- 扫描 ----------

    def _check_ffmpeg(self) -> None:
        tools = _which_tools()
        ff, fp = tools.get("ffmpeg"), tools.get("ffprobe")
        self.left_panel.set_ffmpeg_status(bool(ff and fp))

    def _worker_busy(self) -> bool:
        w = self.worker
        if w is None:
            return False
        t = getattr(w, "_thread", None)
        return t is not None and t.is_alive()

    def _start_scan(self, mode: str = "quick") -> None:
        if self._worker_busy():
            QMessageBox.information(self, "提示", "扫描正在进行中")
            return
        deep = mode == "deep"
        prof = self.left_panel.form_to_profile_dict(self.store.get_active_name())
        devices = prof["devices"]
        if not devices:
            QMessageBox.warning(self, "错误", "请至少配置一台设备")
            return

        opt = dict(prof["scan_options"] or {})
        opt["deep_av_check"] = deep
        opt["av_save"] = bool(self.left_panel.chk_av_save.isChecked()) if deep else False
        prof["scan_options"] = opt
        self.store.update_profile(None, prof)

        target = self.left_panel.selected_target()
        queue_all = bool(target.get("all"))
        if queue_all:
            bad = [d["name"] for d in devices if not (d.get("ip") or "").strip()]
            if bad:
                QMessageBox.warning(
                    self, "错误", "以下设备 IP 为空，无法加入队列：\n" + "\n".join(bad)
                )
                return
            if any(not d.get("password") for d in devices):
                if not QMessageBox.question(
                    self, "确认", "部分设备密码为空，是否继续？"
                ) == QMessageBox.StandardButton.Yes:
                    return
        else:
            device = target.get("device")
            if device is None or not device.get("ip"):
                QMessageBox.warning(self, "错误", "设备 IP 为空")
                return
            if not device.get("password"):
                if not QMessageBox.question(self, "确认", "密码为空，是否继续？") == QMessageBox.StandardButton.Yes:
                    return

        if deep and not (_which_tools().get("ffmpeg") and _which_tools().get("ffprobe")):
            if not QMessageBox.question(
                self,
                "缺少 ffmpeg",
                "深度巡检需要 ffmpeg/ffprobe，当前未检测到。\n"
                "是否改为仅做状态与近期录像检查继续？（取消则中止）",
            ) == QMessageBox.StandardButton.Yes:
                return
            opt["deep_av_check"] = False
            opt["av_save"] = False
            deep = False

        mode_label = "深度巡检" if deep else "快速巡检"
        if deep and opt.get("av_save"):
            mode_label += " · 保存片段"
        self.results_panel.clear_result()

        if queue_all:
            self._start_queue(devices, opt, deep, mode_label)
            return

        self.log_panel.log(
            f"—— {mode_label} {device.get('name')} ({device.get('ip')}) ——",
            level="step",
        )
        self.log_panel.log(
            f"模式: {mode_label} · 回溯 {self.left_panel._format_lookback_label(opt)}"
            + (f" · 快速模式(仅配置)" if opt.get("no_search") and not deep else ""),
            level="info",
        )
        self.left_panel.set_scan_buttons_enabled(False)
        self.left_panel.set_cancel_enabled(True)
        self.status_bar.set_state(
            "running", detail=f"{mode_label} · {device.get('name')} ({device.get('ip')})"
        )
        self.status_bar.set_progress(0.02)

        worker = ScanWorker(device, opt, self)
        worker.log_line.connect(self._on_log_line)
        worker.progress_update.connect(self._on_progress_update)
        worker.scan_finished.connect(self._on_scan_finished)
        worker.scan_failed.connect(self._on_scan_failed)
        worker.scan_cancelled.connect(self._on_scan_cancelled)
        self.worker = worker
        worker.start()

    def _start_queue(
        self,
        devices: List[Dict[str, Any]],
        opt: Dict[str, Any],
        deep: bool,
        mode_label: str,
    ) -> None:
        """B5：多设备队列巡检（顺序执行，逐台刷新结果）。"""
        self.log_panel.log(
            f"—— 队列巡检 {len(devices)} 台设备 ——",
            level="step",
        )
        self.log_panel.log(
            f"模式: {mode_label} · 回溯 {self.left_panel._format_lookback_label(opt)}"
            + (f" · 快速模式(仅配置)" if opt.get("no_search") and not deep else ""),
            level="info",
        )
        self.left_panel.set_scan_buttons_enabled(False)
        self.left_panel.set_cancel_enabled(True)
        self.status_bar.set_state(
            "running", detail=f"队列 {mode_label} · {len(devices)} 台"
        )
        self.status_bar.set_progress(0.0)

        worker = QueueScanWorker(devices, opt, self)
        worker.log_line.connect(self._on_log_line)
        worker.progress_update.connect(self._on_progress_update)
        worker.device_finished.connect(self._on_queue_device_finished)
        worker.queue_finished.connect(self._on_queue_finished)
        worker.scan_failed.connect(self._on_scan_failed)
        worker.scan_cancelled.connect(self._on_scan_cancelled)
        self.worker = worker
        worker.start()

    def _on_queue_device_finished(self, report: Dict[str, Any]) -> None:
        if report.get("error"):
            self.log_panel.log(
                f"{report.get('device_name')}: {report['error']}", level="error"
            )
            return
        self.results_panel.render_result(report)
        self._save_history(report)

    def _on_queue_finished(self, results: List[Dict[str, Any]]) -> None:
        self.status_bar.set_progress(1.0, current=None, total=None)
        self._finish_worker(results)
        ok = sum(
            1
            for r in results
            if not r.get("error") and (r.get("health") or {}).get("健康状态") == "良好"
        )
        warn = sum(
            1
            for r in results
            if not r.get("error") and (r.get("health") or {}).get("健康状态") != "良好"
        )
        fail = sum(1 for r in results if r.get("error"))
        detail = f"队列完成 {len(results)} 台 · 良好 {ok} · 预警 {warn} · 失败 {fail}"
        if fail:
            self.status_bar.set_state("error", text="异常", detail=detail)
            self.log_panel.log(detail, level="error")
        elif warn:
            self.status_bar.set_state("warn", text="预警", detail=detail)
            self.log_panel.log(detail, level="warn")
        else:
            self.status_bar.set_state("ok", text="完成", detail=detail)
            self.log_panel.log(detail, level="ok")

    def _save_history(self, report: Dict[str, Any]) -> None:
        """B6：归档一份巡检结果（失败结果同样归档，便于回溯）。"""
        if not isinstance(report, dict):
            return
        try:
            history.save_report(report)
        except Exception as e:
            self.log_panel.log(f"历史报告归档失败: {e}", level="warn")

    def _open_history(self) -> None:
        """B6：历史报告列表（查看渲染 / 再导出）。"""
        from ui.widgets.history_dialog import HistoryDialog

        if getattr(self, "_history_dialog", None) is None:
            dlg = HistoryDialog(self)
            dlg.report_loaded.connect(self._on_history_report_loaded)
            self._history_dialog = dlg
        else:
            dlg = self._history_dialog
            dlg.refresh()
        dlg.set_dark(theme.effective_dark())
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_history_report_loaded(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return
        self.results_panel.render_result(data)
        name = data.get("device_name") or ""
        self.status_bar.set_state(
            "ready", detail=f"已加载历史报告 · {name}".strip(" ·")
        )
        self.log_panel.log(
            f"已加载历史报告：{name or data.get('ip') or '?'}（历史归档）",
            level="info",
        )

    def _cancel_scan(self) -> None:
        w = self.worker
        if w is not None:
            w.cancel()
            self.left_panel.set_cancel_enabled(False)
            self.log_panel.log("正在取消巡检…", level="warn")

    def _on_scan_cancelled(self) -> None:
        self._finish_worker(None)
        self.status_bar.set_state("ready", detail="巡检已取消")
        self.log_panel.log("巡检已取消", level="warn")

    def _finish_worker(self, _result: Optional[Dict[str, Any]]) -> None:
        self.worker = None
        self._set_scan_buttons_enabled(True)
        self.left_panel.set_cancel_enabled(False)

    # ---------- 进度 ----------

    _PHASE_RANGES = {
        "init": (0.0, 0.02),
        "connect": (0.02, 0.08),
        "device": (0.08, 0.14),
        "sys": (0.14, 0.18),
        "cameras": (0.18, 0.26),
        "recording": (0.26, 0.28),
        "disk": (0.28, 0.70),
        "deep": (0.70, 0.96),
        "health": (0.96, 0.98),
        "storage": (0.98, 0.99),
        "done": (0.99, 1.0),
    }

    def _on_progress_update(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return
        msg = str(data.get("msg") or "").strip()
        phase = str(data.get("phase") or "")
        current = data.get("current")
        total = data.get("total")
        overall = data.get("overall")

        frac = None
        if overall is not None:
            try:
                frac = float(overall)
            except (TypeError, ValueError):
                frac = None
        if frac is None and current is not None and total:
            try:
                c, t = int(current), int(total)
                if t > 0:
                    lo, hi = self._PHASE_RANGES.get(phase, (0.0, 1.0))
                    frac = lo + (hi - lo) * (c / t)
            except (TypeError, ValueError):
                frac = None
        if frac is None and phase in self._PHASE_RANGES:
            frac = self._PHASE_RANGES[phase][0]

        if frac is not None:
            self.status_bar.set_progress(frac, current=current, total=total)
        if msg:
            brief = msg.replace("\n", " ")
            if len(brief) > 72:
                brief = brief[:72] + "…"
            self.status_bar.set_state("running", detail=brief)

    # ---------- 结果 ----------

    def _on_scan_finished(self, data: Dict[str, Any]) -> None:
        self.status_bar.set_progress(1.0, current=None, total=None)
        self._finish_worker(data)
        health = data.get("health") or {}
        hstat = health.get("健康状态") or "未知"
        name = data.get("device_name") or ""
        if hstat == "良好":
            self.status_bar.set_state("ok", text="完成", detail=f"健康：良好 · {name}".strip(" ·"))
        elif hstat == "严重":
            self.status_bar.set_state("error", text="异常", detail=f"健康：严重 · {name}".strip(" ·"))
        else:
            self.status_bar.set_state("warn", text="预警", detail=f"健康：{hstat} · {name}".strip(" ·"))
        self.results_panel.render_result(data)
        self._save_history(data)
        self.log_panel.log("查询完成。", level="ok")

    def _on_scan_failed(self, err: str) -> None:
        self._finish_worker(None)
        brief = (err or "未知错误").strip().splitlines()[0] if err else "未知错误"
        self.status_bar.set_state("error", detail=brief[:100])
        self.log_panel.log(f"巡检失败: {brief}", level="error")
        if "\n" in err:
            self.log_panel.log(err[:500], level="muted")
        QMessageBox.critical(self, "巡检失败", err[:800])

    def _set_scan_buttons_enabled(self, enabled: bool) -> None:
        self.left_panel.set_scan_buttons_enabled(enabled)
        # 导出 / 大窗：有结果且非巡检中才可用
        self.results_panel.set_result_actions_enabled(enabled)

    # ---------- 导出 ----------

    def _export_result(self) -> None:
        data = self.results_panel.last_result
        if not data:
            QMessageBox.information(self, "导出", "暂无巡检结果，请先执行一次巡检。")
            return
        name = str(data.get("device_name") or "NVR").replace("/", "_").replace(" ", "_")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出巡检结果",
            f"nvr_report_{name}_{stamp}.csv",
            "CSV 表格 (*.csv);;文本报告 (*.txt);;全部 (*.*)",
        )
        if not path:
            return
        try:
            if path.lower().endswith(".txt"):
                export_report.export_txt(path, data, self.results_panel.warn_text())
            else:
                if not path.lower().endswith(".csv"):
                    path += ".csv"
                export_report.export_csv(path, data)
            self.log_panel.log(f"已导出: {path}", level="ok")
            QMessageBox.information(self, "导出成功", f"已保存到：\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    # ---------- 日志 ----------

    def _log(self, msg: str, level: Optional[str] = None) -> None:
        self.log_panel.log(msg, level)

    def _on_log_line(self, msg: str) -> None:
        self.log_panel.log(msg)

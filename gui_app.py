#!/usr/bin/env python3
"""
NVR 状态查询 GUI — 安装即用、含深度抽检、多配置档案。
依赖: customtkinter, requests; 深度抽检需 ffmpeg/ffprobe。
"""

from __future__ import annotations

import csv
import os
import queue
import sys
import threading
import traceback
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional, Tuple

try:
    import customtkinter as ctk
except ImportError as e:
    raise SystemExit(
        "缺少 customtkinter，请执行: uv add customtkinter\n或: pip install customtkinter"
    ) from e

from config_store import (
    ConfigStore,
    _empty_device,
    app_data_dir,
    default_av_save_root,
)
from hikvision_status import HikvisionNVR, _which_tools


APP_TITLE = "NVR 状态巡检"
APP_VERSION = "1.0.0"


class ScanWorker(threading.Thread):
    """后台扫描，避免卡住 UI。"""

    def __init__(
        self,
        device: Dict[str, Any],
        options: Dict[str, Any],
        msg_q: queue.Queue,
    ):
        super().__init__(daemon=True)
        self.device = device
        self.options = options
        self.msg_q = msg_q
        # 注意: 不可命名为 self._stop，会覆盖 Thread 内部的 _stop() 方法
        self._cancel = threading.Event()

    def stop(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            def progress(
                msg: str = "",
                current: Optional[int] = None,
                total: Optional[int] = None,
                phase: str = "",
                overall: Optional[float] = None,
                **_extra: Any,
            ) -> None:
                """日志 + 结构化进度（供底部进度条同步）。"""
                if msg:
                    self.msg_q.put(("log", msg))
                if (
                    overall is not None
                    or current is not None
                    or total is not None
                    or phase
                    or msg
                ):
                    self.msg_q.put(
                        (
                            "progress",
                            {
                                "msg": msg or "",
                                "current": current,
                                "total": total,
                                "phase": phase or "",
                                "overall": overall,
                            },
                        )
                    )

            opt = self.options
            av_limit = opt.get("av_limit") or 0
            save_root = (opt.get("av_save_root") or "").strip() or default_av_save_root()
            tools = _which_tools()
            want_deep = bool(opt.get("deep_av_check") or opt.get("av_save"))
            has_ff = bool(tools.get("ffmpeg") and tools.get("ffprobe"))
            if want_deep and not has_ff:
                progress(
                    "未找到 ffmpeg/ffprobe，已跳过深度抽检，仅做状态与近期录像检查",
                    phase="init",
                    overall=0.02,
                )
                want_deep = False
            want_save = bool(opt.get("av_save")) and has_ff and want_deep

            nvr = HikvisionNVR(
                ip=self.device["ip"],
                port=int(self.device.get("port") or 80),
                username=self.device.get("username") or "admin",
                password=self.device.get("password") or "",
                use_ssl=bool(self.device.get("ssl")),
                lookback_minutes=int(opt.get("lookback") or 60),
                check_disk_recording=not bool(opt.get("no_search")),
                search_workers=int(opt.get("workers") or 8),
                deep_av_check=want_deep,
                av_seconds=int(opt.get("av_seconds") or 6),
                av_workers=int(opt.get("av_workers") or 2),
                av_limit=int(av_limit) if av_limit and int(av_limit) > 0 else None,
                silence_db=float(opt.get("silence_db") or -80),
                busy_start_hour=int(opt.get("busy_start") or 10),
                busy_end_hour=int(opt.get("busy_end") or 18),
                av_save=want_save,
                av_save_root=save_root,
                quiet=True,
                progress_callback=progress,
            )

            progress(
                f"连接 {self.device.get('name')} ({self.device.get('ip')})...",
                phase="connect",
                overall=0.03,
            )
            info = nvr.get_device_info()
            if not info:
                self.msg_q.put(("error", "无法获取设备信息，请检查 IP/账号/网络"))
                return
            progress(
                f"设备: {info.get('型号', '')} 固件 {info.get('固件版本', '')}",
                phase="device",
                overall=0.10,
            )

            progress("读取系统状态…", phase="sys", overall=0.14)
            sys_status = nvr.get_system_status()
            progress("获取摄像头列表…", phase="cameras", overall=0.18)
            cameras = nvr.get_cameras()
            progress(
                f"摄像头 {len(cameras)} 路，开始录像/近期录像检查...",
                phase="recording",
                overall=0.26,
                current=0,
                total=max(len(cameras), 1),
            )
            records = nvr.get_recording_status()
            progress("汇总健康状态…", phase="health", overall=0.97)
            health = nvr.get_health_summary()
            progress("读取硬盘状态…", phase="storage", overall=0.99)
            drives = nvr.get_storage_status()
            progress("巡检完成", phase="done", overall=1.0)

            self.msg_q.put(
                (
                    "done",
                    {
                        "device_name": self.device.get("name") or self.device.get("ip"),
                        "info": info,
                        "sys_status": sys_status,
                        "cameras": cameras,
                        "records": records,
                        "health": health,
                        "drives": drives,
                        "av_save_dir": nvr.av_save_dir,
                        "deep_av": nvr.deep_av_check,
                    },
                )
            )
        except Exception as e:
            self.msg_q.put(("error", f"{e}\n{traceback.format_exc()}"))


class NVRApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.title(f"{APP_TITLE} v{APP_VERSION}")
        # 左侧栏已收窄，默认窗口略紧凑；按屏幕居中，并限制不超过屏占比
        self.minsize(960, 768)
        self._apply_default_geometry()
        self._set_window_icon()

        self.store = ConfigStore()
        self.msg_q: queue.Queue = queue.Queue()
        self.worker: Optional[ScanWorker] = None
        self._device_rows: List[Dict[str, Any]] = []
        self._result_records: List[Dict[str, Any]] = []
        self._last_result: Optional[Dict[str, Any]] = None  # 最近一次巡检结果(导出用)

        self._build_ui()
        self._reload_profiles_ui()
        self._load_active_profile_to_form()
        self.after(200, self._poll_queue)
        self._check_ffmpeg()

    def _apply_default_geometry(self) -> None:
        """按屏幕尺寸设置更合适的默认窗口大小，并居中。"""
        try:
            self.update_idletasks()
            sw = int(self.winfo_screenwidth() or 1440)
            sh = int(self.winfo_screenheight() or 900)
        except Exception:
            sw, sh = 1440, 900

        # 目标约 1140×912（高度在 760 基础上 +20%），小屏按比例缩小
        w = min(1140, max(960, int(sw * 0.72)))
        h = min(912, max(768, int(sh * 0.86)))
        # 为菜单栏 / Dock 留边
        w = min(w, sw - 80)
        h = min(h, sh - 100)
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _set_window_icon(self) -> None:
        """设置窗口/任务栏图标（开发运行与打包后均尝试加载）。"""
        candidates = []
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            candidates.append(os.path.join(sys._MEIPASS, "assets", "app_logo.png"))  # type: ignore[attr-defined]
            candidates.append(os.path.join(os.path.dirname(sys.executable), "assets", "app_logo.png"))
        here = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(here, "assets", "app_logo.png"))
        for path in candidates:
            if path and os.path.isfile(path):
                try:
                    from PIL import Image, ImageTk  # type: ignore

                    img = Image.open(path)
                    self._icon_img = ImageTk.PhotoImage(img)
                    self.iconphoto(True, self._icon_img)
                    return
                except Exception:
                    try:
                        # 无 Pillow 时用 tk 原生（部分平台支持 png）
                        self.iconbitmap(path)  # type: ignore[arg-type]
                        return
                    except Exception:
                        try:
                            from tkinter import PhotoImage

                            self._icon_img = PhotoImage(file=path)
                            self.iconphoto(True, self._icon_img)
                            return
                        except Exception:
                            pass

    # ---------- UI ----------

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # 顶栏: 配置档案
        top = ctk.CTkFrame(self)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(top, text="配置档案", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, padx=8, pady=8
        )
        self.profile_var = ctk.StringVar(value="")
        self.profile_menu = ctk.CTkOptionMenu(
            top, variable=self.profile_var, values=["默认"], command=self._on_profile_change
        )
        self.profile_menu.grid(row=0, column=1, sticky="w", padx=4, pady=8)

        ctk.CTkButton(top, text="新建", width=70, command=self._profile_new).grid(
            row=0, column=2, padx=4
        )
        ctk.CTkButton(top, text="另存为", width=70, command=self._profile_save_as).grid(
            row=0, column=3, padx=4
        )
        ctk.CTkButton(top, text="重命名", width=70, command=self._profile_rename).grid(
            row=0, column=4, padx=4
        )
        ctk.CTkButton(top, text="删除", width=70, fg_color="#a33", command=self._profile_delete).grid(
            row=0, column=5, padx=4
        )
        ctk.CTkButton(top, text="导入", width=70, command=self._profile_import).grid(
            row=0, column=6, padx=4
        )
        ctk.CTkButton(top, text="导出", width=70, command=self._profile_export).grid(
            row=0, column=7, padx=4
        )

        # 主体: 左配置 + 右结果
        # 左侧功能区固定约 250px（再缩约 1/4），右侧结果区吃满剩余宽度
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=12, pady=6)
        self._left_col_width = 250
        body.grid_columnconfigure(0, weight=0, minsize=self._left_col_width)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # 左侧：上方可滚动表单 + 底部固定操作栏（避免按钮被裁切）
        left_panel = ctk.CTkFrame(body, fg_color="transparent")
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left_panel.grid_rowconfigure(0, weight=1)
        left_panel.grid_rowconfigure(1, weight=0)
        left_panel.grid_columnconfigure(0, weight=1)

        left = ctk.CTkScrollableFrame(
            left_panel,
            label_text="设备与扫描设置",
            width=self._left_col_width - 12,
        )
        left.grid(row=0, column=0, sticky="nsew")
        self.left_scroll = left
        self.left_panel = left_panel

        right = ctk.CTkFrame(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        # --- 设备列表（仅显示名称 + IP；添加/编辑用独立窗口）---
        ctk.CTkLabel(left, text="设备列表", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=4, pady=(4, 2)
        )
        self.dev_frame = ctk.CTkFrame(left)
        self.dev_frame.pack(fill="x", padx=4, pady=4)

        btn_row = ctk.CTkFrame(left, fg_color="transparent")
        btn_row.pack(fill="x", padx=4, pady=4)
        ctk.CTkButton(btn_row, text="添加设备", width=78, command=self._add_device).pack(
            side="left", padx=2
        )
        ctk.CTkButton(btn_row, text="删除选中", width=78, command=self._del_device).pack(
            side="left", padx=2
        )
        ctk.CTkButton(btn_row, text="保存档案", width=78, command=self._save_form_to_profile).pack(
            side="left", padx=2
        )

        ctk.CTkLabel(left, text="扫描目标设备").pack(anchor="w", padx=4, pady=(8, 2))
        self.scan_device_var = ctk.StringVar(value="")
        self.scan_device_menu = ctk.CTkOptionMenu(left, variable=self.scan_device_var, values=["-"])
        self.scan_device_menu.pack(fill="x", padx=4, pady=2)

        # --- 扫描设置（默认折叠，按钮展开）---
        self.var_lookback = ctk.StringVar(value="60")
        self.var_workers = ctk.StringVar(value="8")
        self.var_deep = ctk.BooleanVar(value=False)  # 由巡检按钮决定，档案可记忆
        self.var_av_save = ctk.BooleanVar(value=False)
        self.var_av_sec = ctk.StringVar(value="6")
        self.var_av_workers = ctk.StringVar(value="2")
        self.var_av_limit = ctk.StringVar(value="0")
        self.var_busy_start = ctk.StringVar(value="10")
        self.var_busy_end = ctk.StringVar(value="18")
        self.var_silence = ctk.StringVar(value="-80")
        self.var_no_search = ctk.BooleanVar(value=False)
        self.var_save_root = ctk.StringVar(value=default_av_save_root())

        self._scan_settings_expanded = False
        self.btn_toggle_scan_settings = ctk.CTkButton(
            left,
            text="▶  扫描设置",
            anchor="center",
            height=40,
            corner_radius=8,
            border_width=2,
            border_color=("#3b82f6", "#60a5fa"),
            fg_color=("#eff6ff", "#1e3a5f"),
            hover_color=("#dbeafe", "#274b72"),
            text_color=("#1d4ed8", "#93c5fd"),
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._toggle_scan_settings,
        )
        self.btn_toggle_scan_settings.pack(fill="x", padx=4, pady=(14, 6))

        # 扫描设置（侧栏内展开，默认折叠）
        opts = ctk.CTkFrame(left, fg_color=("gray92", "gray17"), corner_radius=8)
        self.scan_settings_frame = opts
        opts.grid_columnconfigure(0, minsize=118, weight=0)
        opts.grid_columnconfigure(1, weight=1)

        label_w = 118
        entry_w = 88
        r = 0

        def add_entry_row(
            parent: ctk.CTkFrame,
            label: str,
            variable: ctk.StringVar,
            row: int,
            width: int = entry_w,
        ) -> int:
            ctk.CTkLabel(parent, text=label, width=label_w, anchor="w").grid(
                row=row, column=0, sticky="w", padx=(8, 8), pady=5
            )
            ctk.CTkEntry(parent, textvariable=variable, width=width).grid(
                row=row, column=1, sticky="w", padx=(0, 8), pady=5
            )
            return row + 1

        def add_check_row(parent: ctk.CTkFrame, text: str, variable: ctk.BooleanVar, row: int) -> int:
            ctk.CTkCheckBox(parent, text=text, variable=variable).grid(
                row=row, column=0, columnspan=2, sticky="w", padx=(8, 8), pady=4
            )
            return row + 1

        r = add_entry_row(opts, "检查近多久(分钟)", self.var_lookback, r)
        r = add_entry_row(opts, "检查并发数", self.var_workers, r)
        r = add_check_row(opts, "快速模式(监控配置查询)", self.var_no_search, r)

        ctk.CTkLabel(opts, text="抽检片段保存路径", width=label_w, anchor="w").grid(
            row=r, column=0, sticky="w", padx=(8, 8), pady=5
        )
        path_cell = ctk.CTkFrame(opts, fg_color="transparent")
        path_cell.grid(row=r, column=1, sticky="ew", padx=(0, 8), pady=5)
        path_cell.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(
            path_cell,
            textvariable=self.var_save_root,
            placeholder_text="选择或输入保存目录",
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(path_cell, text="浏览…", width=56, command=self._browse_save_root).grid(
            row=0, column=1, sticky="e"
        )
        r += 1

        r = add_entry_row(opts, "抽检秒数(≤12)", self.var_av_sec, r)
        r = add_entry_row(opts, "抽检并发(≤3)", self.var_av_workers, r)
        r = add_entry_row(opts, "抽检路数上限(0=全部)", self.var_av_limit, r)
        r = add_entry_row(opts, "繁忙时段开始时", self.var_busy_start, r)
        r = add_entry_row(opts, "繁忙时段结束时", self.var_busy_end, r)
        r = add_entry_row(opts, "静音阈值 dB", self.var_silence, r)
        # 底部留白，避免最后一项被下方「开始巡检」区域视觉挤压/裁切
        ctk.CTkFrame(opts, fg_color="transparent", height=16).grid(
            row=r, column=0, columnspan=2, sticky="ew"
        )

        # 巡检操作区（在扫描设置之后；展开设置时仍可向下滚动看到全部）
        actions = ctk.CTkFrame(left, fg_color="transparent")
        self._scan_actions_frame = actions
        actions.pack(fill="x", padx=4, pady=(14, 24))

        ctk.CTkLabel(
            actions,
            text="开始巡检",
            font=ctk.CTkFont(weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=2, pady=(0, 4))

        self.ffmpeg_label = ctk.CTkLabel(
            actions,
            text="",
            text_color="gray",
            wraplength=210,
            justify="left",
            anchor="w",
        )
        self.ffmpeg_label.pack(fill="x", padx=2, pady=(0, 8))

        # 按钮宽度约为通栏的 2/3（左对齐）；两按钮间距为原 8px 的 2 倍
        def _pack_scan_btn(parent: ctk.CTkFrame, text: str, command, pady) -> ctk.CTkButton:
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", pady=pady)
            row.grid_columnconfigure(0, weight=2)
            row.grid_columnconfigure(1, weight=1)
            btn = ctk.CTkButton(
                row,
                text=text,
                height=44,
                font=ctk.CTkFont(size=15, weight="bold"),
                command=command,
            )
            btn.grid(row=0, column=0, sticky="ew")
            return btn

        self.btn_scan_quick = _pack_scan_btn(
            actions,
            "快速巡检",
            lambda: self._start_scan(mode="quick"),
            pady=(0, 0),
        )
        self.btn_scan_deep = _pack_scan_btn(
            actions,
            "深度巡检",
            lambda: self._start_scan(mode="deep"),
            pady=(16, 6),  # 与上方按钮间隔 16（原 8 的 2 倍）
        )

        deep_opts_row = ctk.CTkFrame(actions, fg_color="transparent")
        deep_opts_row.pack(fill="x", pady=(0, 4))
        self.chk_av_save = ctk.CTkCheckBox(
            deep_opts_row,
            text="保存抽检片段（仅深度巡检）",
            variable=self.var_av_save,
        )
        self.chk_av_save.pack(side="left", padx=2)

        # 兼容旧代码引用
        self.btn_scan = self.btn_scan_quick

        # 底部辅助操作固定，避免滚出可视区
        bottom = ctk.CTkFrame(left_panel)
        bottom.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        bottom.grid_columnconfigure(0, weight=1)
        bottom.grid_columnconfigure(1, weight=1)
        bottom.grid_columnconfigure(2, weight=1)

        ctk.CTkButton(
            bottom, text="导出结果", height=34, command=self._export_result
        ).grid(row=0, column=0, sticky="ew", padx=(8, 4), pady=10)
        ctk.CTkButton(
            bottom, text="打开保存目录", height=34, command=self._open_save_dir
        ).grid(row=0, column=1, sticky="ew", padx=4, pady=10)
        ctk.CTkButton(
            bottom, text="打开配置目录", height=34, command=self._open_config_dir
        ).grid(row=0, column=2, sticky="ew", padx=(4, 8), pady=10)

        # --- 右侧结果：标题 + 指标卡片 + 表格 + 日志 ---
        right.grid_rowconfigure(0, weight=0)
        right.grid_rowconfigure(1, weight=0)
        right.grid_rowconfigure(2, weight=0)
        right.grid_rowconfigure(3, weight=1)
        right.grid_rowconfigure(4, weight=0)
        right.grid_columnconfigure(0, weight=1)

        head_right = ctk.CTkFrame(right, fg_color="transparent")
        head_right.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        head_right.grid_columnconfigure(0, weight=1)
        self.summary_label = ctk.CTkLabel(
            head_right,
            text="尚未扫描 — 配置设备后点击「快速巡检」或「深度巡检」",
            font=ctk.CTkFont(size=15, weight="bold"),
            anchor="w",
        )
        self.summary_label.grid(row=0, column=0, sticky="w")
        self.device_sub_label = ctk.CTkLabel(
            head_right, text="", text_color="gray", anchor="w", font=ctk.CTkFont(size=12)
        )
        self.device_sub_label.grid(row=1, column=0, sticky="w", pady=(2, 0))

        # 指标卡片行
        self.metrics_frame = ctk.CTkFrame(right, fg_color="transparent")
        self.metrics_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        for i in range(5):
            self.metrics_frame.grid_columnconfigure(i, weight=1)
        self._metric_cards: Dict[str, Dict[str, Any]] = {}
        for i, key in enumerate(("health", "online", "record", "disk", "audio")):
            card = ctk.CTkFrame(self.metrics_frame, corner_radius=10, height=72)
            card.grid(row=0, column=i, sticky="ew", padx=4, pady=2)
            card.grid_propagate(False)
            title = ctk.CTkLabel(card, text="—", font=ctk.CTkFont(size=11), text_color="gray")
            title.pack(anchor="w", padx=10, pady=(8, 0))
            value = ctk.CTkLabel(card, text="—", font=ctk.CTkFont(size=18, weight="bold"))
            value.pack(anchor="w", padx=10, pady=(2, 8))
            self._metric_cards[key] = {"frame": card, "title": title, "value": value}

        # 预警区（紧凑）
        self.warn_box = ctk.CTkTextbox(right, height=56, font=ctk.CTkFont(size=12))
        self.warn_box.grid(row=2, column=0, sticky="ew", padx=10, pady=(2, 4))
        self.warn_box.insert("end", "预警信息将显示在这里…\n")

        # 通道表格（Treeview + CTk 滚动条：触控板可用且外观统一）
        self.table_card = ctk.CTkFrame(right, corner_radius=12)
        self.table_card.grid(row=3, column=0, sticky="nsew", padx=8, pady=4)
        self.table_card.grid_rowconfigure(1, weight=1)
        self.table_card.grid_columnconfigure(0, weight=1)

        title_row = ctk.CTkFrame(self.table_card, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 6))
        title_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            title_row,
            text="通道明细",
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        title_right = ctk.CTkFrame(title_row, fg_color="transparent")
        title_right.grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(
            title_right,
            text="双指滑动 · Shift+滑动横向",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
            anchor="e",
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            title_right,
            text="放大查看",
            width=88,
            height=28,
            font=ctk.CTkFont(size=12),
            command=self._open_channel_detail_window,
        ).pack(side="left")
        self._channel_detail_win: Optional[ctk.CTkToplevel] = None
        self._channel_detail_tree: Optional[ttk.Treeview] = None
        self._last_channel_records: List[Dict[str, Any]] = []
        self._last_channel_deep: bool = False

        # 内嵌「表格画布」：圆角底 + 现代细滚动条
        dark = ctk.get_appearance_mode() == "Dark"
        table_shell_bg = ("#e8ecf1", "#1a1a1a")
        self._table_shell = ctk.CTkFrame(
            self.table_card,
            corner_radius=10,
            fg_color=table_shell_bg,
            border_width=1,
            border_color=("#d0d7de", "#3a3a3a"),
        )
        self._table_shell.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self._table_shell.grid_rowconfigure(0, weight=1)
        self._table_shell.grid_columnconfigure(0, weight=1)

        table_wrap = ctk.CTkFrame(self._table_shell, fg_color="transparent", corner_radius=0)
        table_wrap.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        table_wrap.grid_rowconfigure(0, weight=1)
        table_wrap.grid_columnconfigure(0, weight=1)

        self._headers_base = ("ch", "name", "online", "audio", "disk", "record")
        self._headers_deep = ("ch", "name", "online", "audio", "disk", "record", "vchk", "achk")
        self._tree_headings = {
            "ch": "通道",
            "name": "名称",
            "online": "在线",
            "audio": "含音频",
            "disk": "近期录像",
            "record": "录像",
            "vchk": "视频抽检",
            "achk": "音频抽检",
        }
        self._tree_widths = {
            "ch": 56,
            "name": 180,
            "online": 56,
            "audio": 64,
            "disk": 84,
            "record": 72,
            "vchk": 80,
            "achk": 80,
        }

        self._style_treeview()
        # 用原生 Frame 包一层，去掉 Treeview 系统边框的「复古」感
        tree_host = ctk.CTkFrame(
            table_wrap,
            fg_color=("#ffffff" if not dark else "#242424"),
            corner_radius=6,
        )
        tree_host.grid(row=0, column=0, sticky="nsew")
        tree_host.grid_rowconfigure(0, weight=1)
        tree_host.grid_columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            tree_host,
            columns=self._headers_base,
            show="headings",
            selectmode="browse",
            style="Channel.Treeview",
        )
        self._configure_tree_columns(deep=False)
        try:
            self.tree.configure(borderwidth=0, highlightthickness=0)
        except Exception:
            pass

        # CTk 细滚动条（替代系统 ttk 粗滚动条）
        sb_kwargs = dict(
            corner_radius=8,
            border_spacing=2,
            fg_color=("gray88", "gray22"),
            button_color=("gray65", "gray48"),
            button_hover_color=("gray50", "gray60"),
        )
        self._tree_ysb = ctk.CTkScrollbar(
            table_wrap,
            orientation="vertical",
            command=self.tree.yview,
            width=11,
            **sb_kwargs,
        )
        self._tree_xsb = ctk.CTkScrollbar(
            table_wrap,
            orientation="horizontal",
            command=self.tree.xview,
            height=11,
            **sb_kwargs,
        )
        self.tree.configure(
            yscrollcommand=self._tree_ysb.set,
            xscrollcommand=self._tree_xsb.set,
        )
        self.tree.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        self._tree_ysb.grid(row=0, column=1, sticky="ns", padx=(4, 0), pady=0)
        self._tree_xsb.grid(row=1, column=0, sticky="ew", padx=0, pady=(4, 0))
        # 右下角填色，避免滚动条交叉处空洞
        ctk.CTkFrame(table_wrap, fg_color="transparent", width=11, height=11).grid(
            row=1, column=1, sticky="nsew"
        )

        # 触控板/滚轮：在表格卡片范围内滚动 Treeview
        self._bind_tree_scroll()

        # 运行日志（时间戳 + 级别着色，更直观）
        log_card = ctk.CTkFrame(right, corner_radius=12)
        log_card.grid(row=4, column=0, sticky="ew", padx=8, pady=(4, 8))
        log_card.grid_columnconfigure(0, weight=1)

        log_header = ctk.CTkFrame(log_card, fg_color="transparent")
        log_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))
        log_header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            log_header,
            text="运行日志",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self._log_hint = ctk.CTkLabel(
            log_header,
            text="等待操作…",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
            anchor="w",
        )
        self._log_hint.grid(row=0, column=1, sticky="w", padx=(10, 0))
        ctk.CTkButton(
            log_header,
            text="复制",
            width=52,
            height=24,
            font=ctk.CTkFont(size=11),
            fg_color=("gray80", "gray30"),
            text_color=("gray10", "gray90"),
            hover_color=("gray70", "gray40"),
            command=self._copy_log,
        ).grid(row=0, column=2, padx=(4, 0))
        ctk.CTkButton(
            log_header,
            text="清空",
            width=52,
            height=24,
            font=ctk.CTkFont(size=11),
            fg_color=("gray80", "gray30"),
            text_color=("gray10", "gray90"),
            hover_color=("gray70", "gray40"),
            command=self._clear_log,
        ).grid(row=0, column=3, padx=(4, 0))

        # 图例：一眼看懂颜色含义
        legend = ctk.CTkFrame(log_card, fg_color="transparent")
        legend.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 2))
        for text, color in (
            ("步骤", ("#0969da", "#58a6ff")),
            ("成功", ("#1a7f37", "#6dcf7a")),
            ("警告", ("#b26a00", "#fcb24a")),
            ("错误", ("#c62828", "#ff6b6b")),
        ):
            ctk.CTkLabel(
                legend,
                text=f"● {text}",
                font=ctk.CTkFont(size=10),
                text_color=color,
            ).pack(side="left", padx=(0, 10))

        # 等宽字体便于对齐时间戳；不可用时回退默认
        if sys.platform == "darwin":
            mono = ctk.CTkFont(family="Menlo", size=11)
        elif sys.platform == "win32":
            mono = ctk.CTkFont(family="Consolas", size=11)
        else:
            mono = ctk.CTkFont(family="DejaVu Sans Mono", size=11)

        self.log_box = ctk.CTkTextbox(
            log_card,
            height=108,
            font=mono,
            fg_color=("#f6f8fa", "#1a1a1a"),
            border_width=1,
            border_color=("#d0d7de", "#333333"),
            corner_radius=8,
            activate_scrollbars=True,
        )
        self.log_box.grid(row=2, column=0, sticky="ew", padx=8, pady=(2, 8))
        self._setup_log_tags()
        self._log_line_count = 0
        self._log("就绪。配置设备后点击「快速巡检」或「深度巡检」。", level="info")

        self.summary_box = self.warn_box

        # 底部状态栏：状态点 + 文案 + 详情 + 进度条
        status = ctk.CTkFrame(self, corner_radius=10, height=44)
        status.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))
        status.grid_columnconfigure(2, weight=1)

        self._status_dot = ctk.CTkLabel(
            status, text="●", width=22, font=ctk.CTkFont(size=14), anchor="center"
        )
        self._status_dot.grid(row=0, column=0, padx=(12, 2), pady=8, sticky="w")

        self.status_var = ctk.StringVar(value="就绪")
        self._status_label = ctk.CTkLabel(
            status,
            textvariable=self.status_var,
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
            width=88,
        )
        self._status_label.grid(row=0, column=1, padx=(0, 8), pady=8, sticky="w")

        self._status_detail_var = ctk.StringVar(value="等待开始巡检")
        self._status_detail = ctk.CTkLabel(
            status,
            textvariable=self._status_detail_var,
            font=ctk.CTkFont(size=12),
            text_color=("gray45", "gray65"),
            anchor="w",
        )
        self._status_detail.grid(row=0, column=2, padx=4, pady=8, sticky="ew")

        self._progress_pct_var = ctk.StringVar(value="")
        self._progress_pct = ctk.CTkLabel(
            status,
            textvariable=self._progress_pct_var,
            font=ctk.CTkFont(size=11),
            width=42,
            anchor="e",
            text_color=("gray45", "gray65"),
        )
        self._progress_pct.grid(row=0, column=3, padx=(4, 2), pady=8, sticky="e")

        self.progress = ctk.CTkProgressBar(
            status,
            mode="determinate",
            width=200,
            height=12,
            corner_radius=6,
            border_width=0,
            progress_color=("#3b82f6", "#3b82f6"),
            fg_color=("#e5e7eb", "#333333"),
        )
        self.progress.grid(row=0, column=4, padx=(4, 14), pady=8, sticky="e")
        self.progress.set(0)
        self._progress_running = False
        self._set_app_status("ready")

    def _style_treeview(self) -> None:
        """现代化 Treeview：无边框、圆角卡片内嵌、表头扁平。"""
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        mode = ctk.get_appearance_mode()
        if mode == "Dark":
            bg, fg = "#242424", "#e6e6e6"
            head_bg, head_fg = "#1c1c1c", "#c8c8c8"
            sel, field = "#2d6ea8", "#242424"
            border = "#242424"
        else:
            bg, fg = "#ffffff", "#1f2328"
            head_bg, head_fg = "#f0f3f7", "#424a53"
            sel, field = "#0969da", "#ffffff"
            border = "#ffffff"
        # 表头/单元格字号较默认小 2 号
        font = ("PingFang SC", 10) if sys.platform == "darwin" else ("Microsoft YaHei UI", 8)
        head_font = (font[0], font[1], "bold")

        # 去掉 Treeview 外围边框元素，更干净
        try:
            style.layout(
                "Channel.Treeview",
                [("Channel.Treeview.treearea", {"sticky": "nswe"})],
            )
        except Exception:
            pass

        style.configure(
            "Channel.Treeview",
            background=bg,
            foreground=fg,
            fieldbackground=field,
            rowheight=26,
            borderwidth=0,
            relief="flat",
            font=font,
        )
        style.configure(
            "Channel.Treeview.Heading",
            background=head_bg,
            foreground=head_fg,
            relief="flat",
            borderwidth=0,
            font=head_font,
            padding=(6, 6),
        )
        style.map(
            "Channel.Treeview",
            background=[("selected", sel)],
            foreground=[("selected", "#ffffff")],
        )
        style.map(
            "Channel.Treeview.Heading",
            relief=[("active", "flat"), ("pressed", "flat")],
            background=[("active", head_bg), ("pressed", head_bg)],
            foreground=[("active", head_fg)],
        )
        # 隐藏 clam 主题默认的 focus 边框色
        try:
            style.configure("Channel.Treeview", lightcolor=border, darkcolor=border, bordercolor=border)
            style.configure(
                "Channel.Treeview.Heading",
                lightcolor=head_bg,
                darkcolor=head_bg,
                bordercolor=head_bg,
            )
        except Exception:
            pass

    def _configure_tree_columns(self, deep: bool) -> None:
        cols = self._headers_deep if deep else self._headers_base
        self.tree.configure(columns=cols, style="Channel.Treeview")
        for col in cols:
            self.tree.heading(col, text=self._tree_headings[col], anchor="center")
            self.tree.column(
                col,
                width=self._tree_widths[col],
                minwidth=48,
                anchor="w" if col == "name" else "center",
                stretch=(col == "name"),
            )

    def _open_channel_detail_window(self) -> None:
        """打开放大窗口查看通道明细。"""
        win = self._channel_detail_win
        if win is not None:
            try:
                if win.winfo_exists():
                    win.lift()
                    win.focus_force()
                    self._fill_channel_detail_tree()
                    return
            except Exception:
                self._channel_detail_win = None
                self._channel_detail_tree = None

        win = ctk.CTkToplevel(self)
        win.title("通道明细 · 放大查看")
        win.geometry("1100x680")
        win.minsize(720, 420)
        try:
            win.transient(self)
        except Exception:
            pass
        self._channel_detail_win = win

        win.grid_rowconfigure(1, weight=1)
        win.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(win, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            head,
            text="通道明细",
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            head,
            text="关闭",
            width=72,
            height=30,
            command=lambda: self._close_channel_detail_window(),
        ).grid(row=0, column=1, sticky="e")

        body = ctk.CTkFrame(win, corner_radius=10)
        body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)

        tree_wrap = ctk.CTkFrame(body, fg_color="transparent")
        tree_wrap.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        tree_wrap.grid_rowconfigure(0, weight=1)
        tree_wrap.grid_columnconfigure(0, weight=1)

        self._style_treeview()
        tree = ttk.Treeview(
            tree_wrap,
            columns=self._headers_base,
            show="headings",
            selectmode="browse",
            style="Channel.Treeview",
        )
        try:
            tree.configure(borderwidth=0, highlightthickness=0)
        except Exception:
            pass
        ysb = ctk.CTkScrollbar(tree_wrap, orientation="vertical", command=tree.yview, width=12)
        xsb = ctk.CTkScrollbar(tree_wrap, orientation="horizontal", command=tree.xview, height=12)
        tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        xsb.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        self._channel_detail_tree = tree
        win.protocol("WM_DELETE_WINDOW", self._close_channel_detail_window)
        self._fill_channel_detail_tree()
        try:
            win.after(80, win.lift)
        except Exception:
            pass

    def _close_channel_detail_window(self) -> None:
        win = self._channel_detail_win
        self._channel_detail_win = None
        self._channel_detail_tree = None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass

    def _fill_channel_detail_tree(self) -> None:
        """把最近一次巡检的通道数据填入放大窗口。"""
        tree = self._channel_detail_tree
        win = self._channel_detail_win
        if tree is None or win is None:
            return
        try:
            if not win.winfo_exists():
                return
        except Exception:
            return

        deep = bool(self._last_channel_deep)
        records = self._last_channel_records or []
        cols = self._headers_deep if deep else self._headers_base
        # 放大窗口列更宽，便于阅读
        scale = 1.45
        tree.configure(columns=cols, style="Channel.Treeview")
        for col in cols:
            tree.heading(col, text=self._tree_headings[col], anchor="center")
            base_w = self._tree_widths[col]
            tree.column(
                col,
                width=max(64, int(base_w * scale)),
                minwidth=56,
                anchor="w" if col == "name" else "center",
                stretch=(col == "name"),
            )

        for item in tree.get_children():
            tree.delete(item)

        dark = ctk.get_appearance_mode() == "Dark"
        tree.tag_configure("ok", foreground="#6dcf7a" if dark else "#1a7f37")
        tree.tag_configure("bad", foreground="#ff6b6b" if dark else "#c62828")
        tree.tag_configure("warn", foreground="#fcb24a" if dark else "#b26a00")
        tree.tag_configure("muted", foreground="#999999")
        tree.tag_configure("odd", background="#2e2e2e" if dark else "#f6f8fa")
        tree.tag_configure("even", background="#242424" if dark else "#ffffff")

        if not records:
            tree.insert(
                "",
                "end",
                values=tuple(["—"] * len(cols)),
                tags=("muted", "even"),
            )
            return

        for i, r in enumerate(records):
            values = [
                str(r.get("通道", "")),
                str(r.get("名称") or "—"),
                self._fmt_online(r.get("在线")),
                self._fmt_bool_audio(r.get("录像含音频")),
                self._fmt_status(r.get("落盘状态")),
                self._fmt_status(r.get("录像是否正常")),
            ]
            if deep:
                values += [
                    self._fmt_status(r.get("视频抽检")),
                    self._fmt_status(r.get("音频抽检")),
                ]
            tag = self._row_tag(r, deep)
            stripe = "odd" if i % 2 else "even"
            tree.insert("", "end", values=values, tags=(tag, stripe))

    def _pointer_in_widget(self, widget) -> bool:
        """指针是否在指定控件矩形内。"""
        try:
            if widget is None:
                return False
            px, py = self.winfo_pointerxy()
            x, y = widget.winfo_rootx(), widget.winfo_rooty()
            w, h = widget.winfo_width(), widget.winfo_height()
            return w > 1 and h > 1 and x <= px <= x + w and y <= py <= y + h
        except Exception:
            return False

    def _pointer_over_table(self) -> bool:
        """指针是否在通道表格卡片内。"""
        return self._pointer_in_widget(getattr(self, "table_card", None))

    def _pointer_over_left_panel(self) -> bool:
        """指针是否在左侧「设备与扫描设置」区域。"""
        return self._pointer_in_widget(
            getattr(self, "left_panel", None)
        ) or self._pointer_in_widget(getattr(self, "left_scroll", None))

    def _left_canvas(self):
        """CTkScrollableFrame 内部画布。"""
        left = getattr(self, "left_scroll", None)
        if left is None:
            return None
        for attr in ("_parent_canvas", "_canvas"):
            c = getattr(left, attr, None)
            if c is not None:
                return c
        return None

    def _bind_tree_scroll(self) -> None:
        """macOS 双指滑动 / 滚轮：左侧面板与通道表格分区滚动。"""

        def _units(event) -> int:
            if getattr(event, "num", None) == 4:
                return -3
            if getattr(event, "num", None) == 5:
                return 3
            if hasattr(event, "delta") and event.delta:
                if sys.platform == "darwin":
                    # macOS 触控板 delta 较小，放大步长
                    d = int(-1 * event.delta)
                    if d == 0:
                        d = -1 if event.delta > 0 else 1
                    return d
                return int(-1 * (event.delta / 120)) or (-1 if event.delta > 0 else 1)
            return 0

        def _is_shift(event) -> bool:
            try:
                return bool(int(getattr(event, "state", 0)) & 0x1)
            except Exception:
                return False

        def _scroll_left(event) -> Optional[str]:
            canvas = self._left_canvas()
            if canvas is None:
                return None
            try:
                u = _units(event)
                if u:
                    canvas.yview_scroll(u, "units")
                    return "break"
            except Exception:
                return None
            return None

        def _scroll_y(event) -> Optional[str]:
            # 通道表优先
            if self._pointer_over_table():
                if _is_shift(event):
                    return _scroll_x(event)
                try:
                    u = _units(event)
                    if u:
                        self.tree.yview_scroll(u, "units")
                        return "break"
                except Exception:
                    return None
                return None
            # 左侧设置区双指上下滑动
            if self._pointer_over_left_panel():
                return _scroll_left(event)
            return None

        def _scroll_x(event) -> Optional[str]:
            if not self._pointer_over_table():
                return None
            try:
                u = _units(event)
                if u:
                    self.tree.xview_scroll(u, "units")
                    return "break"
            except Exception:
                return None
            return None

        targets = [self.tree, self._tree_ysb, self._tree_xsb, self.table_card]
        if hasattr(self, "_table_shell"):
            targets.append(self._table_shell)
        left = getattr(self, "left_scroll", None)
        if left is not None:
            targets.append(left)
            canvas = self._left_canvas()
            if canvas is not None:
                targets.append(canvas)
        if getattr(self, "left_panel", None) is not None:
            targets.append(self.left_panel)

        for w in targets:
            try:
                w.bind("<MouseWheel>", _scroll_y, add="+")
                w.bind("<Shift-MouseWheel>", _scroll_x, add="+")
                w.bind("<Button-4>", _scroll_y, add="+")
                w.bind("<Button-5>", _scroll_y, add="+")
                w.bind("<Shift-Button-4>", _scroll_x, add="+")
                w.bind("<Shift-Button-5>", _scroll_x, add="+")
            except Exception:
                pass
        # 全局兜底：按指针位置路由到表格或左侧滚动区
        self.bind_all("<MouseWheel>", _scroll_y, add="+")
        self.bind_all("<Shift-MouseWheel>", _scroll_x, add="+")
        self.bind_all("<Button-4>", _scroll_y, add="+")
        self.bind_all("<Button-5>", _scroll_y, add="+")

        def _focus(_e=None):
            try:
                self.tree.focus_set()
            except Exception:
                pass

        self.tree.bind("<Enter>", _focus, add="+")
        self.tree.bind("<Button-1>", _focus, add="+")

    def _set_metric(self, key: str, title: str, value: str, tone: str = "normal") -> None:
        card = self._metric_cards.get(key)
        if not card:
            return
        card["title"].configure(text=title)
        colors = {
            "normal": ("#1a1a1a", "#eaeaea"),
            "ok": ("#1a7f37", "#6dcf7a"),
            "warn": ("#b26a00", "#fcb24a"),
            "bad": ("#c62828", "#ff6b6b"),
            "muted": ("#666666", "#999999"),
        }
        fg = colors.get(tone, colors["normal"])
        card["value"].configure(text=value, text_color=fg)

    # ---------- 设备表单 ----------

    def _clear_device_rows(self) -> None:
        for w in self.dev_frame.winfo_children():
            w.destroy()
        self._device_rows.clear()

    @staticmethod
    def _normalize_device(dev: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        base = _empty_device()
        if dev:
            base.update(dev)
        try:
            base["port"] = int(base.get("port") or 80)
        except (TypeError, ValueError):
            base["port"] = 80
        base["name"] = str(base.get("name") or "NVR").strip() or "NVR"
        base["ip"] = str(base.get("ip") or "").strip()
        base["username"] = str(base.get("username") or "admin").strip() or "admin"
        base["password"] = str(base.get("password") or "")
        base["ssl"] = bool(base.get("ssl"))
        return base

    def _add_device_row(self, dev: Optional[Dict[str, Any]] = None) -> None:
        """列表行仅展示名称 + IP，完整配置存在 row['device']。"""
        device = self._normalize_device(dev)
        row = ctk.CTkFrame(self.dev_frame)
        row.pack(fill="x", pady=2)

        selected = ctk.BooleanVar(value=False)
        name_lbl = ctk.CTkLabel(row, text=device["name"], anchor="w", width=88)
        ip_lbl = ctk.CTkLabel(row, text=device["ip"] or "—", anchor="w")

        row_data: Dict[str, Any] = {
            "frame": row,
            "selected": selected,
            "device": device,
            "name_lbl": name_lbl,
            "ip_lbl": ip_lbl,
        }

        ctk.CTkCheckBox(row, text="", variable=selected, width=24).grid(
            row=0, column=0, padx=(4, 2), pady=4, sticky="w"
        )
        name_lbl.grid(row=0, column=1, padx=2, pady=4, sticky="w")
        ip_lbl.grid(row=0, column=2, padx=2, pady=4, sticky="ew")
        ctk.CTkButton(
            row,
            text="编辑",
            width=48,
            height=26,
            font=ctk.CTkFont(size=12),
            command=lambda r=row_data: self._edit_device(r),
        ).grid(row=0, column=3, padx=(4, 6), pady=4, sticky="e")
        row.grid_columnconfigure(2, weight=1)

        self._device_rows.append(row_data)
        self._refresh_scan_device_menu()

    def _refresh_device_row_labels(self, row_data: Dict[str, Any]) -> None:
        d = row_data["device"]
        row_data["name_lbl"].configure(text=d.get("name") or "NVR")
        row_data["ip_lbl"].configure(text=d.get("ip") or "—")

    def _add_device(self) -> None:
        n = len(self._device_rows) + 1
        d = self._normalize_device(_empty_device())
        d["name"] = f"NVR{n}"
        d["ip"] = ""
        self._open_device_dialog(device=d, on_save=self._on_device_dialog_add)

    def _edit_device(self, row_data: Dict[str, Any]) -> None:
        self._open_device_dialog(
            device=dict(row_data["device"]),
            on_save=lambda dev, r=row_data: self._on_device_dialog_edit(r, dev),
            title="编辑设备",
        )

    def _on_device_dialog_add(self, device: Dict[str, Any]) -> None:
        self._add_device_row(device)
        self._log(f"已添加设备：{device.get('name')} ({device.get('ip')})")

    def _on_device_dialog_edit(self, row_data: Dict[str, Any], device: Dict[str, Any]) -> None:
        row_data["device"] = self._normalize_device(device)
        self._refresh_device_row_labels(row_data)
        self._refresh_scan_device_menu()
        self._log(f"已更新设备：{device.get('name')} ({device.get('ip')})")

    def _open_device_dialog(
        self,
        device: Optional[Dict[str, Any]] = None,
        on_save: Optional[Any] = None,
        title: str = "添加设备",
    ) -> None:
        """独立窗口填写/修改设备信息。"""
        dev = self._normalize_device(device)

        win = ctk.CTkToplevel(self)
        win.title(title)
        win.geometry("420x380")
        win.minsize(380, 340)
        win.resizable(False, False)
        try:
            win.transient(self)
            win.grab_set()
        except Exception:
            pass

        win.grid_columnconfigure(0, weight=1)

        form = ctk.CTkFrame(win, fg_color="transparent")
        form.grid(row=0, column=0, sticky="nsew", padx=18, pady=(16, 8))
        form.grid_columnconfigure(1, weight=1)

        var_name = ctk.StringVar(value=str(dev.get("name") or ""))
        var_ip = ctk.StringVar(value=str(dev.get("ip") or ""))
        var_port = ctk.StringVar(value=str(dev.get("port") or 80))
        var_user = ctk.StringVar(value=str(dev.get("username") or "admin"))
        var_pass = ctk.StringVar(value=str(dev.get("password") or ""))
        var_ssl = ctk.BooleanVar(value=bool(dev.get("ssl")))

        fields = [
            ("名称", var_name, False),
            ("IP 地址", var_ip, False),
            ("端口", var_port, False),
            ("用户名", var_user, False),
            ("密码", var_pass, True),
        ]
        for i, (label, var, is_pwd) in enumerate(fields):
            ctk.CTkLabel(form, text=label, width=72, anchor="w").grid(
                row=i, column=0, sticky="w", padx=(0, 10), pady=8
            )
            entry = ctk.CTkEntry(form, textvariable=var, height=32)
            if is_pwd:
                entry.configure(show="*")
            entry.grid(row=i, column=1, sticky="ew", pady=8)

        ctk.CTkCheckBox(form, text="使用 HTTPS (SSL)", variable=var_ssl).grid(
            row=len(fields), column=0, columnspan=2, sticky="w", pady=(4, 8)
        )

        btn_bar = ctk.CTkFrame(win, fg_color="transparent")
        btn_bar.grid(row=1, column=0, sticky="ew", padx=18, pady=(8, 16))
        btn_bar.grid_columnconfigure(0, weight=1)
        btn_bar.grid_columnconfigure(1, weight=1)

        def _close() -> None:
            try:
                win.grab_release()
            except Exception:
                pass
            win.destroy()

        def _confirm() -> None:
            name = var_name.get().strip() or "NVR"
            ip = var_ip.get().strip()
            if not ip:
                messagebox.showerror("错误", "IP 地址不能为空", parent=win)
                return
            try:
                port = int(var_port.get().strip() or "80")
            except ValueError:
                messagebox.showerror("错误", "端口必须是数字", parent=win)
                return
            if port < 1 or port > 65535:
                messagebox.showerror("错误", "端口范围应为 1–65535", parent=win)
                return
            result = self._normalize_device(
                {
                    "name": name,
                    "ip": ip,
                    "port": port,
                    "username": var_user.get().strip() or "admin",
                    "password": var_pass.get(),
                    "ssl": bool(var_ssl.get()),
                }
            )
            if on_save:
                on_save(result)
            _close()

        ctk.CTkButton(btn_bar, text="取消", height=34, fg_color="gray40", command=_close).grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )
        ctk.CTkButton(btn_bar, text="确定", height=34, command=_confirm).grid(
            row=0, column=1, sticky="ew", padx=(6, 0)
        )

        win.protocol("WM_DELETE_WINDOW", _close)
        try:
            win.after(50, win.lift)
            win.after(80, lambda: win.focus_force())
        except Exception:
            pass

    def _del_device(self) -> None:
        keep = []
        for r in self._device_rows:
            if r["selected"].get():
                r["frame"].destroy()
            else:
                keep.append(r)
        if len(keep) == len(self._device_rows):
            messagebox.showinfo("提示", "请先勾选要删除的设备")
            return
        if not keep:
            messagebox.showwarning("提示", "至少保留一台设备")
            self._add_device_row()
            return
        self._device_rows = keep
        self._refresh_scan_device_menu()

    def _collect_devices(self) -> List[Dict[str, Any]]:
        devices = []
        for r in self._device_rows:
            devices.append(self._normalize_device(r.get("device")))
        return devices

    def _refresh_scan_device_menu(self) -> None:
        devices = self._collect_devices()
        labels = [f"{i+1}. {d['name']} ({d['ip']})" for i, d in enumerate(devices)] or ["-"]
        cur = self.scan_device_var.get()
        self.scan_device_menu.configure(values=labels)
        if cur in labels:
            self.scan_device_var.set(cur)
        else:
            self.scan_device_var.set(labels[0])

    # ---------- 档案 ----------

    def _reload_profiles_ui(self) -> None:
        names = self.store.list_profiles() or ["默认"]
        self.profile_menu.configure(values=names)
        active = self.store.get_active_name()
        if active not in names:
            active = names[0]
        self.profile_var.set(active)

    def _on_profile_change(self, name: str) -> None:
        if name and name in self.store.list_profiles():
            self.store.set_active(name)
            self._load_active_profile_to_form()
            self._log(f"已切换配置档案: {name}")

    def _load_active_profile_to_form(self) -> None:
        prof = self.store.get_profile()
        self._clear_device_rows()
        for d in prof.get("devices") or [_empty_device()]:
            self._add_device_row(d)
        opt = prof.get("scan_options") or {}
        self.var_lookback.set(str(opt.get("lookback", 60)))
        self.var_workers.set(str(opt.get("workers", 8)))
        self.var_no_search.set(bool(opt.get("no_search")))
        self.var_deep.set(bool(opt.get("deep_av_check")))
        self.var_av_save.set(bool(opt.get("av_save")))
        self.var_av_sec.set(str(opt.get("av_seconds", 6)))
        self.var_av_workers.set(str(opt.get("av_workers", 2)))
        self.var_av_limit.set(str(opt.get("av_limit", 0)))
        self.var_busy_start.set(str(opt.get("busy_start", 10)))
        self.var_busy_end.set(str(opt.get("busy_end", 18)))
        self.var_silence.set(str(opt.get("silence_db", -80)))
        root = (opt.get("av_save_root") or "").strip() or default_av_save_root()
        self.var_save_root.set(root)
        # 默认扫描设备
        idx = int(prof.get("default") or 0)
        devices = self._collect_devices()
        labels = [f"{i+1}. {d['name']} ({d['ip']})" for i, d in enumerate(devices)]
        if labels:
            self.scan_device_menu.configure(values=labels)
            self.scan_device_var.set(labels[min(idx, len(labels) - 1)])

    def _form_to_profile_dict(self) -> Dict[str, Any]:
        devices = self._collect_devices()
        # 当前选中的扫描设备索引
        default = 0
        labels = [f"{i+1}. {d['name']} ({d['ip']})" for i, d in enumerate(devices)]
        cur = self.scan_device_var.get()
        if cur in labels:
            default = labels.index(cur)
        try:
            av_limit = int(self.var_av_limit.get() or 0)
        except ValueError:
            av_limit = 0
        opt = {
            "lookback": int(self.var_lookback.get() or 60),
            "no_search": bool(self.var_no_search.get()),
            "workers": int(self.var_workers.get() or 8),
            # deep_av_check 由「快速/深度巡检」按钮决定；档案只记最近一次偏好
            "deep_av_check": bool(self.var_deep.get()),
            "av_seconds": int(self.var_av_sec.get() or 6),
            "av_workers": int(self.var_av_workers.get() or 2),
            "av_limit": av_limit,
            "silence_db": float(self.var_silence.get() or -80),
            "busy_start": int(self.var_busy_start.get() or 10),
            "busy_end": int(self.var_busy_end.get() or 18),
            "av_save": bool(self.var_av_save.get()),
            "av_save_root": self.var_save_root.get().strip(),
        }
        return {
            "name": self.store.get_active_name(),
            "devices": devices,
            "default": default,
            "scan_options": opt,
        }

    def _save_form_to_profile(self) -> None:
        try:
            prof = self._form_to_profile_dict()
            for d in prof["devices"]:
                if not d.get("ip"):
                    messagebox.showerror("错误", "设备 IP 不能为空")
                    return
            self.store.update_profile(None, prof)
            self._log(f"已保存档案「{self.store.get_active_name()}」")
            messagebox.showinfo("保存成功", f"配置档案「{self.store.get_active_name()}」已保存")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def _profile_new(self) -> None:
        dialog = ctk.CTkInputDialog(text="新档案名称:", title="新建配置档案")
        name = dialog.get_input()
        if not name:
            return
        created = self.store.create_profile(name, clone_from=None)
        self._reload_profiles_ui()
        self.profile_var.set(created)
        self._load_active_profile_to_form()
        self._log(f"已新建档案: {created}")

    def _profile_save_as(self) -> None:
        dialog = ctk.CTkInputDialog(text="另存为档案名称:", title="另存配置")
        name = dialog.get_input()
        if not name:
            return
        # 先把当前表单写入新档案
        self._save_form_to_profile()
        created = self.store.create_profile(name, clone_from=self.store.get_active_name())
        # 再写一次表单到新档案
        self.store.set_active(created)
        self._reload_profiles_ui()
        self.profile_var.set(created)
        self._save_form_to_profile()
        self._log(f"已另存为: {created}")

    def _profile_rename(self) -> None:
        old = self.store.get_active_name()
        dialog = ctk.CTkInputDialog(text=f"将「{old}」重命名为:", title="重命名")
        new = dialog.get_input()
        if not new:
            return
        if self.store.rename_profile(old, new):
            self._reload_profiles_ui()
            self.profile_var.set(new)
            self._log(f"已重命名: {old} → {new}")
        else:
            messagebox.showerror("失败", "重命名失败（名称冲突或非法）")

    def _profile_delete(self) -> None:
        name = self.store.get_active_name()
        if not messagebox.askyesno("确认", f"删除配置档案「{name}」？"):
            return
        if self.store.delete_profile(name):
            self._reload_profiles_ui()
            self._load_active_profile_to_form()
            self._log(f"已删除档案: {name}")
        else:
            messagebox.showerror("失败", "无法删除（至少保留一个档案）")

    def _profile_export(self) -> None:
        name = self.store.get_active_name()
        path = filedialog.asksaveasfilename(
            title="导出配置",
            defaultextension=".json",
            initialfile=f"{name}.json",
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        self._save_form_to_profile()
        self.store.export_profile(name, path)
        self._log(f"已导出: {path}")

    def _profile_import(self) -> None:
        path = filedialog.askopenfilename(
            title="导入配置",
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            name = self.store.import_profile(path)
            self._reload_profiles_ui()
            self.profile_var.set(name)
            self._load_active_profile_to_form()
            self._log(f"已导入档案: {name}")
        except Exception as e:
            messagebox.showerror("导入失败", str(e))

    # ---------- 扫描 ----------

    def _check_ffmpeg(self) -> None:
        tools = _which_tools()
        ff = tools.get("ffmpeg")
        fp = tools.get("ffprobe")
        if ff and fp:
            self.ffmpeg_label.configure(
                text="ffmpeg: 已就绪",
                text_color=("green", "#6f6"),
            )
        else:
            self.ffmpeg_label.configure(
                text="未检测到 ffmpeg/ffprobe：深度抽检与保存片段不可用。\n"
                "可将二进制放到应用目录 bin/ 下，或安装到系统 PATH。",
                text_color=("#a60", "#fa0"),
            )

    def _toggle_scan_settings(self) -> None:
        """侧栏内展开/折叠扫描设置。"""
        self._scan_settings_expanded = not self._scan_settings_expanded
        self._apply_scan_settings_visibility()

    def _scroll_left_to_settings(self) -> None:
        """展开后滚动左侧区域，尽量让设置底部（含静音阈值）进入可视区。"""
        canvas = self._left_canvas()
        frame = getattr(self, "scan_settings_frame", None)
        if canvas is None or frame is None:
            return
        try:
            # 先更新布局，再滚动到设置面板底部附近
            self.update_idletasks()
            canvas.update_idletasks()
            # 滚到设置区域：用 bbox 估算相对位置
            try:
                # 将设置框底边滚进可视区
                y = frame.winfo_y() + frame.winfo_height()
                # 相对 scrollable 内容高度
                total = max(canvas.bbox("all")[3], 1)
                # 目标：设置底边略高于视口底部
                view_h = max(canvas.winfo_height(), 1)
                top = max(0.0, min(1.0, (y - view_h + 24) / total))
                canvas.yview_moveto(top)
            except Exception:
                canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _apply_scan_settings_visibility(self) -> None:
        if self._scan_settings_expanded:
            # 插在按钮与巡检操作区之间
            kwargs = dict(fill="x", padx=4, pady=(0, 12))
            actions = getattr(self, "_scan_actions_frame", None)
            if actions is not None:
                self.scan_settings_frame.pack(before=actions, **kwargs)
            else:
                self.scan_settings_frame.pack(
                    after=self.btn_toggle_scan_settings, **kwargs
                )
            self.btn_toggle_scan_settings.configure(text="▼  扫描设置")
            # 展开后自动滚一下，避免静音阈值被视口裁切
            self.after(30, self._scroll_left_to_settings)
            self.after(120, self._scroll_left_to_settings)
        else:
            self.scan_settings_frame.pack_forget()
            self.btn_toggle_scan_settings.configure(text="▶  扫描设置")

    def _set_scan_buttons_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.btn_scan_quick.configure(state=state)
        self.btn_scan_deep.configure(state=state)

    def _browse_save_root(self) -> None:
        current = self.var_save_root.get().strip() or default_av_save_root()
        if os.path.isdir(current):
            initial = current
        else:
            parent = os.path.dirname(current)
            initial = parent if parent and os.path.isdir(parent) else os.path.expanduser("~")
        path = filedialog.askdirectory(
            title="选择抽检片段保存目录",
            initialdir=initial,
        )
        if path:
            self.var_save_root.set(path)
            # 路径变更同步进当前档案（不弹窗）
            try:
                self.store.update_profile(None, self._form_to_profile_dict())
                self._log(f"抽检片段保存路径已设为：{path}")
            except Exception:
                pass

    def _open_save_dir(self) -> None:
        path = self.var_save_root.get().strip() or default_av_save_root()
        os.makedirs(path, exist_ok=True)
        self._open_path(path)

    def _open_config_dir(self) -> None:
        self._open_path(app_data_dir())

    def _open_path(self, path: str) -> None:
        try:
            if sys.platform == "darwin":
                os.system(f'open "{path}"')
            elif sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                os.system(f'xdg-open "{path}"')
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    def _setup_log_tags(self) -> None:
        """日志着色：时间 / 级别前缀 / 正文。"""
        dark = ctk.get_appearance_mode() == "Dark"
        box = self.log_box
        box.tag_config("time", foreground="#8b949e" if dark else "#6e7781")
        box.tag_config("sep", foreground="#484f58" if dark else "#afb8c1")
        box.tag_config(
            "info",
            foreground="#c9d1d9" if dark else "#24292f",
        )
        box.tag_config("step", foreground="#58a6ff" if dark else "#0969da")
        box.tag_config("ok", foreground="#6dcf7a" if dark else "#1a7f37")
        box.tag_config("warn", foreground="#fcb24a" if dark else "#b26a00")
        box.tag_config("error", foreground="#ff6b6b" if dark else "#c62828")
        box.tag_config("muted", foreground="#8b949e" if dark else "#6e7781")

    @staticmethod
    def _detect_log_level(msg: str) -> str:
        s = (msg or "").strip()
        low = s.lower()
        if (
            s.startswith("错误")
            or "失败" in s
            or "traceback" in low
            or "exception" in low
            or "无法" in s
        ):
            return "error"
        if (
            "警告" in s
            or "跳过" in s
            or "未找到" in s
            or "缺少" in s
            or "密码为空" in s
        ):
            return "warn"
        if (
            s.startswith("——")
            or s.startswith("连接")
            or "开始巡检" in s
            or "快速巡检" in s
            or "深度巡检" in s
            or "开始录像" in s
            or "摄像头" in s and "路" in s
            or "优先时段" in s
            or "深度抽检" in s
        ):
            return "step"
        if (
            "完成" in s
            or s.startswith("已")
            or "正常" in s and "异常" not in s
            or "设备:" in s
        ):
            return "ok"
        if s.startswith("  ") or s.startswith("通道"):
            return "muted"
        return "info"

    _LEVEL_MARK = {
        "info": ("·", "info"),
        "step": ("▸", "step"),
        "ok": ("✓", "ok"),
        "warn": ("!", "warn"),
        "error": ("✕", "error"),
        "muted": ("·", "muted"),
    }

    def _log(self, msg: str, level: Optional[str] = None) -> None:
        """写入一条日志：时间戳 + 级别标记 + 正文（着色）。"""
        text = str(msg).rstrip("\n")
        if not text:
            return
        level = level or self._detect_log_level(text)
        mark, tag = self._LEVEL_MARK.get(level, ("·", "info"))
        ts = datetime.now().strftime("%H:%M:%S")

        # 分隔线：单独一行，弱化显示
        if text.startswith("——") and text.endswith("——"):
            self.log_box.insert("end", f"\n{ts}  ", ("time",))
            self.log_box.insert("end", f"{'─' * 8} ", ("sep",))
            body = text.strip("— ").strip()
            self.log_box.insert("end", f"{body}\n", ("step",))
        else:
            self.log_box.insert("end", f"{ts}  ", ("time",))
            self.log_box.insert("end", f"{mark} ", (tag,))
            self.log_box.insert("end", f"{text}\n", (tag,))

        self.log_box.see("end")
        self._log_line_count = getattr(self, "_log_line_count", 0) + 1
        # 顶部摘要：最近一条 + 条数
        short = text if len(text) <= 42 else text[:40] + "…"
        try:
            self._log_hint.configure(text=f"最近 · {short}   （共 {self._log_line_count} 条）")
        except Exception:
            pass

    def _clear_log(self) -> None:
        self.log_box.delete("1.0", "end")
        self._log_line_count = 0
        self._log("日志已清空。", level="muted")

    def _copy_log(self) -> None:
        try:
            content = self.log_box.get("1.0", "end-1c")
            self.clipboard_clear()
            self.clipboard_append(content)
            self._log_hint.configure(text="已复制全部日志到剪贴板")
        except Exception as e:
            messagebox.showerror("复制失败", str(e))

    def _worker_busy(self) -> bool:
        """安全判断后台线程是否仍在运行。"""
        w = self.worker
        if w is None:
            return False
        try:
            return w.is_alive()
        except TypeError:
            # 兼容异常状态的 Thread
            return False

    def _start_scan(self, mode: str = "quick") -> None:
        """启动巡检。mode: quick=状态/落盘；deep=深度音视频抽检。"""
        if self._worker_busy():
            messagebox.showinfo("提示", "扫描正在进行中")
            return
        deep = mode == "deep"
        # 按钮决定本次模式；深度旁勾选决定是否落盘保存片段
        self.var_deep.set(deep)
        if not deep:
            # 快速巡检不保存片段
            pass
        try:
            prof = self._form_to_profile_dict()
        except Exception as e:
            messagebox.showerror("参数错误", str(e))
            return
        devices = prof["devices"]
        if not devices:
            messagebox.showerror("错误", "请至少配置一台设备")
            return

        opt = dict(prof["scan_options"] or {})
        opt["deep_av_check"] = deep
        opt["av_save"] = bool(self.var_av_save.get()) if deep else False
        prof["scan_options"] = opt
        # 保存当前表单
        self.store.update_profile(None, prof)

        labels = [f"{i+1}. {d['name']} ({d['ip']})" for i, d in enumerate(devices)]
        cur = self.scan_device_var.get()
        idx = labels.index(cur) if cur in labels else 0
        device = devices[idx]
        if not device.get("ip"):
            messagebox.showerror("错误", "设备 IP 为空")
            return
        if not device.get("password"):
            if not messagebox.askyesno("确认", "密码为空，是否继续？"):
                return

        if deep and not (
            _which_tools().get("ffmpeg") and _which_tools().get("ffprobe")
        ):
            if not messagebox.askyesno(
                "缺少 ffmpeg",
                "深度巡检需要 ffmpeg/ffprobe，当前未检测到。\n"
                "是否改为仅做状态与近期录像检查继续？\n"
                "（取消则中止）",
            ):
                return
            opt["deep_av_check"] = False
            opt["av_save"] = False
            deep = False

        self.warn_box.delete("1.0", "end")
        for item in self.tree.get_children():
            self.tree.delete(item)
        for k in self._metric_cards:
            self._set_metric(k, "—", "—", "muted")
        mode_label = "深度巡检" if deep else "快速巡检"
        if deep and opt.get("av_save"):
            mode_label += " · 保存片段"
        self._log(
            f"—— {mode_label} {device.get('name')} ({device.get('ip')}) ——",
            level="step",
        )
        self._log(
            f"模式: {mode_label} · 回溯 {opt.get('lookback') or 60} 分钟"
            + (f" · 快速模式(仅配置)" if opt.get("no_search") and not deep else ""),
            level="info",
        )
        self._set_app_status(
            "running",
            detail=f"{mode_label} · {device.get('name')} ({device.get('ip')})",
        )
        self._set_scan_buttons_enabled(False)

        # 上一轮结束后允许启动新线程；不复用已结束的 Thread 实例
        self.worker = ScanWorker(device, opt, self.msg_q)
        self.worker.start()

    def _status_palette(self) -> Dict[str, Dict[str, str]]:
        """状态颜色（亮色 / 暗色）。"""
        dark = ctk.get_appearance_mode() == "Dark"
        if dark:
            return {
                "ready": {"fg": "#9ca3af", "dot": "#6b7280", "bar": "#6b7280"},
                "running": {"fg": "#60a5fa", "dot": "#3b82f6", "bar": "#3b82f6"},
                "ok": {"fg": "#4ade80", "dot": "#22c55e", "bar": "#22c55e"},
                "warn": {"fg": "#fbbf24", "dot": "#f59e0b", "bar": "#f59e0b"},
                "error": {"fg": "#f87171", "dot": "#ef4444", "bar": "#ef4444"},
            }
        return {
            "ready": {"fg": "#6b7280", "dot": "#9ca3af", "bar": "#9ca3af"},
            "running": {"fg": "#1d4ed8", "dot": "#2563eb", "bar": "#3b82f6"},
            "ok": {"fg": "#15803d", "dot": "#16a34a", "bar": "#22c55e"},
            "warn": {"fg": "#b45309", "dot": "#d97706", "bar": "#f59e0b"},
            "error": {"fg": "#b91c1c", "dot": "#dc2626", "bar": "#ef4444"},
        }

    def _set_progress_value(self, fraction: float, *, current=None, total=None) -> None:
        """设置确定进度 0~1，并更新百分比/计数文案。"""
        try:
            frac = max(0.0, min(1.0, float(fraction)))
        except (TypeError, ValueError):
            frac = 0.0
        self._progress_running = True
        try:
            self.progress.set(frac)
        except Exception:
            pass
        pct = int(round(frac * 100))
        if current is not None and total:
            try:
                self._progress_pct_var.set(f"{int(current)}/{int(total)}")
            except (TypeError, ValueError):
                self._progress_pct_var.set(f"{pct}%")
        else:
            self._progress_pct_var.set(f"{pct}%")

    def _apply_query_progress(self, data: Dict[str, Any]) -> None:
        """根据后台上报更新进度条与状态详情。"""
        if not isinstance(data, dict):
            return
        msg = str(data.get("msg") or "").strip()
        phase = str(data.get("phase") or "")
        current = data.get("current")
        total = data.get("total")
        overall = data.get("overall")

        # 阶段默认区间（无 overall 时用 current/total 插值）
        phase_ranges = {
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

        frac: Optional[float] = None
        if overall is not None:
            try:
                frac = float(overall)
            except (TypeError, ValueError):
                frac = None
        if frac is None and current is not None and total:
            try:
                c, t = int(current), int(total)
                if t > 0:
                    lo, hi = phase_ranges.get(phase, (0.0, 1.0))
                    frac = lo + (hi - lo) * (c / t)
            except (TypeError, ValueError):
                frac = None
        if frac is None and phase in phase_ranges:
            frac = phase_ranges[phase][0]

        if frac is not None:
            self._set_progress_value(frac, current=current, total=total)

        if msg:
            brief = msg.replace("\n", " ")
            if len(brief) > 72:
                brief = brief[:72] + "…"
            self._status_detail_var.set(brief)

    def _set_app_status(
        self,
        state: str,
        text: Optional[str] = None,
        detail: str = "",
    ) -> None:
        """
        更新底部状态栏。
        state: ready | running | ok | warn | error
        """
        labels = {
            "ready": "就绪",
            "running": "扫描中",
            "ok": "完成",
            "warn": "完成",
            "error": "失败",
        }
        defaults_detail = {
            "ready": "等待开始巡检",
            "running": "正在巡检…",
            "ok": "巡检完成",
            "warn": "巡检完成（有预警）",
            "error": "巡检失败",
        }
        state = state if state in labels else "ready"
        palette = self._status_palette().get(state) or self._status_palette()["ready"]
        label = text if text is not None else labels[state]
        self.status_var.set(label)
        self._status_detail_var.set(detail or defaults_detail[state])
        try:
            self._status_dot.configure(text_color=palette["dot"])
            self._status_label.configure(text_color=palette["fg"])
            self.progress.configure(progress_color=palette["bar"])
        except Exception:
            pass

        if state == "running":
            self._progress_running = True
            self._set_progress_value(0.02)
        else:
            self._progress_running = False
            if state in ("ok", "warn"):
                self.progress.set(1.0)
                self._progress_pct_var.set("100%")
            elif state == "error":
                # 保留当前进度，便于看出失败前走到哪
                if not self._progress_pct_var.get():
                    self.progress.set(0.0)
            else:
                self.progress.set(0.0)
                self._progress_pct_var.set("")

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == "log":
                    msg = str(payload)
                    self._log(msg)
                elif kind == "progress":
                    if isinstance(payload, dict):
                        self._apply_query_progress(payload)
                    elif payload:
                        self._status_detail_var.set(str(payload)[:72])
                elif kind == "error":
                    self._set_scan_buttons_enabled(True)
                    err = str(payload).strip().splitlines()[0] if payload else "未知错误"
                    self._set_app_status("error", detail=err[:100])
                    self.worker = None
                    self._log(f"巡检失败: {err}", level="error")
                    if "\n" in str(payload):
                        self._log(str(payload)[:500], level="muted")
                    messagebox.showerror("巡检失败", str(payload)[:800])
                elif kind == "done":
                    self._set_scan_buttons_enabled(True)
                    self.worker = None
                    # 先拉满进度再切换完成态
                    self._set_progress_value(1.0)
                    health = (payload or {}).get("health") or {}
                    hstat = health.get("健康状态") or "未知"
                    name = (payload or {}).get("device_name") or ""
                    if hstat == "良好":
                        self._set_app_status(
                            "ok",
                            text="完成",
                            detail=f"健康：良好 · {name}".strip(" ·"),
                        )
                    elif hstat == "严重":
                        self._set_app_status(
                            "error",
                            text="异常",
                            detail=f"健康：严重 · {name}".strip(" ·"),
                        )
                    else:
                        self._set_app_status(
                            "warn",
                            text="预警",
                            detail=f"健康：{hstat} · {name}".strip(" ·"),
                        )
                    self._render_result(payload)
                    self._notify_scan_done(payload)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    @staticmethod
    def _fmt_online(v: Any) -> str:
        if v == "true":
            return "是"
        if v == "false":
            return "否"
        return "—"

    @staticmethod
    def _fmt_bool_audio(v: Any) -> str:
        if v is True:
            return "是"
        if v is False:
            return "否"
        return "—"

    @staticmethod
    def _fmt_status(v: Any) -> str:
        s = str(v or "—")
        return {
            "正常": "正常",
            "异常": "异常",
            "未知": "未知",
            "跳过": "跳过",
            "未配置": "未配置",
            "警告": "警告",
        }.get(s, s)

    def _row_tag(self, r: Dict[str, Any], deep: bool) -> str:
        if r.get("在线") == "false" or r.get("落盘状态") == "异常" or r.get("录像是否正常") in (
            "异常",
            "未配置",
        ):
            return "bad"
        if deep and (
            r.get("视频抽检") == "异常" or r.get("音频抽检") in ("异常", "警告")
        ):
            return "warn" if r.get("音频抽检") == "警告" else "bad"
        if r.get("落盘状态") == "跳过" or r.get("录像是否正常") in ("未知", "跳过"):
            return "muted"
        return "ok"

    def _notify_scan_done(self, data: Dict[str, Any]) -> None:
        """巡检完成后简洁弹窗。"""
        name = data.get("device_name") or "设备"
        messagebox.showinfo("查询完成", f"{name}\n查询完成")

    def _export_result(self) -> None:
        """导出最近一次巡检结果为 CSV 或文本报告。"""
        data = self._last_result
        if not data:
            messagebox.showinfo("导出", "暂无巡检结果，请先执行一次巡检。")
            return

        name = str(data.get("device_name") or "NVR").replace("/", "_").replace(" ", "_")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            title="导出巡检结果",
            defaultextension=".csv",
            initialfile=f"nvr_report_{name}_{stamp}.csv",
            filetypes=[
                ("CSV 表格", "*.csv"),
                ("文本报告", "*.txt"),
                ("全部", "*.*"),
            ],
        )
        if not path:
            return
        try:
            if path.lower().endswith(".txt"):
                self._export_txt(path, data)
            else:
                if not path.lower().endswith(".csv"):
                    path = path + ".csv"
                self._export_csv(path, data)
            self._log(f"已导出: {path}")
            messagebox.showinfo("导出成功", f"已保存到：\n{path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def _export_csv(self, path: str, data: Dict[str, Any]) -> None:
        records = data.get("records") or []
        deep = bool(data.get("deep_av"))
        health = data.get("health") or {}
        stats = health.get("统计") or {}
        info = data.get("info") or {}

        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["# NVR 巡检报告"])
            w.writerow(["导出时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
            w.writerow(["设备名称", data.get("device_name") or ""])
            w.writerow(["型号", info.get("型号") or ""])
            w.writerow(["固件", info.get("固件版本") or ""])
            w.writerow(["健康状态", health.get("健康状态") or ""])
            for i, warn in enumerate(health.get("预警信息") or [], 1):
                w.writerow([f"预警{i}", warn])
            w.writerow([])
            w.writerow(["汇总项", "数值"])
            # 导出时用易懂表头；stats 内部键仍为 落盘*
            label_map = {
                "落盘正常": "近期有录像",
                "落盘异常": "近期无录像",
                "落盘未知": "近期录像未知",
            }
            for key in (
                "摄像头总数", "摄像头在线", "摄像头离线",
                "计划已配置", "计划未配置",
                "录像正常", "录像异常", "录像未知",
                "含音频", "不含音频",
                "落盘正常", "落盘异常", "落盘未知",
                "视频抽检正常", "视频抽检异常",
                "音频抽检正常", "音频抽检异常", "音频抽检警告",
            ):
                if key in stats:
                    w.writerow([label_map.get(key, key), stats.get(key)])
            w.writerow([])
            header = ["通道", "名称", "在线", "含音频", "近期录像", "录像"]
            if deep:
                header += ["视频抽检", "音频抽检", "抽检详情"]
            else:
                header += ["近期录像详情"]
            w.writerow(header)
            for r in records:
                online = self._fmt_online(r.get("在线"))
                row = [
                    r.get("通道", ""),
                    r.get("名称") or "",
                    online,
                    self._fmt_bool_audio(r.get("录像含音频")),
                    self._fmt_status(r.get("落盘状态")),
                    self._fmt_status(r.get("录像是否正常")),
                ]
                if deep:
                    row += [
                        self._fmt_status(r.get("视频抽检")),
                        self._fmt_status(r.get("音频抽检")),
                        r.get("抽检详情") or "",
                    ]
                else:
                    row.append(r.get("落盘详情") or "")
                w.writerow(row)

            drives = data.get("drives") or []
            if drives:
                w.writerow([])
                w.writerow(["硬盘", "状态", "使用率", "已用TB", "容量TB", "剩余TB"])
                for d in drives:
                    w.writerow([
                        d.get("盘符"), d.get("状态"), d.get("使用率"),
                        d.get("已用空间TB"), d.get("容量TB"), d.get("剩余空间TB"),
                    ])

    def _export_txt(self, path: str, data: Dict[str, Any]) -> None:
        """导出纯文本报告（摘要 + 表格行）。"""
        summary = self.summary_box.get("1.0", "end").strip()
        lines = [summary, "", "【通道明细】"]
        deep = bool(data.get("deep_av"))
        if deep:
            lines.append(
                "通道\t名称\t在线\t含音频\t近期录像\t录像\t视频抽检\t音频抽检"
            )
        else:
            lines.append("通道\t名称\t在线\t含音频\t近期录像\t录像")
        for r in data.get("records") or []:
            cols = [
                str(r.get("通道", "")),
                str(r.get("名称") or ""),
                self._fmt_online(r.get("在线")),
                self._fmt_bool_audio(r.get("录像含音频")),
                self._fmt_status(r.get("落盘状态")),
                self._fmt_status(r.get("录像是否正常")),
            ]
            if deep:
                cols += [
                    self._fmt_status(r.get("视频抽检")),
                    self._fmt_status(r.get("音频抽检")),
                ]
            lines.append("\t".join(cols))
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _render_result(self, data: Dict[str, Any]) -> None:
        self._last_result = data
        health = data.get("health") or {}
        stats = health.get("统计") or {}
        status = health.get("健康状态", "未知")
        info = data.get("info") or {}
        records = data.get("records") or []
        self._result_records = records
        deep = bool(data.get("deep_av"))
        name = data.get("device_name") or ""

        # —— 标题 ——
        tone = "ok" if status == "良好" else ("bad" if status == "严重" else "warn")
        self.summary_label.configure(text=f"健康：{status}    ·    {name}")
        self.device_sub_label.configure(
            text=f"{info.get('型号') or '-'}  ·  固件 {info.get('固件版本') or '-'}  ·  通道 {len(records)}"
        )

        # —— 指标卡片 ——
        online = stats.get("摄像头在线", 0)
        offline = stats.get("摄像头离线", 0)
        total = stats.get("摄像头总数", len(records))
        self._set_metric(
            "health", "健康状态", str(status),
            "ok" if status == "良好" else ("bad" if status == "严重" else "warn"),
        )
        self._set_metric(
            "online", "摄像头在线", f"{online}/{total}",
            "bad" if offline else "ok",
        )
        disk_checked = bool(stats.get("落盘已检查", True))
        record_checked = bool(stats.get("录像已检查", disk_checked))

        rec_ok = stats.get("录像正常", 0)
        rec_bad = stats.get("录像异常", 0)
        if not record_checked:
            self._set_metric("record", "录像正常", "未检查", "muted")
        else:
            self._set_metric(
                "record", "录像正常", f"{rec_ok}/{total}",
                "bad" if rec_bad else "ok",
            )

        disk_ok = stats.get("落盘正常", 0)
        disk_bad = stats.get("落盘异常", 0)
        if not disk_checked:
            self._set_metric("disk", "近期有录像", "未检查", "muted")
        else:
            disk_label = f"{disk_ok}/{total}"
            if stats.get("落盘未知", 0) and not disk_ok and not disk_bad:
                disk_label = "未检查"
                disk_tone = "muted"
            else:
                disk_tone = "bad" if disk_bad else "ok"
            self._set_metric("disk", "近期有录像", disk_label, disk_tone)

        audio_yes = stats.get("含音频", 0)
        self._set_metric(
            "audio", "含音频配置", f"{audio_yes}/{total}",
            "ok" if audio_yes == total and total else "warn",
        )

        # —— 预警 ——
        # 未查询近期录像/录像综合时，不在预警区展示相关结论
        self.warn_box.delete("1.0", "end")
        warns = list(health.get("预警信息") or [])
        if not disk_checked or not record_checked:
            filtered = []
            for w in warns:
                s = str(w)
                if not disk_checked and (
                    "落盘" in s or "近期录像" in s or "近期无录像" in s or "录像片段" in s
                ):
                    continue
                # 录像正常/异常综合结论依赖近期录像检查；配置类（计划/音频）仍保留
                if not record_checked and (
                    "无正常落盘" in s
                    or "近期无录像" in s
                    or "落盘状态未知" in s
                    or "近期录像状态未知" in s
                    or ("录像" in s and "计划" not in s and "音频" not in s)
                ):
                    continue
                filtered.append(w)
            warns = filtered

        drives = data.get("drives") or []
        wlines: List[str] = []
        if warns:
            wlines.append("预警：")
            for w in warns:
                wlines.append(f"  • {w}")
        else:
            wlines.append("预警：无")
        if not disk_checked and not record_checked:
            wlines.append("说明：本次未查近期录像状态（快速模式仅配置查询）")
        if drives:
            wlines.append(
                "硬盘："
                + "  |  ".join(
                    f"{d.get('盘符')} {d.get('状态')} {d.get('使用率')}"
                    for d in drives
                )
            )
        if data.get("av_save_dir"):
            wlines.append(f"片段目录：{data['av_save_dir']}")
        if deep:
            wlines.append(
                f"抽检：视频 正常{stats.get('视频抽检正常', 0)}/"
                f"异常{stats.get('视频抽检异常', 0)}  "
                f"音频 正常{stats.get('音频抽检正常', 0)}/"
                f"异常{stats.get('音频抽检异常', 0)}/"
                f"警告{stats.get('音频抽检警告', 0)}"
            )
        self.warn_box.insert("end", "\n".join(wlines) + "\n")

        # —— Treeview 表格（支持双指滑动）——
        self._last_channel_records = list(records or [])
        self._last_channel_deep = bool(deep)
        self._style_treeview()
        self._configure_tree_columns(deep)
        for item in self.tree.get_children():
            self.tree.delete(item)

        dark = ctk.get_appearance_mode() == "Dark"
        self.tree.tag_configure("ok", foreground="#6dcf7a" if dark else "#1a7f37")
        self.tree.tag_configure("bad", foreground="#ff6b6b" if dark else "#c62828")
        self.tree.tag_configure("warn", foreground="#fcb24a" if dark else "#b26a00")
        self.tree.tag_configure("muted", foreground="#999999")
        # 斑马纹：更轻、更接近现代数据表
        self.tree.tag_configure("odd", background="#2e2e2e" if dark else "#f6f8fa")
        self.tree.tag_configure("even", background="#242424" if dark else "#ffffff")

        for i, r in enumerate(records):
            values = [
                str(r.get("通道", "")),
                str(r.get("名称") or "—"),
                self._fmt_online(r.get("在线")),
                self._fmt_bool_audio(r.get("录像含音频")),
                self._fmt_status(r.get("落盘状态")),
                self._fmt_status(r.get("录像是否正常")),
            ]
            if deep:
                values += [
                    self._fmt_status(r.get("视频抽检")),
                    self._fmt_status(r.get("音频抽检")),
                ]
            tag = self._row_tag(r, deep)
            stripe = "odd" if i % 2 else "even"
            self.tree.insert("", "end", values=values, tags=(tag, stripe))

        # 放大窗口已打开时同步刷新
        self._fill_channel_detail_tree()

        issue_n = 0
        for r in records:
            if self._row_tag(r, deep) not in ("bad", "warn"):
                continue
            issue_n += 1
            detail = r.get("抽检详情") or r.get("落盘详情") or ""
            if detail and detail not in ("未启用深度抽检", "未检查", "跳过"):
                self._log(
                    f"通道 {r.get('通道')} {r.get('名称') or ''}: {detail}",
                    level="warn" if self._row_tag(r, deep) == "warn" else "error",
                )

        offline = stats.get("摄像头离线", 0)
        rec_bad = stats.get("录像异常", 0)
        disk_bad = stats.get("落盘异常", 0)
        self._log(
            f"巡检完成 · 健康 {status} · 在线 {online}/{total}"
            f" · 离线 {offline} · 录像异常 {rec_bad} · 近期无录像 {disk_bad}"
            + (f" · 问题通道 {issue_n}" if issue_n else ""),
            level="ok" if status == "良好" else ("error" if status == "严重" else "warn"),
        )


def main() -> None:
    app = NVRApp()
    app.mainloop()


if __name__ == "__main__":
    main()

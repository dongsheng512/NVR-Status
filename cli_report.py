#!/usr/bin/env python3
"""Rich-based CLI report for NVR inspection (summary-first, exception-first)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

console = Console()


# ---------- helpers ----------

def _health_style(status: str) -> str:
    if status == "良好":
        return "bold green"
    if status == "警告":
        return "bold yellow"
    if status in ("严重", "故障", "异常"):
        return "bold red"
    return "bold white"


def _health_badge(status: str) -> Text:
    icons = {"良好": "✓", "警告": "!", "严重": "×", "故障": "×"}
    icon = icons.get(status, "?")
    return Text(f"[{icon}] {status}", style=_health_style(status))


def _ok_style(val: str) -> str:
    if val in ("正常", "在线", "是", "ok"):
        return "green"
    if val in ("异常", "未配置", "离线", "否", "错误"):
        return "red"
    if val in ("警告", "未知", "跳过"):
        return "yellow"
    return "white"


def _cell(val: Any, style: Optional[str] = None) -> Text:
    s = "—" if val is None or val == "" else str(val)
    return Text(s, style=style or _ok_style(s))


def _usage_bar(pct: float, width: int = 12) -> Text:
    pct = max(0.0, min(100.0, pct))
    if pct > 95:
        color = "red"
    elif pct > 80:
        color = "yellow"
    else:
        color = "green"
    filled = int(round(width * pct / 100.0))
    bar = "█" * filled + "░" * (width - filled)
    return Text(f"{bar} {pct:5.1f}%", style=color)


def _is_problem_record(r: Dict[str, Any], deep: bool) -> bool:
    ok = r.get("录像是否正常") or "未知"
    if ok != "正常":
        return True
    if r.get("在线") == "false":
        return True
    if r.get("录像含音频") is False:
        return True
    if deep:
        if r.get("视频抽检") not in (None, "正常", "跳过"):
            return True
        if r.get("音频抽检") not in (None, "正常", "跳过"):
            return True
    return False


def _drive_usage_pct(drive: Dict[str, Any]) -> float:
    try:
        return float(str(drive.get("使用率", "0")).replace("%", "") or 0)
    except (TypeError, ValueError):
        return 0.0


def _sum_drives(drives: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    cap = sum(float(d.get("容量TB") or 0) for d in drives)
    used = sum(float(d.get("已用空间TB") or 0) for d in drives)
    free = sum(float(d.get("剩余空间TB") or 0) for d in drives)
    avg = (used / cap * 100) if cap > 0 else 0.0
    return {"cap": cap, "used": used, "free": free, "avg": avg}


# ---------- single device render ----------

def render_device_report(report: Dict[str, Any], *, verbose: bool = False) -> None:
    """Render one device report to the terminal."""
    if report.get("error"):
        console.print(
            Panel(
                Text(str(report["error"]), style="bold red"),
                title=f"[bold]{report.get('device_name', '设备')}[/]  ·  连接失败",
                border_style="red",
                box=box.ROUNDED,
            )
        )
        return

    info = report.get("info") or {}
    sys_status = report.get("sys_status") or {}
    health = report.get("health") or {}
    stats = health.get("统计") or {}
    alarms = report.get("alarms") or []
    cameras = report.get("cameras") or []
    records = report.get("records") or []
    drives = report.get("drives") or []
    deep = bool(report.get("deep_av_check"))
    label = report.get("device_name") or info.get("设备名称") or report.get("ip") or "NVR"
    model = info.get("型号") or "—"
    fw = info.get("固件版本") or "—"
    status = health.get("健康状态") or "未知"
    warnings = health.get("预警信息") or []

    # --- Hero panel ---
    hero = Table.grid(padding=(0, 2))
    hero.add_column(style="bold")
    hero.add_column()
    hero.add_row("整体状态", _health_badge(status))
    hero.add_row("型号 / 固件", Text(f"{model}  ·  {fw}", style="cyan"))
    hero.add_row("地址", Text(str(report.get("ip") or info.get("MAC地址") or "—"), style="dim"))
    if sys_status.get("运行时长"):
        hero.add_row("运行时长", str(sys_status.get("运行时长")))
    if sys_status.get("内存使用率"):
        hero.add_row("内存", f"{sys_status.get('内存使用', '—')} / 可用 {sys_status.get('内存可用', '—')} ({sys_status.get('内存使用率')})")
    if sys_status.get("当前时间"):
        hero.add_row("设备时间", str(sys_status.get("当前时间")))

    border = "green" if status == "良好" else ("yellow" if status == "警告" else "red")
    console.print()
    console.print(
        Panel(
            hero,
            title=f"[bold]{label}[/]",
            subtitle=f"[dim]{info.get('序列号') or ''}[/]",
            border_style=border,
            box=box.ROUNDED,
        )
    )

    # --- Metrics table ---
    metrics = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold cyan",
        pad_edge=False,
        expand=False,
    )
    metrics.add_column("检查项", style="bold")
    metrics.add_column("结果", justify="center")
    metrics.add_column("明细")

    cam_total = stats.get("摄像头总数", len(cameras))
    cam_on = stats.get("摄像头在线", 0)
    cam_off = stats.get("摄像头离线", 0)
    metrics.add_row(
        "摄像头",
        _cell("正常" if cam_off == 0 and cam_total else ("异常" if cam_off else "未知")),
        f"在线 {cam_on} / 离线 {cam_off} · 共 {cam_total}",
    )
    plan_ok = stats.get("计划已配置", 0)
    plan_bad = stats.get("计划未配置", 0)
    metrics.add_row(
        "录像计划",
        _cell("正常" if plan_bad == 0 else "异常"),
        f"已配置 {plan_ok} / 未配置 {plan_bad}",
    )
    rec_ok = stats.get("录像正常", 0)
    rec_bad = stats.get("录像异常", 0)
    rec_unk = stats.get("录像未知", 0)
    metrics.add_row(
        "录像/近期录像",
        _cell("正常" if rec_bad == 0 and rec_unk == 0 else ("异常" if rec_bad else "警告")),
        f"正常 {rec_ok} / 异常 {rec_bad} / 未知 {rec_unk}",
    )
    a_yes = stats.get("含音频", 0)
    a_no = stats.get("不含音频", 0)
    metrics.add_row(
        "音频配置",
        _cell("正常" if a_no == 0 else "警告"),
        f"含音频 {a_yes} / 不含 {a_no}",
    )
    if deep:
        v_ok = stats.get("视频抽检正常", 0)
        v_bad = stats.get("视频抽检异常", 0)
        a_ok = stats.get("音频抽检正常", 0)
        a_bad = stats.get("音频抽检异常", 0)
        a_warn = stats.get("音频抽检警告", 0)
        av_status = "正常"
        if v_bad or a_bad:
            av_status = "异常"
        elif a_warn:
            av_status = "警告"
        metrics.add_row(
            "音视频抽检",
            _cell(av_status),
            f"视频 {v_ok}/{v_ok + v_bad} · 音频 正常{a_ok}/异常{a_bad}/警告{a_warn}"
            f" · {report.get('av_seconds', 6)}s RTSP",
        )

    dsum = _sum_drives(drives)
    if drives:
        disk_status = "正常"
        if dsum["avg"] > 95:
            disk_status = "警告"
        abn = sum(1 for d in drives if d.get("状态") not in ("ok", "sleep", "idle"))
        if abn:
            disk_status = "异常"
        disk_detail = Text(f"{len(drives)} 块 · {dsum['used']:.1f}/{dsum['cap']:.1f} TB · 均用 ")
        disk_detail.append(_usage_bar(dsum["avg"]))
        metrics.add_row("硬盘", _cell(disk_status), disk_detail)

    alarm_status = "正常" if not alarms else "警告"
    metrics.add_row(
        "报警",
        _cell(alarm_status),
        "无报警" if not alarms else f"{len(alarms)} 条",
    )
    console.print(metrics)

    # --- Warnings ---
    if warnings or alarms:
        lines = []
        for w in warnings:
            lines.append(Text(f"• {w}", style="yellow"))
        for a in alarms:
            lines.append(
                Text(
                    f"• [报警] {a.get('类型', '')}: {a.get('盘符', '')} - {a.get('描述', '')}",
                    style="red",
                )
            )
        console.print(
            Panel(
                Group(*lines),
                title="[bold yellow]预警[/]",
                border_style="yellow",
                box=box.ROUNDED,
            )
        )
    else:
        console.print(Text("  预警: 无", style="dim green"))

    # --- Channels: exception-first ---
    problem = [r for r in records if _is_problem_record(r, deep)]
    console.print()
    console.print(Rule(f"通道  ·  共 {len(records)} 路", style="cyan"))

    if not records:
        console.print(Text("  无通道数据", style="yellow"))
    elif not verbose and not problem:
        console.print(
            Text(f"  ✓ 全部 {len(records)} 路正常（录像/在线/音频", style="green")
            + (Text("/抽检", style="green") if deep else Text(""))
            + Text("）。使用 --verbose 查看全表。", style="green")
        )
    else:
        show = records if verbose else problem
        if not verbose:
            console.print(
                Text(f"  仅显示异常/警告 {len(problem)} 路（--verbose 查看全部）", style="yellow")
            )
        ch = Table(
            box=box.SIMPLE,
            header_style="bold",
            show_lines=False,
            pad_edge=False,
        )
        ch.add_column("#", justify="right", style="cyan", no_wrap=True)
        ch.add_column("名称", overflow="ellipsis", max_width=18)
        ch.add_column("在线", justify="center")
        ch.add_column("模式", no_wrap=True)
        ch.add_column("音频", justify="center")
        ch.add_column("录像", justify="center")
        if deep:
            ch.add_column("视频", justify="center")
            ch.add_column("音频抽检", justify="center")
        if verbose:
            ch.add_column("IP", style="dim", no_wrap=True)

        # map channel id -> ip from cameras
        ip_map = {str(c.get("id")): c.get("IP") for c in cameras}

        detail_lines: List[str] = []
        for r in show:
            online = r.get("在线")
            if online == "true":
                on_cell = _cell("在线")
            elif online == "false":
                on_cell = _cell("离线")
            else:
                on_cell = _cell("未知")
            audio = r.get("录像含音频")
            if audio is True:
                au = _cell("是")
            elif audio is False:
                au = _cell("否")
            else:
                au = _cell("未知")
            row = [
                str(r.get("通道", "")),
                r.get("名称") or "—",
                on_cell,
                r.get("录像模式") or "—",
                au,
                _cell(r.get("录像是否正常") or "未知"),
            ]
            if deep:
                row.append(_cell(r.get("视频抽检") or "跳过"))
                row.append(_cell(r.get("音频抽检") or "跳过"))
            if verbose:
                row.append(ip_map.get(str(r.get("通道")), "—") or "—")
            ch.add_row(*row)

            # 异常详情表后统一输出
            ch_label = f"通道 {r.get('通道')}"
            if r.get("名称"):
                ch_label += f" {r.get('名称')}"
            if r.get("落盘详情") and r.get("落盘状态") not in (None, "正常", "跳过"):
                detail_lines.append(
                    f"{ch_label}: 近期录像 {r.get('落盘状态')} - {r.get('落盘详情')}"
                )
            if deep and r.get("抽检详情") and (
                r.get("视频抽检") not in ("正常", "跳过", None)
                or r.get("音频抽检") not in ("正常", "跳过", None)
            ):
                detail_lines.append(f"{ch_label}: 抽检 {r['抽检详情']}")

        console.print(ch)
        for d in detail_lines:
            console.print(Text(f"  ↳ {d}", style="dim yellow"))

    # --- Disks ---
    console.print()
    console.print(Rule(f"硬盘  ·  {len(drives)} 块", style="cyan"))
    if not drives:
        console.print(Text("  未检测到硬盘", style="yellow"))
    else:
        dt = Table(box=box.SIMPLE, header_style="bold", pad_edge=False)
        dt.add_column("盘符", style="cyan", no_wrap=True)
        dt.add_column("接口")
        dt.add_column("属性")
        dt.add_column("容量", justify="right")
        dt.add_column("已用", justify="right")
        dt.add_column("剩余", justify="right")
        dt.add_column("使用率")
        dt.add_column("状态", justify="center")
        for d in drives:
            st = d.get("状态") or ""
            if st == "ok":
                st_cell = _cell("正常")
            elif st in ("sleep", "idle"):
                st_cell = Text("休眠", style="cyan")
            else:
                st_cell = _cell("异常")
            pct = _drive_usage_pct(d)
            dt.add_row(
                str(d.get("盘符", "")),
                str(d.get("类型", "")),
                str(d.get("属性", "")),
                f"{d.get('容量TB', '—')}TB",
                f"{d.get('已用空间TB', '—')}TB",
                f"{d.get('剩余空间TB', '—')}TB",
                _usage_bar(pct),
                st_cell,
            )
        console.print(dt)
        console.print(
            Text(
                f"  合计  {dsum['used']:.1f}/{dsum['cap']:.1f} TB  ·  均用 ",
                style="dim",
            )
            + _usage_bar(dsum["avg"])
        )

    # --- verbose extras ---
    if verbose:
        console.print()
        console.print(Rule("设备详情", style="dim"))
        detail = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
        detail.add_column(style="bold dim", min_width=12)
        detail.add_column()
        for k, v in (info or {}).items():
            detail.add_row(str(k), str(v))
        if report.get("av_save_dir"):
            detail.add_row("抽检片段", str(report["av_save_dir"]))
        if report.get("lookback_minutes"):
            detail.add_row("近期录像窗口", f"近 {report['lookback_minutes']} 分钟")
        console.print(detail)

        if cameras and verbose:
            console.print()
            console.print(Rule(f"摄像头清单  ·  {len(cameras)}", style="dim"))
            ct = Table(box=box.SIMPLE, header_style="bold", pad_edge=False)
            ct.add_column("#", justify="right", style="cyan")
            ct.add_column("名称")
            ct.add_column("IP", style="dim")
            ct.add_column("型号")
            ct.add_column("在线", justify="center")
            for c in cameras:
                on = c.get("在线")
                if on == "true":
                    oc = _cell("在线")
                elif on == "false":
                    oc = _cell("离线")
                else:
                    oc = _cell(str(on or "未知"))
                ct.add_row(
                    str(c.get("id", "")),
                    c.get("名称") or "—",
                    c.get("IP") or "—",
                    c.get("型号") or "—",
                    oc,
                )
            console.print(ct)


def render_site_summary(reports: Sequence[Dict[str, Any]]) -> None:
    """Top-level multi-device summary (also fine for a single device)."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n = len(reports)
    ok_n = sum(1 for r in reports if not r.get("error"))
    fail_n = n - ok_n

    total_ch = 0
    online_ch = 0
    total_disks = 0
    total_cap = 0.0
    total_used = 0.0
    worst = "良好"
    rank = {"良好": 0, "警告": 1, "严重": 2, "故障": 3, "未知": 1}
    all_warnings: List[str] = []

    for r in reports:
        if r.get("error"):
            worst = "严重"
            all_warnings.append(f"{r.get('device_name', '?')}: 连接失败")
            continue
        h = r.get("health") or {}
        st = h.get("健康状态") or "未知"
        if rank.get(st, 1) > rank.get(worst, 0):
            worst = st
        stats = h.get("统计") or {}
        total_ch += int(stats.get("摄像头总数") or 0)
        online_ch += int(stats.get("摄像头在线") or 0)
        drives = r.get("drives") or []
        total_disks += len(drives)
        s = _sum_drives(drives)
        total_cap += s["cap"]
        total_used += s["used"]
        for w in h.get("预警信息") or []:
            all_warnings.append(f"{r.get('device_name', '?')}: {w}")

    head = Table.grid(padding=(0, 2))
    head.add_column(style="bold")
    head.add_column()
    head.add_row("巡检时间", now)
    head.add_row("整体状态", _health_badge(worst))
    head.add_row(
        "设备",
        Text(f"{ok_n}/{n} 台成功", style="green" if fail_n == 0 else "yellow")
        + (Text(f"  ·  失败 {fail_n}", style="red") if fail_n else Text("")),
    )
    head.add_row(
        "通道",
        f"{online_ch}/{total_ch} 在线" if total_ch else "—",
    )
    if total_disks:
        avg = (total_used / total_cap * 100) if total_cap > 0 else 0
        head.add_row(
            "硬盘",
            Text(f"{total_disks} 块  ·  {total_used:.1f}/{total_cap:.1f} TB  ·  ")
            + _usage_bar(avg),
        )
    if all_warnings:
        # show at most 4 in hero
        preview = all_warnings[:4]
        more = len(all_warnings) - len(preview)
        warn_text = "\n".join(f"• {w}" for w in preview)
        if more > 0:
            warn_text += f"\n• …另有 {more} 条"
        head.add_row("主要风险", Text(warn_text, style="yellow"))

    border = "green" if worst == "良好" else ("yellow" if worst == "警告" else "red")
    title = "NVR 站点巡检" if n > 1 else "NVR 巡检"
    console.print()
    console.print(
        Panel(head, title=f"[bold cyan]{title}[/]", border_style=border, box=box.DOUBLE)
    )

    if n > 1:
        t = Table(
            title="设备一览",
            box=box.ROUNDED,
            header_style="bold cyan",
            show_lines=False,
            pad_edge=False,
        )
        t.add_column("#", justify="right", style="dim")
        t.add_column("名称")
        t.add_column("型号")
        t.add_column("状态", justify="center")
        t.add_column("通道", justify="center")
        t.add_column("硬盘")
        t.add_column("预警", overflow="ellipsis", max_width=36)

        for i, r in enumerate(reports, 1):
            if r.get("error"):
                t.add_row(
                    str(i),
                    str(r.get("device_name") or "—"),
                    "—",
                    Text("失败", style="bold red"),
                    "—",
                    "—",
                    Text(str(r["error"])[:40], style="red"),
                )
                continue
            info = r.get("info") or {}
            h = r.get("health") or {}
            st = h.get("健康状态") or "未知"
            stats = h.get("统计") or {}
            ch_n = stats.get("摄像头总数", 0)
            ch_on = stats.get("摄像头在线", 0)
            drives = r.get("drives") or []
            s = _sum_drives(drives)
            warns = h.get("预警信息") or []
            warn_s = warns[0] if warns else "—"
            t.add_row(
                str(i),
                str(r.get("device_name") or "—"),
                str(info.get("型号") or "—"),
                _health_badge(st),
                f"{ch_on}/{ch_n}",
                Text(f"{len(drives)}盘 ") + _usage_bar(s["avg"], width=8),
                Text(warn_s, style="yellow" if warns else "dim"),
            )
        console.print(t)


def render_reports(reports: Sequence[Dict[str, Any]], *, verbose: bool = False) -> None:
    """Render site summary + each device."""
    if not reports:
        console.print("[yellow]无巡检结果[/]")
        return
    render_site_summary(reports)
    for r in reports:
        render_device_report(r, verbose=verbose)
    console.print()
    console.print(Rule(style="dim"))
    n = len(reports)
    fail = sum(1 for r in reports if r.get("error"))
    if fail:
        console.print(
            f"[bold yellow]完成: {n - fail}/{n} 台成功, 失败 {fail} 台[/]"
        )
    else:
        console.print(f"[bold green]完成: {n}/{n} 台设备查询成功[/]")
    if not verbose:
        console.print("[dim]提示: 加 --verbose 可输出全部通道与设备详情[/]")
    console.print()


def collect_status(
    nvr: Any,
    *,
    device_name: str = "",
) -> Dict[str, Any]:
    """Gather all status fields from a connected HikvisionNVR instance."""
    info = nvr.get_device_info()
    sys_status = nvr.get_system_status()
    cameras = nvr.get_cameras()
    records = nvr.get_recording_status()
    health = nvr.get_health_summary()
    alarms = nvr.get_alarm_status()
    drives = nvr.get_storage_status()
    return {
        "device_name": device_name or (info or {}).get("设备名称") or nvr.ip,
        "ip": nvr.ip,
        "info": info or {},
        "sys_status": sys_status or {},
        "health": health or {},
        "alarms": alarms or [],
        "cameras": cameras or [],
        "records": records or [],
        "drives": drives or [],
        "lookback_minutes": nvr.lookback_minutes,
        "deep_av_check": nvr.deep_av_check,
        "av_seconds": nvr.av_seconds,
        "av_workers": nvr.av_workers,
        "busy_start_hour": nvr.busy_start_hour,
        "busy_end_hour": nvr.busy_end_hour,
        "av_save": nvr.av_save,
        "av_save_dir": nvr.av_save_dir,
        "check_disk_recording": nvr.check_disk_recording,
        "error": None,
    }

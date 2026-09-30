#!/usr/bin/env python3
"""
海康威视NVR/DVR状态查询脚本（兼容入口 / 薄门面）。

自 B2 起，核心实现已迁移至 nvr_core 包：
- HikvisionNVR: nvr_core.nvr（组合 ISAPI 客户端 / 存储 / 录像 / 抽检 / 健康）
- 工具函数与异常: nvr_core.util（Colors、_which_tools、ScanCancelled 等）

本模块保留 CLI（build_arg_parser / nvr_from_args / main）并重新导出
旧公共 API，避免破坏 cli_report、ui 及测试的既有 import。
"""

import argparse

import nvr_core.util as _util
from nvr_core.nvr import HikvisionNVR

# ---- 重新导出旧公共 API（保持既有 import 兼容） ----
Colors = _util.Colors
ScanCancelled = _util.ScanCancelled
_to_int = _util._to_int
_to_float = _util._to_float
_parse_hik_time = _util._parse_hik_time
_project_dir = _util._project_dir
_resource_dir = _util._resource_dir
_which_tools = _util._which_tools
_safe_filename = _util._safe_filename

__all__ = [
    "HikvisionNVR",
    "Colors",
    "ScanCancelled",
    "_to_int",
    "_to_float",
    "_parse_hik_time",
    "_project_dir",
    "_resource_dir",
    "_which_tools",
    "_safe_filename",
    "print_status",
    "build_arg_parser",
    "nvr_from_args",
]


def print_status(nvr: HikvisionNVR, *, verbose: bool = False, device_name: str = ""):
    """采集并打印单台 NVR 状态（Rich 报告式布局）。返回统一结果 dict。"""
    from cli_report import collect_status, render_reports

    report = collect_status(nvr, device_name=device_name)
    if not report.get("info"):
        report["error"] = "无法获取设备信息，请检查 IP/账号/网络"
    render_reports([report], verbose=verbose)
    return report


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="海康威视NVR/DVR状态查询工具")
    parser.add_argument("-i", "--ip", help="NVR/DVR的IP地址（单机直连时必填）")
    parser.add_argument("-p", "--port", type=int, default=80, help="端口号,默认80")
    parser.add_argument("-u", "--username", default="admin", help="用户名,默认admin")
    parser.add_argument("-w", "--password", help="密码（单机直连时必填）")
    parser.add_argument("-s", "--ssl", action="store_true", help="使用HTTPS")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="输出全部通道明细与设备详情（默认仅摘要+异常通道）",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=60,
        help="检查近期录像落盘的时间窗口(分钟),默认60",
    )
    parser.add_argument(
        "--no-search",
        action="store_true",
        help="跳过CMSearch落盘检查(仅查计划与音频配置,更快)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="落盘检查并发数,默认8",
    )
    parser.add_argument(
        "--deep-av-check",
        action="store_true",
        help="启用深度音视频抽检:短时RTSP拉流(默认关闭,不干扰日常录像)",
    )
    parser.add_argument(
        "--av-seconds",
        type=int,
        default=6,
        help=f"每路抽检时长(秒),默认6,最大{HikvisionNVR.AV_SECONDS_MAX}",
    )
    parser.add_argument(
        "--av-workers",
        type=int,
        default=2,
        help=f"抽检并发数,默认2,最大{HikvisionNVR.AV_WORKERS_MAX}(保护NVR)",
    )
    parser.add_argument(
        "--av-limit",
        type=int,
        default=None,
        help="最多抽检通道数(用于抽样);默认全部落盘正常通道",
    )
    parser.add_argument(
        "--av-channels",
        default=None,
        metavar="N[,N...]",
        help="只抽检指定物理通道号(逗号分隔,如 31,64);默认抽检全部候选通道",
    )
    parser.add_argument(
        "--av-at",
        default=None,
        metavar="HH:MM[,HH:MM...]",
        help=(
            "定点抽检时刻(本地时间,逗号分隔可多个,如 19:05,16:20),"
            "每个时刻各查一遍;默认按繁忙时段逻辑选点。"
            "指定时刻若距现在不足10分钟会自动前移并在结果里注明"
        ),
    )
    parser.add_argument(
        "--silence-db",
        type=float,
        default=-80.0,
        help="音频 mean_volume 低于该值标为警告(dB),默认-80",
    )
    parser.add_argument(
        "--busy-start",
        type=int,
        default=10,
        help="深度抽检优先时段开始小时(本地时间,默认10)",
    )
    parser.add_argument(
        "--busy-end",
        type=int,
        default=18,
        help="深度抽检优先时段结束小时(本地时间,默认18)",
    )
    parser.add_argument(
        "--busy-days-ago",
        type=int,
        default=0,
        help="深度抽检优先落在哪一天: 0=今天, 1=昨天, N=N天前(默认0,最大30)",
    )
    parser.add_argument(
        "--av-save",
        action="store_true",
        help="保存抽检视频片段到项目目录 av_samples/<当前时间>/ (会自动启用深度抽检)",
    )
    parser.add_argument(
        "--av-save-root",
        default=None,
        help="抽检片段保存根目录,默认: 项目目录/av_samples",
    )
    return parser


def nvr_from_args(args: argparse.Namespace, *, quiet: bool = False) -> HikvisionNVR:
    """按 CLI 参数构造 NVR（委托 nvr_core.scan_runner 统一构造逻辑）。"""
    from nvr_core.scan_runner import build_nvr

    device = {
        "ip": args.ip,
        "port": int(getattr(args, "port", None) or 80),
        "username": getattr(args, "username", None) or "admin",
        "password": getattr(args, "password", None) or "",
        "ssl": bool(getattr(args, "ssl", False)),
    }
    busy_start = getattr(args, "busy_start", None)
    busy_end = getattr(args, "busy_end", None)
    deep = bool(
        getattr(args, "deep_av_check", False) or getattr(args, "av_save", False)
    )
    av_channels = getattr(args, "av_channels", None)
    if av_channels and not deep:
        # 通道过滤只作用于深度抽检；静默留着会让参数看起来「生效了」
        if not quiet:
            print(
                Colors.warning(
                    "--av-channels 已忽略: 未启用深度抽检"
                    "(需 --deep-av-check 或 --av-save)"
                )
            )
        av_channels = None
    av_at = getattr(args, "av_at", None)
    if av_at and not deep:
        # 同 --av-channels:定点时刻只作用于深度抽检
        if not quiet:
            print(
                Colors.warning(
                    "--av-at 已忽略: 未启用深度抽检"
                    "(需 --deep-av-check 或 --av-save)"
                )
            )
        av_at = None
    options = {
        "lookback": int(getattr(args, "lookback", None) or 60),
        "no_search": bool(getattr(args, "no_search", False)),
        "workers": int(getattr(args, "workers", None) or 8),
        "deep_av_check": deep,
        "av_seconds": int(getattr(args, "av_seconds", None) or 6),
        "av_workers": int(getattr(args, "av_workers", None) or 2),
        "av_limit": getattr(args, "av_limit", None),
        "av_channels": av_channels,
        "av_at": av_at,
        "silence_db": float(getattr(args, "silence_db", None) if getattr(args, "silence_db", None) is not None else -80.0),
        # 0 是合法小时(午夜),不能用 or 兜底
        "busy_start": 10 if busy_start is None else int(busy_start),
        "busy_end": 18 if busy_end is None else int(busy_end),
        "busy_days_ago": int(getattr(args, "busy_days_ago", None) or 0),
        "av_save": bool(getattr(args, "av_save", False)),
        "av_save_root": getattr(args, "av_save_root", None),
    }
    return build_nvr(device, options, quiet=quiet)


def main():
    """单机 CLI 入口。

    退出码: 0=巡检成功且健康良好/警告; 1=巡检失败(连接错误等);
    2=巡检成功但健康状态「严重」(便于 cron/监控脚本告警)。
    """
    parser = build_arg_parser()
    args = parser.parse_args()

    if not args.ip or not args.password:
        parser.error("单机模式需要 -i/--ip 与 -w/--password；多设备请使用 ./nvr")

    try:
        nvr = nvr_from_args(args)
        try:
            report = print_status(nvr, verbose=bool(args.verbose))
        finally:
            nvr.close()
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as e:
        print(f"巡检失败: {e}")
        raise SystemExit(1)

    if report.get("error"):
        raise SystemExit(1)
    if ((report.get("health") or {}).get("健康状态")) == "严重":
        raise SystemExit(2)
    raise SystemExit(0)


if __name__ == "__main__":
    main()

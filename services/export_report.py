"""巡检结果导出：CSV(utf-8-sig) / TXT 纯文本报告。

无 Qt / 无 GUI 依赖，CLI 与 GUI 可共用，便于单测。
"""

from __future__ import annotations

import csv
from datetime import datetime
from typing import Any, Dict, List


def fmt_online(v: Any) -> str:
    if v == "true":
        return "是"
    if v == "false":
        return "否"
    return "—"


def fmt_bool_audio(v: Any) -> str:
    if v is True:
        return "是"
    if v is False:
        return "否"
    return "—"


def fmt_status(v: Any) -> str:
    s = str(v or "—")
    return {
        "正常": "正常",
        "异常": "异常",
        "未知": "未知",
        "跳过": "跳过",
        "未配置": "未配置",
        "警告": "警告",
    }.get(s, s)


def _row_values(r: Dict[str, Any], deep: bool) -> List[str]:
    row = [
        str(r.get("通道", "")),
        str(r.get("名称") or ""),
        fmt_online(r.get("在线")),
        fmt_bool_audio(r.get("录像含音频")),
        fmt_status(r.get("落盘状态")),
        fmt_status(r.get("录像是否正常")),
    ]
    if deep:
        row += [
            fmt_status(r.get("视频抽检")),
            fmt_status(r.get("音频抽检")),
        ]
    return row


def export_csv(path: str, data: Dict[str, Any]) -> None:
    """导出 CSV(utf-8-sig，Excel 友好)。"""
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
            row = _row_values(r, deep)
            if deep:
                row.append(r.get("抽检详情") or "")
            else:
                row.append(r.get("落盘详情") or "")
            w.writerow(row)

        ow = data.get("disk_overwrite") or (data.get("health") or {}).get("循环覆盖") or {}
        if ow:
            w.writerow([])
            w.writerow(["循环覆盖", ow.get("label") or "未知"])
            if ow.get("detail"):
                w.writerow(["循环覆盖说明", ow.get("detail")])
            if ow.get("source"):
                w.writerow(["循环覆盖来源", ow.get("source")])

        drives = data.get("drives") or []
        if drives:
            w.writerow([])
            w.writerow(["硬盘", "状态", "使用率", "已用TB", "容量TB", "剩余TB"])
            for d in drives:
                w.writerow([
                    d.get("盘符"), d.get("状态"), d.get("使用率"),
                    d.get("已用空间TB"), d.get("容量TB"), d.get("剩余空间TB"),
                ])


def export_txt(path: str, data: Dict[str, Any], summary_text: str = "") -> None:
    """导出纯文本报告（摘要 + 表格行）。"""
    deep = bool(data.get("deep_av"))
    lines = [summary_text.strip(), "", "【通道明细】"]
    if deep:
        lines.append(
            "通道\t名称\t在线\t含音频\t近期录像\t录像\t视频抽检\t音频抽检"
        )
    else:
        lines.append("通道\t名称\t在线\t含音频\t近期录像\t录像")
    for r in data.get("records") or []:
        lines.append("\t".join(_row_values(r, deep)))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

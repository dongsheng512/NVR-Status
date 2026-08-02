"""巡检历史报告归档：JSON 存取 + 列表浏览。

B6：巡检完成结果自动归档到 app_data_dir()/reports/，
支持列表查看与再导出（CSV/TXT 由 export_report 负责）。

无 Qt / 无 GUI 依赖，GUI 与 CLI 可共用，便于单测。
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from config_store import app_data_dir

MAX_REPORTS = 500  # 保留最近 N 份，超出删除最旧


def history_dir() -> str:
    """历史报告目录（按年/月分桶，避免单目录文件过多）。"""
    path = os.path.join(app_data_dir(), "reports")
    os.makedirs(path, exist_ok=True)
    return path


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "_", str(name or "NVR")).strip("_") or "NVR"


def _json_default(obj: Any) -> Any:
    """json.dump default：把巡检结果里常见的非 JSON 类型转成可序列化值。"""
    if isinstance(obj, datetime):
        # 保留时区信息（ISO8601）
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, time):
        return obj.isoformat()
    if isinstance(obj, timedelta):
        return obj.total_seconds()
    if isinstance(obj, timezone):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, set):
        return list(obj)
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8", errors="replace")
        except Exception:
            return repr(obj)
    # 兜底：避免再抛 TypeError 导致整次归档失败
    return str(obj)


def save_report(data: Dict[str, Any]) -> str:
    """归档一份巡检结果，返回文件路径。

    在结果 dict 上注入 saved_at 后写入 reports/YYYYMMDD_HHMMSS_<设备>.json。
    保留最近 MAX_REPORTS 份，超出清理最旧。
    结果中的 datetime 等类型会自动转成字符串后再写入。
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = _safe_name(data.get("device_name") or "NVR")
    path = os.path.join(history_dir(), f"{stamp}_{name}.json")
    payload = dict(data or {})
    payload["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload["_file"] = os.path.basename(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            payload,
            f,
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        )
    _prune(history_dir())
    return path


def _prune(directory: str) -> None:
    files = [
        os.path.join(directory, fn)
        for fn in os.listdir(directory)
        if fn.endswith(".json")
    ]
    files.sort(key=os.path.getmtime, reverse=True)
    for old in files[MAX_REPORTS:]:
        try:
            os.remove(old)
        except OSError:
            pass


def list_reports() -> List[Dict[str, Any]]:
    """返回元数据列表（新→旧）：时间 / 设备 / 健康 / 通道数 / 错误 / 文件。"""
    out: List[Dict[str, Any]] = []
    directory = history_dir()
    for fn in sorted(os.listdir(directory), reverse=True):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(directory, fn)
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        health = payload.get("health") or {}
        records = payload.get("records") or []
        out.append(
            {
                "path": path,
                "saved_at": payload.get("saved_at") or "",
                "device_name": payload.get("device_name") or "",
                "ip": payload.get("ip") or "",
                "health_status": health.get("健康状态") or "未知",
                "channels": len(records),
                "error": payload.get("error") or "",
            }
        )
    return out


def load_report(path: str) -> Optional[Dict[str, Any]]:
    """读回完整结果 dict；文件损坏/缺失返回 None。"""
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None

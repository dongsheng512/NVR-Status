#!/usr/bin/env python3
"""多配置档案管理: 支持多组设备与扫描选项的保存/切换。"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from copy import deepcopy
from typing import Any, Dict, List, Optional


APP_NAME = "NVRStatus"


def app_data_dir() -> str:
    """跨平台用户数据目录(配置、档案、默认输出)。"""
    if sys.platform == "darwin":
        base = os.path.expanduser(f"~/Library/Application Support/{APP_NAME}")
    elif sys.platform == "win32":
        base = os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")),
            APP_NAME,
        )
    else:
        base = os.path.expanduser(f"~/.config/{APP_NAME}")
    os.makedirs(base, exist_ok=True)
    return base


def default_profiles_path() -> str:
    return os.path.join(app_data_dir(), "profiles.json")


def default_av_save_root() -> str:
    path = os.path.join(app_data_dir(), "av_samples")
    os.makedirs(path, exist_ok=True)
    return path


def _empty_device() -> Dict[str, Any]:
    return {
        "name": "NVR1",
        "ip": "192.168.1.100",
        "port": 80,
        "username": "admin",
        "password": "",
        "ssl": False,
    }


def _default_scan_options() -> Dict[str, Any]:
    return {
        "lookback": 60,  # 始终以分钟存储
        "lookback_unit": "minute",  # minute | hour | day，仅影响 GUI 展示
        "no_search": False,
        "workers": 8,
        "deep_av_check": False,
        "av_seconds": 6,
        "av_workers": 2,
        "av_limit": 0,  # 0 = 全部
        "silence_db": -80.0,
        "busy_start": 10,
        "busy_end": 18,
        "busy_days_ago": 0,  # 0=今天, 1=昨天, … 抽检优先落在该日繁忙时段
        "av_save": False,
        "av_save_root": "",  # 空则用 default_av_save_root()
    }


def _default_profile(name: str = "默认") -> Dict[str, Any]:
    return {
        "name": name,
        "devices": [_empty_device()],
        "default": 0,
        "scan_options": _default_scan_options(),
    }


def _default_store() -> Dict[str, Any]:
    return {
        "version": 1,
        "active_profile": "默认",
        "profiles": {
            "默认": _default_profile("默认"),
        },
    }


def _safe_profile_name(name: str) -> str:
    s = (name or "").strip()
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", s)
    return s[:64] or "未命名"


class ConfigStore:
    """多配置档案: 内存 + JSON 持久化。"""

    def __init__(self, path: Optional[str] = None):
        self.path = path or default_profiles_path()
        self.data: Dict[str, Any] = _default_store()
        self.load()

    def load(self) -> None:
        if not os.path.isfile(self.path):
            # 尝试从项目 nvr_config.json 迁移
            self._try_migrate_legacy()
            self.save()
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict) or "profiles" not in raw:
                raise ValueError("invalid profiles format")
            self.data = raw
            if not self.data.get("profiles"):
                self.data = _default_store()
            if self.data.get("active_profile") not in self.data["profiles"]:
                self.data["active_profile"] = next(iter(self.data["profiles"]))
        except Exception:
            self.data = _default_store()
            self._try_migrate_legacy()
            self.save()

    def _try_migrate_legacy(self) -> None:
        """若存在旧版 nvr_config.json,导入为「默认」档案。"""
        candidates = []
        # 开发目录
        here = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(here, "nvr_config.json"))
        # 可执行文件旁
        if getattr(sys, "frozen", False):
            candidates.append(
                os.path.join(os.path.dirname(sys.executable), "nvr_config.json")
            )
        for p in candidates:
            if not os.path.isfile(p):
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    legacy = json.load(f)
                devices = legacy.get("devices") or []
                if not devices:
                    continue
                prof = _default_profile("默认")
                prof["devices"] = devices
                prof["default"] = int(legacy.get("default", 0) or 0)
                self.data["profiles"]["默认"] = prof
                self.data["active_profile"] = "默认"
                return
            except Exception:
                continue

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        shutil.move(tmp, self.path)

    # ---- 档案操作 ----

    def list_profiles(self) -> List[str]:
        return sorted(self.data.get("profiles", {}).keys())

    def get_active_name(self) -> str:
        return self.data.get("active_profile") or "默认"

    def set_active(self, name: str) -> bool:
        if name not in self.data.get("profiles", {}):
            return False
        self.data["active_profile"] = name
        self.save()
        return True

    def get_profile(self, name: Optional[str] = None) -> Dict[str, Any]:
        name = name or self.get_active_name()
        prof = self.data["profiles"].get(name)
        if not prof:
            prof = _default_profile(name)
            self.data["profiles"][name] = prof
        # 补全缺省字段
        if "scan_options" not in prof:
            prof["scan_options"] = _default_scan_options()
        else:
            merged = _default_scan_options()
            merged.update(prof["scan_options"] or {})
            prof["scan_options"] = merged
        if "devices" not in prof:
            prof["devices"] = [_empty_device()]
        return prof

    def resolve_devices(self, name: Optional[str] = None) -> List[Dict[str, Any]]:
        """返回设备的运行时视图: 密码为空时从 keyring 补全 (不可用则原样)。"""
        from services import credentials

        profile_name = name or self.get_active_name()
        devices = self.get_profile(profile_name).get("devices") or []
        if not credentials.available():
            return [deepcopy(d) for d in devices]
        out = []
        for d in devices:
            d = deepcopy(d)
            if not (d.get("password") or ""):
                d["password"] = credentials.get_password(profile_name, d)
            out.append(d)
        return out

    def _migrate_passwords(self, devices: List[Dict[str, Any]], profile_name: str) -> None:
        """keyring 可用时把非空明文密码移入系统凭证, JSON 内置空。

        仅在 set_password 成功后清空 JSON 中的明文，避免写入失败导致丢密。
        """
        from services import credentials

        if not credentials.available():
            return
        for d in devices:
            pw = d.get("password") or ""
            if not pw:
                continue
            if credentials.set_password(profile_name, d, pw):
                d["password"] = ""

    def _purge_keyring(self, profile_name: str, devices: List[Dict[str, Any]]) -> None:
        """删除档案/设备时清理 keyring 条目（尽力而为）。"""
        from services import credentials

        if not credentials.available():
            return
        for d in devices or []:
            try:
                credentials.delete_password(profile_name, d)
            except Exception:
                pass

    def update_profile(self, name: Optional[str], profile: Dict[str, Any]) -> None:
        name = name or self.get_active_name()
        profile = deepcopy(profile)
        profile["name"] = name
        # 已从档案中移除的设备：清理其 keyring 条目
        old_devices = (self.data.get("profiles") or {}).get(name, {}).get("devices") or []
        new_keys = {
            ((d.get("ip") or "").strip(), (d.get("name") or "").strip())
            for d in (profile.get("devices") or [])
        }
        stale = [
            d
            for d in old_devices
            if ((d.get("ip") or "").strip(), (d.get("name") or "").strip()) not in new_keys
        ]
        if stale:
            self._purge_keyring(name, stale)
        self._migrate_passwords(profile.get("devices") or [], name)
        self.data["profiles"][name] = profile
        self.save()

    def create_profile(self, name: str, clone_from: Optional[str] = None) -> str:
        from services import credentials

        name = _safe_profile_name(name)
        base = name
        i = 1
        while name in self.data["profiles"]:
            name = f"{base}_{i}"
            i += 1
        if clone_from and clone_from in self.data["profiles"]:
            prof = deepcopy(self.data["profiles"][clone_from])
            prof["name"] = name
            # 克隆：把源档案 keyring 密码复制到新键（保留源，不 rekey 删除）
            if credentials.available():
                for d in prof.get("devices") or []:
                    # 优先 JSON 明文；否则从源 keyring 读
                    pw = (d.get("password") or "") or credentials.get_password(
                        clone_from, d
                    )
                    if pw and credentials.set_password(name, d, pw):
                        d["password"] = ""
        else:
            prof = _default_profile(name)
        self._migrate_passwords(prof.get("devices") or [], name)
        self.data["profiles"][name] = prof
        self.data["active_profile"] = name
        self.save()
        return name

    def rename_profile(self, old: str, new: str) -> bool:
        from services import credentials

        new = _safe_profile_name(new)
        if old not in self.data["profiles"] or not new or new in self.data["profiles"]:
            return False
        self.data["profiles"][new] = self.data["profiles"].pop(old)
        self.data["profiles"][new]["name"] = new
        if self.data.get("active_profile") == old:
            self.data["active_profile"] = new
        # keyring 账户键含 profile 名，需一并迁移
        if credentials.available():
            for d in self.data["profiles"][new].get("devices") or []:
                try:
                    credentials.rekey_password(old, new, d)
                except Exception:
                    pass
        self.save()
        return True

    def delete_profile(self, name: str) -> bool:
        if name not in self.data["profiles"]:
            return False
        if len(self.data["profiles"]) <= 1:
            return False  # 至少保留一个
        removed = self.data["profiles"].pop(name)
        self._purge_keyring(name, removed.get("devices") or [])
        if self.data.get("active_profile") == name:
            self.data["active_profile"] = next(iter(self.data["profiles"]))
        self.save()
        return True

    def export_profile(self, name: str, path: str) -> None:
        # 导出含运行时密码（便于迁移到他机）；导入侧会再迁入 keyring
        prof = deepcopy(self.get_profile(name))
        prof["devices"] = self.resolve_devices(name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(prof, f, ensure_ascii=False, indent=2)

    def import_profile(self, path: str, name: Optional[str] = None) -> str:
        with open(path, "r", encoding="utf-8") as f:
            prof = json.load(f)
        # 兼容旧 nvr_config 单文件 / 单档案导出
        if "devices" in prof and "profiles" not in prof:
            pname = _safe_profile_name(name or prof.get("name") or "导入")
            full = _default_profile(pname)
            full["devices"] = prof.get("devices") or [_empty_device()]
            full["default"] = int(prof.get("default", 0) or 0)
            if "scan_options" in prof:
                full["scan_options"].update(prof["scan_options"])
            if pname not in self.data["profiles"]:
                created = self.create_profile(pname)
                # create 已写入空默认设备，覆盖为导入内容并迁移密码
                return self._overwrite(created, full)
            return self._overwrite(pname, full)
        raise ValueError("无法识别的配置文件格式")

    def _overwrite(self, name: str, prof: Dict[str, Any]) -> str:
        prof = deepcopy(prof)
        prof["name"] = name
        self._migrate_passwords(prof.get("devices") or [], name)
        self.data["profiles"][name] = prof
        self.data["active_profile"] = name
        self.save()
        return name

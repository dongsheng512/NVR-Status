"""硬盘存储与循环覆盖检查。

B2 拆分：原 HikvisionNVR 的 get_storage_status / get_disk_overwrite_status 等。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

from nvr_core.util import _to_float, _to_int


class StorageMixin:
    def get_storage_status(self) -> List[Dict]:
        """获取硬盘状态"""
        root = self._parse("/ContentMgmt/storage", "硬盘状态")
        if root is None:
            # 部分机型仅暴露 Storage/hdd
            root = self._parse("/ContentMgmt/Storage/hdd", "硬盘列表")
        if root is None:
            return []
        drives = []
        for hdd in root.findall(".//hdd"):
            capacity = _to_int(hdd.findtext('capacity'))
            free_space = _to_int(hdd.findtext('freeSpace'))
            used_space = capacity - free_space
            usage_rate = (used_space / capacity * 100) if capacity > 0 else 0

            drive = {
                "盘符": hdd.findtext("hddName", "未知"),
                "容量TB": f"{capacity / 1024 / 1024:.1f}",
                "剩余空间TB": f"{free_space / 1024 / 1024:.1f}",
                "已用空间TB": f"{used_space / 1024 / 1024:.1f}",
                "使用率": f"{usage_rate:.1f}%",
                "状态": hdd.findtext("status", "未知"),
                "类型": hdd.findtext("hddType", "未知"),
                "属性": hdd.findtext("property", "未知"),
                "制造商": hdd.findtext("manufacturer", "未知"),
            }
            drives.append(drive)
        return drives

    # 循环覆盖相关字段名（不同固件命名差异大）
    _OW_BOOL_TAGS = (
        "overwrite",
        "overWrite",
        "enableOverWrite",
        "enableOverwrite",
        "isEnableOverWrite",
        "hddOverWrite",
        "recordOverWrite",
        "diskOverWrite",
        "recycleRecord",
        "cycleCover",
        "cycleOverwrite",
        "enableCycleCover",
        "enabledOverwrite",
    )
    _OW_STRATEGY_TAGS = (
        "hddFullStrategy",
        "diskFullStrategy",
        "hddFull",
        "fullStrategy",
        "recordFullStrategy",
        "storageFullStrategy",
        "overWriteMode",
        "overwriteMode",
        "diskFullMode",
        "hddFullMode",
    )

    @staticmethod
    def _parse_boolish(val: Optional[str]) -> Optional[bool]:
        v = (val or "").strip().lower()
        if v in ("true", "1", "yes", "enable", "enabled", "on", "open"):
            return True
        if v in ("false", "0", "no", "disable", "disabled", "off", "close"):
            return False
        return None

    @classmethod
    def _parse_overwrite_strategy(cls, val: Optional[str]) -> Optional[bool]:
        """解析硬盘满策略字符串 → True=覆盖/循环, False=停止, None=无法判定。"""
        v = (val or "").strip().lower()
        if not v:
            return None
        # 中文
        if "覆盖" in (val or "") or "循环" in (val or ""):
            if "不" in (val or "") or "禁止" in (val or "") or "停止" in (val or ""):
                return False
            return True
        if "停止" in (val or "") or "停录" in (val or ""):
            return False
        b = cls._parse_boolish(v)
        if b is not None:
            return b
        if any(k in v for k in ("overwrite", "over_write", "cover", "cycle", "recycle")):
            if any(k in v for k in ("stop", "disable", "forbid", "nooverwrite", "notoverwrite")):
                return False
            return True
        if any(k in v for k in ("stop", "halt", "forbid", "norecord", "do_not", "donot")):
            return False
        return None

    def _xml_scan_overwrite(self, root: Optional[ET.Element]) -> Optional[Tuple[bool, str, str]]:
        """在 XML 树中查找循环覆盖相关节点。返回 (enabled, tag, raw_value) 或 None。"""
        if root is None:
            return None
        for el in root.iter():
            tag = (el.tag or "").split("}")[-1]  # 去命名空间
            text = (el.text or "").strip()
            if not text:
                continue
            tag_l = tag.lower()
            # 布尔开关类
            for name in self._OW_BOOL_TAGS:
                if tag_l == name.lower():
                    b = self._parse_boolish(text)
                    if b is not None:
                        return b, tag, text
            # 策略类
            for name in self._OW_STRATEGY_TAGS:
                if tag_l == name.lower():
                    b = self._parse_overwrite_strategy(text)
                    if b is not None:
                        return b, tag, text
            # 模糊匹配 tag 名
            if "overwrite" in tag_l or "over_write" in tag_l:
                b = self._parse_boolish(text)
                if b is None:
                    b = self._parse_overwrite_strategy(text)
                if b is not None:
                    return b, tag, text
            if "fullstrategy" in tag_l or "fullmode" in tag_l or tag_l.endswith("hddfull"):
                b = self._parse_overwrite_strategy(text)
                if b is not None:
                    return b, tag, text
        return None

    def get_disk_overwrite_status(self, drives: Optional[List[Dict]] = None) -> Dict:
        """检查磁盘循环覆盖（硬盘满后是否覆盖旧录像）是否开启。

        返回:
          enabled: True/False/None（None=未能从 ISAPI 读出）
          label: 已开启 / 未开启 / 推断已开启 / 推断未开启 / 未知
          detail: 说明
          source: 来源端点或 infer
        """
        # 多端点探测（机型固件差异大）
        endpoints = (
            "/ContentMgmt/storage",
            "/ContentMgmt/Storage/hdd",
            "/ContentMgmt/Storage/quota",
            "/ContentMgmt/record/AdvanceParam",
            "/ContentMgmt/record/advancedParams",
            "/ContentMgmt/record/advancedParam",
            "/ContentMgmt/Storage/Advanced",
            "/ContentMgmt/Storage/advanced",
        )
        for ep in endpoints:
            if ep in self._cache:
                root = self._cache[ep]
            elif ep == "/ContentMgmt/storage":
                root = self._parse(ep, "硬盘状态")
            else:
                root = self._parse_endpoint_quiet(ep)
            hit = self._xml_scan_overwrite(root)
            if hit is not None:
                enabled, tag, raw = hit
                return {
                    "enabled": enabled,
                    "label": "已开启" if enabled else "未开启",
                    "detail": f"ISAPI {ep} · <{tag}>={raw}",
                    "source": ep,
                }

        # 推断：满盘仍 ok → 多半已开覆盖；满盘且 error/abnormal → 多半未开
        if drives is None:
            drives = self.get_storage_status()
        full_ok = []
        full_bad = []
        for d in drives or []:
            try:
                pct = _to_float(str(d.get("使用率") or "").replace("%", ""))
            except Exception:
                pct = 0.0
            if pct < 95:
                continue
            st = str(d.get("状态") or "").lower()
            if st in ("ok", "normal", "idle", "sleep"):
                full_ok.append(d)
            elif st and st not in ("未知", "unknown", ""):
                full_bad.append(d)
        if full_ok and not full_bad:
            return {
                "enabled": True,
                "label": "推断已开启",
                "detail": "存在使用率≥95% 且状态仍为 ok 的硬盘（循环覆盖时常见）",
                "source": "infer",
            }
        if full_bad and not full_ok:
            return {
                "enabled": False,
                "label": "推断未开启",
                "detail": "存在使用率≥95% 且状态异常的硬盘（未开覆盖时满盘易报错停录）",
                "source": "infer",
            }

        return {
            "enabled": None,
            "label": "未知",
            "detail": "设备未通过 ISAPI 暴露循环覆盖配置，请在 NVR「存储/高级」中人工确认",
            "source": "",
        }

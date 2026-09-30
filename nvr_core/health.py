"""健康状态汇总（复用各模块缓存结果）。

B2 拆分：原 HikvisionNVR 的 get_health_summary。
"""

from __future__ import annotations

from typing import Dict, List

from nvr_core.util import _to_float

# ── 通道检测状态（InputProxy/.../status 的 chanDetectResult）口径 ──
# 海康该字段比 `online` 更细，能反映通道级异常（未接入 / 网络不可达 / IP 冲突等），
# 但各固件取值并不统一。因此采用**白名单**策略：
#   · 明确正常的取值 → 判为「在线」
#   · 明确异常的取值 → 判为「异常」
#   · 其余（含空、未知、我们不认识的取值）→ 返回 ""，一律不参与判定
# 宁可漏判也不误报：新增机型出现没见过的取值时，只是少一层判断力，不会误报。
_DETECT_ONLINE_VALUES = {"connect", "connected", "normal", "ok", "online", "已配置"}
_DETECT_ABNORMAL_VALUES = {
    "notexist",            # 通道不存在 / 未接入
    "disconnect",          # 连接断开
    "disconnected",
    "neterror",            # 网络不可达
    "networkerror",
    "ipconflict",          # IP 冲突
    "ipaddressconflict",
    "userpwerror",         # 用户名 / 密码错误
    "passworderror",
    "userlocked",          # 用户被锁定
    "locked",
    "unsupported",         # 不支持
    "notsupport",
}
# 异常取值 → 人类可读原因（离线预警里用它细分原因；未命中则回退原值）
_DETECT_REASON = {
    "notexist": "通道未接入",
    "disconnect": "连接断开",
    "disconnected": "连接断开",
    "neterror": "网络不可达",
    "networkerror": "网络不可达",
    "ipconflict": "IP 冲突",
    "ipaddressconflict": "IP 冲突",
    "userpwerror": "用户名或密码错误",
    "passworderror": "用户名或密码错误",
    "userlocked": "用户被锁定",
    "locked": "用户被锁定",
    "unsupported": "不支持",
    "notsupport": "不支持",
}


def classify_detect_state(raw) -> str:
    """把 chanDetectResult 归一为 "在线" / "异常" / ""（未知，不参与判定）。"""
    s = str(raw or "").strip().lower()
    if not s:
        return ""
    if s in _DETECT_ABNORMAL_VALUES:
        return "异常"
    if s in _DETECT_ONLINE_VALUES:
        return "在线"
    return ""


def detect_reason(raw) -> str:
    """取异常取值的中文原因；无法映射时返回原值（去空白）。"""
    s = str(raw or "").strip()
    return _DETECT_REASON.get(s.lower(), s)


class HealthMixin:
    def get_health_summary(self) -> Dict:
        """获取设备健康状态汇总(复用已缓存数据)"""
        health = {
            "健康状态": "良好",
            "预警信息": [],
            "统计": {},
        }
        # 严重度排序:良好 < 警告 < 严重
        severity_rank = {"良好": 0, "警告": 1, "严重": 2}
        worst = "良好"

        def raise_to(level: str):
            nonlocal worst
            if severity_rank[level] > severity_rank[worst]:
                worst = level

        # 系统状态:内存
        status = self.get_system_status()
        if status.get("内存使用率"):
            mem_rate = _to_float(status["内存使用率"].replace("%", ""))
            if mem_rate > 90:
                raise_to("严重")
                health["预警信息"].append("内存使用率过高")
            elif mem_rate > 80:
                raise_to("警告")
                health["预警信息"].append("内存使用率偏高")

        # 硬盘状态 + 循环覆盖
        drives = self.get_storage_status()
        overwrite = self.get_disk_overwrite_status(drives)
        health["循环覆盖"] = overwrite
        ow_enabled = overwrite.get("enabled")  # True / False / None
        ow_label = overwrite.get("label") or "未知"

        def _drive_bad(d: Dict) -> bool:
            # 状态缺失/未知不计为异常(与 storage 推断逻辑一致);ok/normal/sleep/idle 均正常
            st = str(d.get("状态") or "").strip().lower()
            return bool(st) and st not in (
                "ok", "normal", "sleep", "idle", "未知", "unknown",
            )

        bad_drives = [d for d in drives if _drive_bad(d)]
        if bad_drives:
            raise_to("严重")
            health["预警信息"].append(f"{len(bad_drives)}块硬盘状态异常")

        full_drives = [
            d
            for d in drives
            if _to_float(str(d.get("使用率") or "0").replace("%", "")) > 95
        ]
        if full_drives:
            n_full = len(full_drives)
            if ow_enabled is False:
                # 未开循环覆盖：满盘将停录，升级严重
                raise_to("严重")
                health["预警信息"].append(
                    f"{n_full}块硬盘空间已满/即将用尽，且循环覆盖{ow_label}，满盘后可能停止录像"
                )
            elif ow_enabled is True:
                raise_to("警告")
                health["预警信息"].append(
                    f"{n_full}块硬盘空间已满/即将用尽（循环覆盖{ow_label}，将覆盖旧录像继续录）"
                )
            else:
                raise_to("警告")
                health["预警信息"].append(
                    f"{n_full}块硬盘空间已满/即将用尽（循环覆盖状态{ow_label}，请人工确认）"
                )
        else:
            # 非满盘：未开启则预警；已开启/未知仅记入统计与结果区展示
            if ow_enabled is False:
                raise_to("警告")
                health["预警信息"].append(
                    f"循环覆盖{ow_label}：硬盘写满后将停止录像，建议在 NVR 存储设置中开启"
                )

        sleeping_drives = [
            d for d in drives if d.get("状态") in ("sleep", "idle")
        ]
        if sleeping_drives:
            health["预警信息"].append(f"{len(sleeping_drives)}块硬盘处于休眠状态")

        # 摄像头在线检查
        # 以 `在线` 为主判据，并用 `检测状态`(chanDetectResult) 做交叉校验：
        #   · online=false            → 离线
        #   · online=true             → 在线（若检测状态明确异常，另记一条通道异常）
        #   · online 缺失/取值异常时   → 用检测状态补判（connect→在线，异常→离线）
        #   · 两者都非明确取值         → 未确认（不误报离线）
        cameras = self.get_cameras()
        offline: List[Dict] = []
        detect_bad: List[Dict] = []
        unconfirmed = 0
        online_n = 0
        for c in cameras:
            raw_online = str(c.get("在线") or "").strip().lower()
            det = classify_detect_state(c.get("检测状态"))
            if raw_online == "false":
                offline.append(c)
            elif raw_online == "true":
                online_n += 1
                if det == "异常":
                    detect_bad.append(c)
            elif det == "在线":
                online_n += 1
            elif det == "异常":
                offline.append(c)
            else:
                unconfirmed += 1

        if offline:
            raise_to("严重")
            names = "、".join(c["名称"] for c in offline if c["名称"] != "未知")
            # 原因细分：优先用通道检测状态解释离线原因（未接入 / 网络不可达 …）
            reasons: Dict[str, int] = {}
            for c in offline:
                det = classify_detect_state(c.get("检测状态"))
                if det == "异常":
                    label = detect_reason(c.get("检测状态"))
                    reasons[label] = reasons.get(label, 0) + 1
            detail = ""
            if reasons:
                detail = "：" + "、".join(
                    f"{k} {v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])
                )
            health["预警信息"].append(
                f"{len(offline)}个摄像头离线"
                + (detail if detail else (f"({names})" if names else ""))
            )

        if detect_bad:
            raise_to("警告")
            health["预警信息"].append(
                f"{len(detect_bad)}个在线通道检测状态异常"
                "(IP 冲突 / 网络不可达等，建议核对通道)"
            )

        # 录像:计划 / 音频 / 落盘
        records = self.get_recording_status()
        # 是否实际查询了落盘（未查则录像综合状态也为跳过，预警区不展示相关结论）
        disk_checked = bool(self.check_disk_recording or self.deep_av_check)
        stats = {
            "通道总数": len(records),
            "计划已配置": 0,
            "计划未配置": 0,
            "录像正常": 0,
            "录像异常": 0,
            "录像未知": 0,
            "录像跳过": 0,
            "含音频": 0,
            "不含音频": 0,
            "音频未知": 0,
            "落盘正常": 0,
            "落盘异常": 0,
            "落盘未知": 0,
            "落盘跳过": 0,
            "落盘已检查": disk_checked,
            "录像已检查": disk_checked,  # 录像正常与否依赖落盘检索
            "视频抽检正常": 0,
            "视频抽检异常": 0,
            "音频抽检正常": 0,
            "音频抽检异常": 0,
            "音频抽检警告": 0,
            "音频抽检未知": 0,
            "摄像头在线": online_n,
            "摄像头离线": len(offline),
            "摄像头状态未确认": unconfirmed,
            "通道检测异常": len(detect_bad),
            "摄像头总数": len(cameras),
            "深度抽检": self.deep_av_check,
            "循环覆盖": ow_label,
            "循环覆盖已开启": ow_enabled,
        }

        if records:
            for r in records:
                if r["已启用录像"]:
                    stats["计划已配置"] += 1
                else:
                    stats["计划未配置"] += 1

                ok = r.get("录像是否正常")
                if ok == "跳过":
                    stats["录像跳过"] += 1
                elif ok == "正常":
                    stats["录像正常"] += 1
                elif ok in ("异常", "未配置"):
                    stats["录像异常"] += 1
                else:
                    stats["录像未知"] += 1

                sa = r.get("录像含音频")
                if sa is True:
                    stats["含音频"] += 1
                elif sa is False:
                    stats["不含音频"] += 1
                else:
                    stats["音频未知"] += 1

                disk = r.get("落盘状态")
                if disk == "跳过":
                    stats["落盘跳过"] += 1
                elif disk == "正常":
                    stats["落盘正常"] += 1
                elif disk == "异常":
                    stats["落盘异常"] += 1
                else:
                    stats["落盘未知"] += 1

                if r.get("视频抽检") == "正常":
                    stats["视频抽检正常"] += 1
                elif r.get("视频抽检") == "异常":
                    stats["视频抽检异常"] += 1
                if r.get("音频抽检") == "正常":
                    stats["音频抽检正常"] += 1
                elif r.get("音频抽检") == "异常":
                    stats["音频抽检异常"] += 1
                elif r.get("音频抽检") == "警告":
                    stats["音频抽检警告"] += 1
                elif r.get("音频抽检") == "未知":
                    stats["音频抽检未知"] += 1

            # 计划/音频来自配置查询，快速模式仍可预警
            if stats["计划未配置"]:
                raise_to("严重")
                health["预警信息"].append(f"{stats['计划未配置']}个通道未配置录像计划")

            if stats["不含音频"]:
                raise_to("警告")
                health["预警信息"].append(f"{stats['不含音频']}个通道未开启录像音频(SaveAudio=false)")

            # 落盘 / 录像综合结论：仅在实际查询后写入预警
            if disk_checked:
                if stats["落盘异常"]:
                    raise_to("严重")
                    bad = [r for r in records if r.get("落盘状态") == "异常"]
                    sample = "、".join(
                        f"{r['通道']}"
                        + (f"({r['名称']})" if r.get("名称") and r["名称"] != "未知" else "")
                        for r in bad[:5]
                    )
                    more = f" 等{len(bad)}路" if len(bad) > 5 else f"({sample})" if sample else ""
                    if len(bad) <= 5 and sample:
                        health["预警信息"].append(
                            f"{stats['落盘异常']}个通道近期无录像: {sample}"
                        )
                    else:
                        health["预警信息"].append(
                            f"{stats['落盘异常']}个通道近期无录像{more}"
                        )

                # 「落盘未知」口径（与「音频抽检未知」保持一致）：
                # 成因多为瞬时检索超时，单路抖动不应把整机降级为警告。
                # 仅当**实际检索到的通道全部未知**（整体检索失败）才升级为警告；
                # 部分未知只进统计与结果区（录像卡片已有提示），不参与健康判定。
                disk_attempted = (
                    stats["落盘正常"] + stats["落盘异常"] + stats["落盘未知"]
                )
                if (
                    stats["落盘未知"]
                    and disk_attempted
                    and stats["落盘未知"] >= disk_attempted
                ):
                    raise_to("警告")
                    health["预警信息"].append(
                        f"{stats['落盘未知']}个通道近期录像状态未知(整体检索失败)"
                    )

            if self.deep_av_check:
                if stats["视频抽检异常"]:
                    raise_to("严重")
                    health["预警信息"].append(
                        f"{stats['视频抽检异常']}个通道短时视频抽检异常"
                    )
                if stats["音频抽检异常"]:
                    raise_to("严重")
                    health["预警信息"].append(
                        f"{stats['音频抽检异常']}个通道短时音频抽检异常(无音轨)"
                    )
                if stats["音频抽检警告"]:
                    raise_to("警告")
                    health["预警信息"].append(
                        f"{stats['音频抽检警告']}个通道音频疑似静音/电平过低"
                    )
                # 「音频抽检未知」与「落盘未知」同口径：部分未知（瞬时拉流超时）不降级，
                # 仅当实际抽到的通道全部未确认（整体拉流失败）才升级为警告。
                audio_attempted = (
                    stats["音频抽检正常"]
                    + stats["音频抽检异常"]
                    + stats["音频抽检警告"]
                    + stats["音频抽检未知"]
                )
                if (
                    stats["音频抽检未知"]
                    and audio_attempted
                    and stats["音频抽检未知"] >= audio_attempted
                ):
                    raise_to("警告")
                    health["预警信息"].append(
                        f"{stats['音频抽检未知']}个通道音频抽检未确认(整体拉流失败)"
                    )

        health["统计"] = stats
        health["健康状态"] = worst
        return health

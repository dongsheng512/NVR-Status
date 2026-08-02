"""健康状态汇总（复用各模块缓存结果）。

B2 拆分：原 HikvisionNVR 的 get_health_summary。
"""

from __future__ import annotations

from typing import Dict

from nvr_core.util import _to_float


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

        bad_drives = [d for d in drives if d["状态"] not in ("ok", "sleep", "idle")]
        if bad_drives:
            raise_to("严重")
            health["预警信息"].append(f"{len(bad_drives)}块硬盘状态异常")

        full_drives = [d for d in drives if _to_float(d["使用率"].replace("%", "")) > 95]
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

        sleeping_drives = [d for d in drives if d["状态"] in ["sleep", "idle"]]
        if sleeping_drives:
            health["预警信息"].append(f"{len(sleeping_drives)}块硬盘处于休眠状态")

        # 摄像头离线检查
        cameras = self.get_cameras()
        offline = [c for c in cameras if c.get("在线") == "false"]
        if offline:
            raise_to("严重")
            names = "、".join(c["名称"] for c in offline if c["名称"] != "未知")
            health["预警信息"].append(
                f"{len(offline)}个摄像头离线" + (f"({names})" if names else "")
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
            "摄像头在线": sum(1 for c in cameras if c.get("在线") == "true"),
            "摄像头离线": len(offline),
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

                if stats["落盘未知"]:
                    raise_to("警告")
                    health["预警信息"].append(
                        f"{stats['落盘未知']}个通道近期录像状态未知(检索失败)"
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

        health["统计"] = stats
        health["健康状态"] = worst
        return health

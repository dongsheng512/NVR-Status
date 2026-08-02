"""录像计划 / 近期落盘检索 / 繁忙时段窗口。

B2 拆分：原 HikvisionNVR 的 get_recording_status 与相关检索逻辑。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Dict, List, Optional, Tuple

from nvr_core.util import _parse_hik_time, _to_int


class RecordingMixin:
    # 录像模式简称映射
    _REC_MODE = {
        "CMR": "连续录像",
        "MDR": "移动侦测",
        "ADR": "报警录像",
        "AE": "事件触发",
    }

    @staticmethod
    def _physical_channel(tr: ET.Element) -> str:
        """从 Track 节点解析物理通道号(优先 SrcChannel)。"""
        src = tr.findtext(".//SrcChannel")
        if src:
            return src
        raw = tr.findtext("Channel") or tr.findtext("id") or "未知"
        tid = _to_int(raw, default=-1)
        # 主码流 track 常见编码: 101/201/.../6401 -> 通道 1/2/.../64
        if tid >= 100 and tid % 100 == 1:
            return str(tid // 100)
        return raw

    @staticmethod
    def _save_audio(tr: ET.Element) -> Optional[bool]:
        """解析 SaveAudio 扩展字段。None 表示接口未返回该字段。"""
        for el in tr.iter():
            if el.tag == "SaveAudio":
                if el.text is None:
                    return None
                return el.text.strip().lower() == "true"
        return None

    def _search_track_recent(self, track_id: str, lookback_minutes: int) -> Dict:
        """CMSearch 查询某 track 近 lookback_minutes 分钟是否有录像片段。"""
        empty = {
            "ok": False,
            "status": "未知",
            "detail": "",
            "segments": 0,
            "latest_end": None,
            "playback_uri": None,
            "seg_start": None,
            "seg_end": None,
        }
        now = datetime.now(timezone.utc)
        start = now - timedelta(minutes=lookback_minutes)
        end = now + timedelta(minutes=5)
        start_s = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_s = end.strftime("%Y-%m-%dT%H:%M:%SZ")
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<CMSearchDescription>
  <searchID>01234567-89AB-CDEF-0123-456789ABCDEF</searchID>
  <trackList>
    <trackID>{track_id}</trackID>
  </trackList>
  <timeSpanList>
    <timeSpan>
      <startTime>{start_s}</startTime>
      <endTime>{end_s}</endTime>
    </timeSpan>
  </timeSpanList>
  <maxResults>10</maxResults>
  <searchResultPostion>0</searchResultPostion>
</CMSearchDescription>"""

        code, text = self._post(
            "/ContentMgmt/search",
            body,
            tag=f"录像检索 track {track_id}",
            quiet=True,
            timeout=20,
            use_thread_session=True,
        )
        if code != 200 or not text:
            empty["detail"] = f"检索失败(HTTP {code})" if code > 0 else "检索失败"
            return empty

        text = re.sub(r'\s+xmlns="[^"]+"', '', text)
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            empty["detail"] = "检索结果解析失败"
            return empty

        if root.tag == "ResponseStatus":
            empty["detail"] = root.findtext("statusString", "检索失败")
            return empty

        matches = root.findall(".//searchMatchItem")
        latest_end: Optional[datetime] = None
        covers_now = False
        # 允许当前段 endTime 略早于 now 的容差(秒):连续录像切段/时钟偏差
        lag_grace = 900  # 15 分钟
        best_uri: Optional[str] = None
        best_st: Optional[datetime] = None
        best_et: Optional[datetime] = None
        best_score: Tuple[int, float] = (-1, -1.0)

        for item in matches:
            st = _parse_hik_time(item.findtext(".//startTime"))
            et = _parse_hik_time(item.findtext(".//endTime"))
            uri = unescape((item.findtext(".//playbackURI") or "").strip())
            if et is not None and (latest_end is None or et > latest_end):
                latest_end = et
            covers = False
            if st is not None and et is not None and st <= now <= et + timedelta(seconds=lag_grace):
                covers_now = True
                covers = True
            elif et is not None and et >= now - timedelta(seconds=lag_grace):
                covers_now = True
                covers = True
            # 优先选覆盖当前的片段,其次 end 最晚
            score = (2 if covers else 0, et.timestamp() if et else 0.0)
            if uri and score >= best_score:
                best_score = score
                best_uri = uri
                best_st, best_et = st, et

        if not matches:
            return {
                "ok": False,
                "status": "异常",
                "detail": f"近{lookback_minutes}分钟无录像片段",
                "segments": 0,
                "latest_end": None,
                "playback_uri": None,
                "seg_start": None,
                "seg_end": None,
            }

        if covers_now:
            detail = "近期有录像"
            if latest_end is not None:
                detail += f"(至 {latest_end.astimezone().strftime('%H:%M:%S')})"
            return {
                "ok": True,
                "status": "正常",
                "detail": detail,
                "segments": len(matches),
                "latest_end": latest_end,
                "playback_uri": best_uri,
                "seg_start": best_st,
                "seg_end": best_et,
            }

        # 有历史片段但未覆盖到当前:仍判异常(可能停录)
        end_txt = latest_end.astimezone().strftime("%Y-%m-%d %H:%M:%S") if latest_end else "未知"
        return {
            "ok": False,
            "status": "异常",
            "detail": f"仅有较早片段(最晚 {end_txt})",
            "segments": len(matches),
            "latest_end": latest_end,
            "playback_uri": best_uri,
            "seg_start": best_st,
            "seg_end": best_et,
        }

    def _busy_hours_window(self) -> Tuple[datetime, datetime, str]:
        """计算优先抽检的繁忙时段(本地 10:00-18:00 默认)。

        返回 (window_start_utc, window_end_utc, 说明)。
        - busy_days_ago > 0: 取「N 天前」当天完整繁忙时段
        - busy_days_ago == 0（今天）:
          - 当前在繁忙时段内: 取 [当日 busy_start, min(现在, busy_end)]
          - 当前早于 busy_start: 取昨日完整繁忙时段
          - 当前晚于 busy_end: 取今日完整繁忙时段
        """
        local = datetime.now().astimezone()
        sh, eh = self.busy_start_hour, self.busy_end_hour
        days_ago = int(getattr(self, "busy_days_ago", 0) or 0)

        def day_window(day: datetime) -> Tuple[datetime, datetime]:
            start = day.replace(hour=sh, minute=0, second=0, microsecond=0)
            # end_hour=18 表示 18:00 整点为止
            if eh >= 24:
                end = (day + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
            else:
                end = day.replace(hour=eh, minute=0, second=0, microsecond=0)
            return start, end

        # 指定历史某一天：直接用该日完整繁忙时段
        if days_ago > 0:
            target = local - timedelta(days=days_ago)
            win_s, win_e = day_window(target)
            day_label = (
                "昨天" if days_ago == 1 else f"{days_ago} 天前"
            )
            label = (
                f"{day_label} {win_s.strftime('%m-%d')} "
                f"{sh:02d}:00-{eh:02d}:00"
            )
            return (
                win_s.astimezone(timezone.utc),
                win_e.astimezone(timezone.utc),
                label,
            )

        today_s, today_e = day_window(local)

        if today_s <= local < today_e:
            win_s, win_e = today_s, min(local - timedelta(seconds=30), today_e)
            if win_e <= win_s:
                win_e = min(today_e, win_s + timedelta(minutes=5))
            label = (
                f"今日 {win_s.strftime('%H:%M')}-{win_e.strftime('%H:%M')} "
                f"(繁忙时段 {sh:02d}:00-{eh:02d}:00)"
            )
        elif local < today_s:
            yday = local - timedelta(days=1)
            win_s, win_e = day_window(yday)
            label = (
                f"昨日 {win_s.strftime('%m-%d %H:%M')}-{win_e.strftime('%H:%M')} "
                f"(当前未到 {sh:02d}:00)"
            )
        else:
            win_s, win_e = today_s, today_e
            label = (
                f"今日 {win_s.strftime('%H:%M')}-{win_e.strftime('%H:%M')} "
                f"(当前已过 {eh:02d}:00)"
            )

        return (
            win_s.astimezone(timezone.utc),
            win_e.astimezone(timezone.utc),
            label,
        )

    def _pick_busy_clip_times(self, seconds: int) -> Tuple[datetime, datetime, str]:
        """在繁忙时段窗口内选取短抽检起止时间(UTC)。

        优先取窗口内接近 14:00 的人流高峰;若当前在繁忙时段且已过 14:00,
        则取窗口末尾附近(更接近现在且仍在繁忙段)。
        """
        win_s, win_e, label = self._busy_hours_window()
        local = datetime.now().astimezone()
        # 目标锚点: 本地 14:00
        peak_local = local.replace(hour=14, minute=0, second=0, microsecond=0)
        # 若窗口是昨天, peak 也落在昨天
        win_s_local = win_s.astimezone()
        if win_s_local.date() != local.date():
            peak_local = win_s_local.replace(hour=14, minute=0, second=0, microsecond=0)
        peak = peak_local.astimezone(timezone.utc)

        sec = max(3, seconds)
        if win_s <= peak <= win_e:
            clip_e = min(peak + timedelta(seconds=sec), win_e)
            clip_s = clip_e - timedelta(seconds=sec)
            if clip_s < win_s:
                clip_s = win_s
                clip_e = min(win_e, clip_s + timedelta(seconds=sec))
            where = "14:00附近"
        else:
            # 取窗口末尾(当前在繁忙时段时即接近现在)
            clip_e = win_e
            clip_s = clip_e - timedelta(seconds=sec)
            if clip_s < win_s:
                clip_s = win_s
                clip_e = min(win_e, clip_s + timedelta(seconds=sec))
            where = "时段末尾"

        clip_label = (
            f"{label}; 抽检点 {clip_s.astimezone().strftime('%m-%d %H:%M:%S')}"
            f"({where})"
        )
        return clip_s, clip_e, clip_label

    def _search_track_in_range(
        self,
        track_id: str,
        start: datetime,
        end: datetime,
    ) -> Dict:
        """CMSearch 指定时间范围,返回含 playback_uri 的结果。"""
        empty = {
            "ok": False,
            "playback_uri": None,
            "seg_start": None,
            "seg_end": None,
            "detail": "",
        }
        # 查询窗口略放大,便于命中跨段录像
        q_start = (start - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        q_end = (end + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<CMSearchDescription>
  <searchID>01234567-89AB-CDEF-0123-456789ABCDEF</searchID>
  <trackList>
    <trackID>{track_id}</trackID>
  </trackList>
  <timeSpanList>
    <timeSpan>
      <startTime>{q_start}</startTime>
      <endTime>{q_end}</endTime>
    </timeSpan>
  </timeSpanList>
  <maxResults>10</maxResults>
  <searchResultPostion>0</searchResultPostion>
</CMSearchDescription>"""
        code, text = self._post(
            "/ContentMgmt/search",
            body,
            tag=f"繁忙时段检索 track {track_id}",
            quiet=True,
            timeout=20,
            use_thread_session=True,
        )
        if code != 200 or not text:
            empty["detail"] = f"检索失败(HTTP {code})" if code > 0 else "检索失败"
            return empty
        text = re.sub(r'\s+xmlns="[^"]+"', '', text)
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            empty["detail"] = "检索结果解析失败"
            return empty
        if root.tag == "ResponseStatus":
            empty["detail"] = root.findtext("statusString", "检索失败")
            return empty

        matches = root.findall(".//searchMatchItem")
        if not matches:
            empty["detail"] = "繁忙时段无录像片段"
            return empty

        # 选与目标 clip 重叠最好、或 end 最接近 clip 的片段
        best_uri = None
        best_st = None
        best_et = None
        best_score: Tuple[int, float] = (-1, -1.0)
        for item in matches:
            st = _parse_hik_time(item.findtext(".//startTime"))
            et = _parse_hik_time(item.findtext(".//endTime"))
            uri = unescape((item.findtext(".//playbackURI") or "").strip())
            if not uri:
                continue
            overlaps = 0
            if st is not None and et is not None and st <= end and et >= start:
                overlaps = 1
            score = (overlaps, et.timestamp() if et else 0.0)
            if score >= best_score:
                best_score = score
                best_uri = uri
                best_st, best_et = st, et

        if not best_uri:
            empty["detail"] = "繁忙时段无可用回放URI"
            return empty
        return {
            "ok": True,
            "playback_uri": best_uri,
            "seg_start": best_st,
            "seg_end": best_et,
            "detail": "ok",
        }

    def get_recording_status(self) -> List[Dict]:
        """获取各通道录像计划、是否含音频、近期落盘是否正常。

        通过 /ContentMgmt/record/tracks 解析:
        - ScheduleAction/Actions/Record=true 表示该时段已配置录像
        - ActionRecordingMode 表示录像模式(CMR连续/MDR动测/ADR报警/AE事件)
        - CustomExtension/SaveAudio 表示是否保存音频
        并通过 CMSearch 检查近 lookback_minutes 是否有实际录像。

        注:Track 的顶层 Enable 字段在网络摄像机NVR上恒为 false,不可信,
        必须看 ScheduleAction 里的 Record 标志。
        """
        if self._recording_cache is not None:
            return self._recording_cache

        root = self._parse("/ContentMgmt/record/tracks", "录像计划")
        if root is None:
            self._recording_cache = []
            return []

        cameras = self.get_cameras()
        name_map = {c["id"]: c.get("名称", "未知") for c in cameras}
        online_map = {c["id"]: c.get("在线", "unknown") for c in cameras}

        records: List[Dict] = []
        for tr in root.findall("Track"):
            track_id = tr.findtext("id") or tr.findtext("Channel") or "未知"
            channel = self._physical_channel(tr)
            actions = tr.findall(".//Actions")
            rec_actions = [a for a in actions if a.findtext("Record") == "true"]
            modes: Dict[str, int] = {}
            for a in actions:
                m = a.findtext("ActionRecordingMode", "")
                if m:
                    modes[m] = modes.get(m, 0) + 1
            total = len(actions)
            enabled = bool(rec_actions)
            main_mode = max(modes, key=modes.get) if modes else tr.findtext("DefaultRecordingMode", "未知")
            save_audio = self._save_audio(tr)

            records.append({
                "track_id": track_id,
                "通道": channel,
                "名称": name_map.get(channel, "未知"),
                "在线": online_map.get(channel, "unknown"),
                "已启用录像": enabled,
                "录像模式": self._REC_MODE.get(main_mode, main_mode),
                "录像时段": f"{len(rec_actions)}/{total}" if total else "0/0",
                "录像含音频": save_audio,  # True/False/None
                "落盘状态": "跳过",
                "落盘详情": "未检查",
                "playback_uri": None,
                "seg_start": None,
                "seg_end": None,
                "视频抽检": "跳过",
                "音频抽检": "跳过",
                "抽检详情": "未启用深度抽检",
                "录像是否正常": None,  # 综合后填充
            })

        records.sort(key=lambda r: _to_int(r["通道"], default=1 << 30))

        # 深度抽检依赖 URI,若开启则强制做落盘检索
        need_search = self.check_disk_recording or self.deep_av_check

        # 并发 CMSearch 检查近期落盘
        # 落盘阶段进度区间：有深度抽检时 28%→70%，否则 28%→92%
        disk_lo = 0.28
        disk_hi = 0.70 if self.deep_av_check else 0.92
        if need_search and records:
            total_n = len(records)
            self._log(
                f"正在检查近 {self.lookback_minutes} 分钟是否有录像"
                f"({total_n} 通道, 并发 {self.search_workers})..."
            )
            self._progress(
                f"近期录像检查 0/{total_n}",
                current=0,
                total=total_n,
                phase="disk",
                overall=disk_lo,
            )
            results: Dict[str, Dict] = {}

            def _job(tid: str) -> Tuple[str, Dict]:
                return tid, self._search_track_recent(tid, self.lookback_minutes)

            done_n = 0
            with ThreadPoolExecutor(max_workers=self.search_workers) as pool:
                futures = [pool.submit(_job, r["track_id"]) for r in records]
                for fut in as_completed(futures):
                    tid, res = fut.result()
                    results[tid] = res
                    done_n += 1
                    frac = done_n / total_n if total_n else 1.0
                    overall = disk_lo + (disk_hi - disk_lo) * frac
                    if (
                        done_n == 1
                        or done_n == total_n
                        or done_n % max(1, total_n // 25) == 0
                    ):
                        self._progress(
                            f"近期录像检查 {done_n}/{total_n}",
                            current=done_n,
                            total=total_n,
                            phase="disk",
                            overall=overall,
                        )

            for r in records:
                res = results.get(r["track_id"], {
                    "ok": False,
                    "status": "未知",
                    "detail": "未返回结果",
                })
                r["落盘状态"] = res.get("status", "未知")
                r["落盘详情"] = res.get("detail", "")
                r["playback_uri"] = res.get("playback_uri")
                r["seg_start"] = res.get("seg_start")
                r["seg_end"] = res.get("seg_end")
            self._progress(
                f"近期录像检查完成 {total_n}/{total_n}",
                current=total_n,
                total=total_n,
                phase="disk",
                overall=disk_hi,
            )
        else:
            for r in records:
                r["落盘状态"] = "跳过"
                r["落盘详情"] = "已跳过近期录像检查"
            self._progress(
                "已跳过近期录像检查",
                phase="disk",
                overall=disk_hi,
            )

        # 可选:短时 RTSP 音视频抽检
        if self.deep_av_check:
            self._run_deep_av_checks(records)

        # 综合「录像是否正常」
        for r in records:
            r["录像是否正常"] = self._judge_recording_ok(r)

        self._recording_cache = records
        return records

    @staticmethod
    def _judge_recording_ok(r: Dict) -> str:
        """综合计划 + 落盘 + 深度抽检,给出通道录像是否正常。

        返回: 正常 / 异常 / 未知 / 未配置 / 跳过
        跳过: 未做落盘检索时不判定录像是否正常。
        """
        if not r.get("已启用录像"):
            return "未配置"

        disk = r.get("落盘状态")
        # 未做落盘检查时，不给出录像正常/异常结论
        if disk == "跳过":
            return "跳过"
        if disk == "异常":
            return "异常"
        if disk not in ("正常",):
            return "未知"

        # 深度抽检:视频轨失败直接异常
        v = r.get("视频抽检")
        if v == "异常":
            return "异常"
        if v == "未知" and r.get("抽检详情"):
            # 工具缺失等
            if disk == "正常":
                return "正常"  # 落盘正常但抽检未能执行,不降为未知录像
            return "未知"

        return "正常"

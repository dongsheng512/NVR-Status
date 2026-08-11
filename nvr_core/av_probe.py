"""短时 RTSP 音视频抽检（ffmpeg/ffprobe）。

B2 拆分：原 HikvisionNVR 的深度音视频抽检逻辑。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from nvr_core.util import _safe_filename


class AVProbeMixin:
    # RTSP 时间串: 海康 playbackURI 里 Z 后缀在不同固件上既可能表示
    # 真 UTC, 也可能表示设备本地墙钟(数字是本地时,后缀仍写 Z)。
    # 写侧默认用本地墙钟(_fmt_rtsp_time); 读侧需与 CMSearch 段时间对齐判定。

    def _inject_rtsp_auth(self, uri: str) -> str:
        """向 rtsp:// 注入 user:pass@；已有 userinfo 则替换。"""
        if not uri or not uri.startswith("rtsp://"):
            return uri
        rest = uri[len("rtsp://") :]
        slash = rest.find("/")
        authority = rest if slash < 0 else rest[:slash]
        path = "" if slash < 0 else rest[slash:]
        if "@" in authority:
            authority = authority.rsplit("@", 1)[-1]
        user = quote(self.username, safe="")
        pwd = quote(self.password, safe="")
        return f"rtsp://{user}:{pwd}@{authority}{path}"

    def _fmt_rtsp_time_mode(self, dt: datetime, mode: str) -> str:
        """按约定格式化 RTSP starttime/endtime。

        mode='local': 设备本地墙钟 + Z（常见国行 NVR，OSD 与墙钟一致）
        mode='utc': 真 UTC + Z（部分固件 / 文档字面含义）
        """
        if mode == "utc":
            return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return self._fmt_rtsp_time(dt)

    def _parse_rtsp_uri_time(self, text: str, mode: str) -> datetime:
        """解析 URI 内 YYYYMMDDTHHMMSSZ → UTC aware。"""
        naive = datetime.strptime(text, "%Y%m%dT%H%M%SZ")
        if mode == "utc":
            return naive.replace(tzinfo=timezone.utc)
        return naive.replace(tzinfo=self._get_device_tz()).astimezone(timezone.utc)

    def _detect_rtsp_time_mode(
        self,
        uri_start_raw: str,
        seg_start: Optional[datetime],
    ) -> str:
        """根据 CMSearch 段起点与 URI 数字的匹配程度判定 local / utc。

        无对照信息时默认 local（与写侧 _fmt_rtsp_time 一致，国行最常见）。
        """
        if not uri_start_raw or seg_start is None:
            return "local"
        try:
            as_utc = self._parse_rtsp_uri_time(uri_start_raw, "utc")
            as_local = self._parse_rtsp_uri_time(uri_start_raw, "local")
        except ValueError:
            return "local"
        seg = seg_start if seg_start.tzinfo else seg_start.replace(tzinfo=timezone.utc)
        d_utc = abs((as_utc - seg).total_seconds())
        d_local = abs((as_local - seg).total_seconds())
        if d_utc + 2 < d_local:
            return "utc"
        return "local"

    def _compute_clip_window(
        self,
        uri_s: datetime,
        uri_e: datetime,
        seconds: int,
        clip_start: Optional[datetime] = None,
        clip_end: Optional[datetime] = None,
    ) -> Tuple[datetime, datetime]:
        """在录像段 [uri_s, uri_e] 内切出短抽检窗（UTC）。"""
        now = datetime.now(timezone.utc)
        sec = max(1, int(seconds))
        if clip_start is not None and clip_end is not None:
            cs = clip_start if clip_start.tzinfo else clip_start.replace(tzinfo=timezone.utc)
            ce = clip_end if clip_end.tzinfo else clip_end.replace(tzinfo=timezone.utc)
            clip_s = max(uri_s, cs)
            clip_e = min(uri_e, ce)
            if clip_e <= clip_s:
                clip_s = uri_s
                clip_e = min(uri_e, uri_s + timedelta(seconds=sec))
            if (clip_e - clip_s).total_seconds() < max(1, sec // 2):
                clip_e = min(uri_e, clip_s + timedelta(seconds=sec))
        else:
            clip_e = min(now - timedelta(seconds=3), uri_e - timedelta(seconds=2))
            if clip_e <= uri_s:
                clip_e = uri_e
            clip_s = clip_e - timedelta(seconds=sec)
            if clip_s < uri_s:
                clip_s = uri_s
            if clip_e <= clip_s:
                clip_e = min(uri_e, clip_s + timedelta(seconds=sec))

        if clip_e <= clip_s:
            clip_s = uri_s
            clip_e = min(uri_e, uri_s + timedelta(seconds=sec))
        if clip_e <= clip_s:
            clip_e = clip_s + timedelta(seconds=sec)
        return clip_s, clip_e

    def _rewrite_rtsp_times(
        self,
        playback_uri: str,
        clip_s: datetime,
        clip_e: datetime,
        mode: str,
    ) -> str:
        """改写 starttime/endtime，去掉 size，保留其余查询参数。"""
        short = playback_uri
        short = re.sub(
            r"starttime=\d{8}T\d{6}Z",
            f"starttime={self._fmt_rtsp_time_mode(clip_s, mode)}",
            short,
            flags=re.I,
        )
        short = re.sub(
            r"endtime=\d{8}T\d{6}Z",
            f"endtime={self._fmt_rtsp_time_mode(clip_e, mode)}",
            short,
            flags=re.I,
        )
        short = re.sub(r"[&?]size=\d+", "", short, flags=re.I)
        short = re.sub(r"\?&", "?", short)
        short = re.sub(r"&&+", "&", short)
        short = short.rstrip("?&")
        return short

    def _build_short_rtsp(
        self,
        playback_uri: str,
        seg_start: Optional[datetime],
        seg_end: Optional[datetime],
        seconds: int,
        clip_start: Optional[datetime] = None,
        clip_end: Optional[datetime] = None,
    ) -> Optional[str]:
        """构造首选短时 RTSP（兼容旧调用）；完整候选见 _build_short_rtsp_candidates。"""
        cands = self._build_short_rtsp_candidates(
            playback_uri,
            seg_start,
            seg_end,
            seconds,
            clip_start=clip_start,
            clip_end=clip_end,
        )
        return cands[0][1] if cands else None

    def _build_short_rtsp_candidates(
        self,
        playback_uri: str,
        seg_start: Optional[datetime],
        seg_end: Optional[datetime],
        seconds: int,
        clip_start: Optional[datetime] = None,
        clip_end: Optional[datetime] = None,
    ) -> List[Tuple[str, str]]:
        """生成短时 RTSP 候选列表 [(标签, url), ...]。

        顺序：
          1. 改写短窗 + 自动检测的时间模式（local/utc）
          2. 改写短窗 + 另一模式（应对固件差异 / 400）
          3. 原 URI 仅注入鉴权，靠 ffmpeg -t 截断（最保守回退）

        根因：旧实现把 URI 内 starttime 一律当 UTC 解析，却用本地墙钟写回，
        东八区会偏移 8 小时 → 设备 400 Bad Request。
        """
        if not playback_uri or not playback_uri.startswith("rtsp://"):
            return []

        now = datetime.now(timezone.utc)
        m = re.search(
            r"starttime=(\d{8}T\d{6}Z).*endtime=(\d{8}T\d{6}Z)",
            playback_uri,
            re.I,
        )
        mode = "local"
        uri_s: Optional[datetime] = None
        uri_e: Optional[datetime] = None

        if m:
            mode = self._detect_rtsp_time_mode(m.group(1), seg_start)
            try:
                uri_s = self._parse_rtsp_uri_time(m.group(1), mode)
                uri_e = self._parse_rtsp_uri_time(m.group(2), mode)
            except ValueError:
                uri_s = uri_e = None

        def _aware(dt: Optional[datetime]) -> Optional[datetime]:
            if dt is None:
                return None
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        # 段边界优先 CMSearch
        if seg_start is not None:
            uri_s = _aware(seg_start)
        elif uri_s is not None:
            uri_s = _aware(uri_s)
        if seg_end is not None:
            uri_e = _aware(seg_end)
        elif uri_e is not None:
            uri_e = _aware(uri_e)

        if uri_e is None:
            uri_e = now
        if uri_s is None:
            uri_s = uri_e - timedelta(minutes=5)
        uri_s = _aware(uri_s)  # type: ignore[assignment]
        uri_e = _aware(uri_e)  # type: ignore[assignment]
        if uri_e <= uri_s:
            uri_e = uri_s + timedelta(seconds=max(1, int(seconds)))

        clip_s, clip_e = self._compute_clip_window(
            uri_s, uri_e, seconds, clip_start=clip_start, clip_end=clip_end
        )

        out: List[Tuple[str, str]] = []
        seen: set = set()

        def _add(label: str, url: str) -> None:
            if url and url not in seen:
                seen.add(url)
                out.append((label, url))

        if m:
            primary = mode
            alt = "utc" if primary == "local" else "local"
            for md in (primary, alt):
                rewritten = self._rewrite_rtsp_times(playback_uri, clip_s, clip_e, md)
                _add(f"short/{md}", self._inject_rtsp_auth(rewritten))
        _add("original", self._inject_rtsp_auth(playback_uri))
        return out
    def _prepare_av_save_dir(self) -> Optional[str]:
        """创建本次抽检的保存目录: <项目>/av_samples/<YYYYMMDD_HHMMSS>/"""
        if not self.av_save:
            return None
        if self.av_save_dir and os.path.isdir(self.av_save_dir):
            return self.av_save_dir
        with self._save_lock:
            if self.av_save_dir and os.path.isdir(self.av_save_dir):
                return self.av_save_dir
            stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
            # 同一秒多次创建时追加序号
            base = os.path.join(self.av_save_root, stamp)
            path = base
            n = 1
            while os.path.exists(path):
                path = f"{base}_{n}"
                n += 1
            os.makedirs(path, exist_ok=True)
            self.av_save_dir = path
            return path

    def _save_clip_file(
        self,
        tmp_path: str,
        track_id: str,
        channel: str = "",
        name: str = "",
        clip_start: Optional[datetime] = None,
    ) -> Optional[str]:
        """将临时片段复制到保存目录,返回保存路径。"""
        save_dir = self._prepare_av_save_dir()
        if not save_dir or not tmp_path or not os.path.isfile(tmp_path):
            return None
        ch = _safe_filename(str(channel or track_id), 16)
        nm = _safe_filename(name or "cam", 40)
        tid = _safe_filename(str(track_id), 16)
        # 文件名带本地抽检时刻,便于与画面 OSD 核对
        if clip_start is not None:
            ttag = clip_start.astimezone(self._get_device_tz()).strftime("%H%M%S")
        else:
            ttag = datetime.now().astimezone(self._get_device_tz()).strftime("%H%M%S")
        fname = f"ch{ch}_{nm}_track{tid}_{ttag}.mkv"
        dest = os.path.join(save_dir, fname)
        # 重名则追加序号
        if os.path.exists(dest):
            stem, ext = os.path.splitext(fname)
            i = 1
            while os.path.exists(dest):
                dest = os.path.join(save_dir, f"{stem}_{i}{ext}")
                i += 1
        try:
            shutil.copy2(tmp_path, dest)
            return dest
        except OSError:
            return None

    def _probe_track_av(
        self,
        track_id: str,
        playback_uri: Optional[str],
        seg_start: Optional[datetime],
        seg_end: Optional[datetime],
        expect_audio: Optional[bool],
        clip_start: Optional[datetime] = None,
        clip_end: Optional[datetime] = None,
        sample_label: str = "",
        channel: str = "",
        name: str = "",
        save_clip_start: Optional[datetime] = None,
    ) -> Dict:
        """短时 RTSP 拉流到临时 mkv,用 ffprobe 检查音视频轨。

        安全策略:
        - 仅 RTSP 回放,时长严格限制(默认数秒)
        - 使用 -c copy,本地不重编码、不写 NVR 盘
        - 临时文件在本机 /tmp;默认用后必删
        - 若开启 av_save,则复制到项目 av_samples/<时间戳>/
        - 不调用 ContentMgmt/download 整段下载
        - 优先使用繁忙时段(默认 10:00-18:00)定点抽检
        """
        result = {
            "视频抽检": "未知",
            "音频抽检": "未知",
            "抽检详情": "",
            "video_codec": None,
            "audio_codec": None,
            "resolution": None,
            "mean_volume_db": None,
            "抽检时段": sample_label,
            "保存路径": None,
        }
        ffmpeg = self._tools.get("ffmpeg")
        ffprobe = self._tools.get("ffprobe")
        if not ffmpeg or not ffprobe:
            result["视频抽检"] = "未知"
            result["音频抽检"] = "未知"
            result["抽检详情"] = "本机未安装 ffmpeg/ffprobe"
            return result

        if not playback_uri:
            result["视频抽检"] = "跳过"
            result["音频抽检"] = "跳过"
            result["抽检详情"] = "无回放URI"
            return result

        candidates = self._build_short_rtsp_candidates(
            playback_uri,
            seg_start,
            seg_end,
            self.av_seconds,
            clip_start=clip_start,
            clip_end=clip_end,
        )
        if not candidates:
            result["抽检详情"] = "无法构造短时RTSP地址"
            return result

        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(prefix=f"nvr_av_{track_id}_", suffix=".mkv")
            os.close(fd)
            last_err = ""
            size = 0
            # 多候选重试。短窗 seek 在部分通道会卡住，必须捕获 TimeoutExpired 继续下一候选。
            for label, rtsp in candidates:
                if os.path.exists(tmp_path):
                    try:
                        os.truncate(tmp_path, 0)
                    except OSError:
                        pass
                if label == "original":
                    timeout = self.av_seconds + 45
                    sock_us = 20_000_000
                else:
                    timeout = self.av_seconds + 20
                    sock_us = 12_000_000
                cmd = [
                    ffmpeg, "-y",
                    "-hide_banner", "-loglevel", "error",
                    "-rtsp_transport", "tcp",
                    "-timeout", str(sock_us),
                    "-i", rtsp,
                    "-t", str(self.av_seconds),
                    "-map", "0",
                    "-c", "copy",
                    "-f", "matroska",
                    tmp_path,
                ]
                try:
                    proc = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=timeout
                    )
                except subprocess.TimeoutExpired:
                    last_err = f"拉流超时({label},{timeout}s)"
                    continue
                size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
                if proc.returncode == 0 and size >= 1024:
                    break
                err_lines = (proc.stderr or "").strip().splitlines()
                last_err = err_lines[-1] if err_lines else f"rc={proc.returncode}"
            else:
                result["视频抽检"] = "异常"
                result["音频抽检"] = "异常"
                hint = ""
                low = last_err.lower()
                if "超时" in last_err or "timeout" in low:
                    hint = (
                        "；短窗 seek 可能卡住，已回退原URI仍失败。"
                        "请检查该通道回放/码流是否异常"
                    )
                elif "400" in low or "bad request" in low:
                    hint = (
                        "；多为回放时间窗无效(时区/段外)。"
                        "已尝试 local/utc 改写与原URI回退"
                    )
                elif "401" in low or "unauthor" in low:
                    hint = "；鉴权失败，请核对账号密码"
                elif "404" in low:
                    hint = "；回放资源不存在，通道可能无该时段录像"
                result["抽检详情"] = f"短时拉流失败({last_err[:100]}){hint}"
                return result
            probe_cmd = [
                ffprobe, "-v", "error",
                "-show_streams",
                "-show_format",
                "-of", "json",
                tmp_path,
            ]
            p2 = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
            if p2.returncode != 0:
                result["视频抽检"] = "异常"
                result["音频抽检"] = "未知"
                result["抽检详情"] = "ffprobe解析失败"
                return result

            data = json.loads(p2.stdout or "{}")
            streams = data.get("streams") or []
            vstreams = [s for s in streams if s.get("codec_type") == "video"]
            astreams = [s for s in streams if s.get("codec_type") == "audio"]

            if vstreams:
                vs = vstreams[0]
                w, h = vs.get("width"), vs.get("height")
                result["video_codec"] = vs.get("codec_name")
                result["resolution"] = f"{w}x{h}" if w and h else None
                if w and h and int(w) >= 160 and int(h) >= 120:
                    result["视频抽检"] = "正常"
                else:
                    result["视频抽检"] = "异常"
                    result["抽检详情"] = f"视频分辨率异常({result['resolution']})"
            else:
                result["视频抽检"] = "异常"
                result["抽检详情"] = "无视频轨"

            if astreams:
                as_ = astreams[0]
                result["audio_codec"] = as_.get("codec_name")
                result["音频抽检"] = "正常"
                # 粗测静音:仅当期望有音频时
                if expect_audio is not False:
                    vol_cmd = [
                        ffmpeg, "-hide_banner", "-nostats",
                        "-i", tmp_path,
                        "-t", str(min(self.av_seconds, 5)),
                        "-af", "volumedetect",
                        "-f", "null", "-",
                    ]
                    vp = subprocess.run(
                        vol_cmd, capture_output=True, text=True, timeout=40
                    )
                    mean_m = re.search(
                        r"mean_volume:\s*([-\d.]+)\s*dB",
                        vp.stderr or "",
                    )
                    if mean_m:
                        mean_db = float(mean_m.group(1))
                        result["mean_volume_db"] = mean_db
                        if mean_db <= self.silence_db:
                            result["音频抽检"] = "警告"
                            extra = f"疑似静音(mean {mean_db:.1f}dB)"
                            result["抽检详情"] = (
                                f"{result['抽检详情']}; {extra}" if result["抽检详情"]
                                else extra
                            )
            else:
                if expect_audio is True:
                    result["音频抽检"] = "异常"
                    extra = "配置含音频但文件无音频轨"
                    result["抽检详情"] = (
                        f"{result['抽检详情']}; {extra}" if result["抽检详情"]
                        else extra
                    )
                elif expect_audio is False:
                    result["音频抽检"] = "跳过"
                else:
                    result["音频抽检"] = "警告"
                    extra = "无音频轨"
                    result["抽检详情"] = (
                        f"{result['抽检详情']}; {extra}" if result["抽检详情"]
                        else extra
                    )

            if result["视频抽检"] == "正常" and result["音频抽检"] in ("正常", "跳过"):
                parts = []
                if result.get("resolution"):
                    parts.append(result["resolution"])
                if result.get("video_codec"):
                    parts.append(result["video_codec"])
                if result.get("audio_codec"):
                    parts.append(result["audio_codec"])
                if result.get("mean_volume_db") is not None:
                    parts.append(f"{result['mean_volume_db']:.1f}dB")
                if not result["抽检详情"]:
                    prefix = "短时抽检OK"
                    if sample_label:
                        # 只保留抽检点时间,避免详情过长
                        m = re.search(r"抽检点\s+([0-9\- :]+)", sample_label)
                        if m:
                            prefix = f"短时抽检OK@{m.group(1).strip()}"
                    result["抽检详情"] = prefix + " " + " ".join(parts)

            # 成功拉到有效片段后,可选保存到项目目录
            if self.av_save and size >= 1024:
                saved = self._save_clip_file(
                    tmp_path,
                    track_id=str(track_id),
                    channel=str(channel or ""),
                    name=str(name or ""),
                    clip_start=save_clip_start or clip_start,
                )
                if saved:
                    result["保存路径"] = saved
                else:
                    extra = "保存片段失败"
                    result["抽检详情"] = (
                        f"{result['抽检详情']}; {extra}" if result["抽检详情"]
                        else extra
                    )

            return result
        except subprocess.TimeoutExpired:
            result["视频抽检"] = "异常"
            result["音频抽检"] = "未知"
            result["抽检详情"] = "拉流超时"
            return result
        except Exception as e:
            result["视频抽检"] = "未知"
            result["音频抽检"] = "未知"
            result["抽检详情"] = f"抽检异常: {e}"
            return result
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _run_deep_av_checks(self, records: List[Dict]) -> None:
        """对通道做短时音视频抽检(低并发,默认仅落盘正常的通道)。

        抽检时间优先落在繁忙时段(默认本地 10:00-18:00),人流较多便于验证音视频。
        """
        if not self.deep_av_check:
            for r in records:
                r.setdefault("视频抽检", "跳过")
                r.setdefault("音频抽检", "跳过")
                r.setdefault("抽检详情", "未启用深度抽检")
            return

        # 已配置录像且落盘正常即可作为候选(繁忙时段会单独检索 URI)
        candidates = [
            r for r in records
            if r.get("已启用录像") and r.get("落盘状态") == "正常"
        ]
        for r in records:
            if r not in candidates:
                if not r.get("已启用录像"):
                    r["视频抽检"] = "跳过"
                    r["音频抽检"] = "跳过"
                    r["抽检详情"] = "未配置录像"
                elif r.get("落盘状态") != "正常":
                    r["视频抽检"] = "跳过"
                    r["音频抽检"] = "跳过"
                    r["抽检详情"] = "近期无录像/未知,跳过拉流"
                else:
                    r["视频抽检"] = "跳过"
                    r["音频抽检"] = "跳过"
                    r["抽检详情"] = "跳过"

        if self.av_limit is not None:
            candidates = candidates[: self.av_limit]

        if not candidates:
            self._log("深度抽检: 无可用通道(需近期有录像)")
            return

        clip_s, clip_e, sample_label = self._pick_busy_clip_times(self.av_seconds)
        save_dir = self._prepare_av_save_dir() if self.av_save else None
        self._log(
            f"深度音视频抽检: {len(candidates)} 通道 × {self.av_seconds}s RTSP"
            f", 并发 {self.av_workers} (仅短时回放,不写NVR盘)"
        )
        self._log(f"优先时段: {sample_label}")
        if save_dir:
            self._log(f"片段保存目录: {save_dir}")

        def _job(rec: Dict) -> Tuple[str, Dict]:
            tid = str(rec["track_id"])
            # 在繁忙时段窗口检索回放URI
            found = self._search_track_in_range(tid, clip_s, clip_e)
            uri = found.get("playback_uri") or rec.get("playback_uri")
            seg_s = found.get("seg_start") if found.get("ok") else rec.get("seg_start")
            seg_e = found.get("seg_end") if found.get("ok") else rec.get("seg_end")
            use_clip = (clip_s, clip_e) if found.get("ok") else (None, None)
            label = sample_label if found.get("ok") else (
                sample_label + "; 繁忙时段未命中,回退近期片段"
            )
            if not uri:
                return tid, {
                    "视频抽检": "跳过",
                    "音频抽检": "跳过",
                    "抽检详情": found.get("detail") or "无回放URI",
                    "抽检时段": label,
                    "保存路径": None,
                }
            res = self._probe_track_av(
                track_id=tid,
                playback_uri=uri,
                seg_start=seg_s,
                seg_end=seg_e,
                expect_audio=rec.get("录像含音频"),
                clip_start=use_clip[0],
                clip_end=use_clip[1],
                sample_label=label,
                channel=str(rec.get("通道") or ""),
                name=str(rec.get("名称") or ""),
                save_clip_start=use_clip[0] or clip_s,
            )
            return tid, res

        results: Dict[str, Dict] = {}
        total_av = len(candidates)
        done_av = 0
        # 深度抽检约占总进度 70%→96%
        deep_lo, deep_hi = 0.70, 0.96
        with ThreadPoolExecutor(max_workers=self.av_workers) as pool:
            futs = [pool.submit(_job, r) for r in candidates]
            for fut in as_completed(futs):
                tid, res = fut.result()
                results[tid] = res
                done_av += 1
                frac = done_av / total_av if total_av else 1.0
                overall = deep_lo + (deep_hi - deep_lo) * frac
                # 节流：每完成 1 路或每 5% 汇报，保证进度条跟手
                if (
                    done_av == 1
                    or done_av == total_av
                    or done_av % max(1, total_av // 20) == 0
                ):
                    self._progress(
                        f"深度抽检 {done_av}/{total_av}",
                        current=done_av,
                        total=total_av,
                        phase="deep",
                        overall=overall,
                    )

        saved_n = 0
        for r in candidates:
            res = results.get(str(r["track_id"]), {})
            r["视频抽检"] = res.get("视频抽检", "未知")
            r["音频抽检"] = res.get("音频抽检", "未知")
            r["抽检详情"] = res.get("抽检详情", "")
            r["抽检时段"] = res.get("抽检时段", sample_label)
            r["video_codec"] = res.get("video_codec")
            r["audio_codec"] = res.get("audio_codec")
            r["resolution"] = res.get("resolution")
            r["mean_volume_db"] = res.get("mean_volume_db")
            r["保存路径"] = res.get("保存路径")
            if r.get("保存路径"):
                saved_n += 1

        if self.av_save:
            if save_dir and saved_n:
                self._log(f"已保存 {saved_n} 个抽检片段 → {save_dir}")
            elif save_dir:
                self._log(f"已创建目录但未保存到文件: {save_dir}")

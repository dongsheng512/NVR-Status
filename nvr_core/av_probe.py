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
    def _build_short_rtsp(
        self,
        playback_uri: str,
        seg_start: Optional[datetime],
        seg_end: Optional[datetime],
        seconds: int,
        clip_start: Optional[datetime] = None,
        clip_end: Optional[datetime] = None,
    ) -> Optional[str]:
        """从完整 playbackURI 截取短窗口,并注入鉴权。绝不整段下载。

        若传入 clip_start/clip_end(UTC),优先使用(用于繁忙时段定点抽检);
        否则回退为片段末尾附近短窗。
        """
        if not playback_uri or not playback_uri.startswith("rtsp://"):
            return None
        now = datetime.now(timezone.utc)
        m = re.search(
            r"starttime=(\d{8}T\d{6}Z).*endtime=(\d{8}T\d{6}Z)",
            playback_uri,
            re.I,
        )
        if m:
            try:
                uri_s = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                uri_e = datetime.strptime(m.group(2), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                uri_s, uri_e = seg_start, seg_end
        else:
            uri_s, uri_e = seg_start, seg_end

        if uri_e is None:
            uri_e = now
        if uri_s is None:
            uri_s = uri_e - timedelta(minutes=5)

        if clip_start is not None and clip_end is not None:
            clip_s = max(uri_s, clip_start)
            clip_e = min(uri_e, clip_end)
            if clip_e <= clip_s:
                # 指定点不在该段内,夹到段内可用区域
                clip_e = min(uri_e, uri_s + timedelta(seconds=seconds))
                clip_s = uri_s
            # 保证至少接近请求时长
            if (clip_e - clip_s).total_seconds() < max(2, seconds // 2):
                clip_e = min(uri_e, clip_s + timedelta(seconds=seconds))
        else:
            # 取片段末尾附近短窗,避开正在写入的最前沿几秒
            clip_e = min(now - timedelta(seconds=3), uri_e - timedelta(seconds=2))
            if clip_e <= uri_s:
                clip_e = uri_e
            clip_s = clip_e - timedelta(seconds=seconds)
            if clip_s < uri_s:
                clip_s = uri_s
            if clip_e <= clip_s:
                clip_e = min(uri_e, clip_s + timedelta(seconds=seconds))

        short = playback_uri
        if m:
            # 必须用设备本地墙钟写入,不能用 UTC(否则 OSD 会显示成早上 5 点多)
            short = re.sub(
                r"starttime=\d{8}T\d{6}Z",
                f"starttime={self._fmt_rtsp_time(clip_s)}",
                short,
                flags=re.I,
            )
            short = re.sub(
                r"endtime=\d{8}T\d{6}Z",
                f"endtime={self._fmt_rtsp_time(clip_e)}",
                short,
                flags=re.I,
            )
        # 去掉 size,避免设备按完整段长度处理
        short = re.sub(r"[&?]size=\d+", "", short)
        short = short.replace("?&", "?").rstrip("?&")

        # rtsp://host/path -> rtsp://user:pass@host/path (密码 URL 编码)
        host_path = short[len("rtsp://"):]
        user = quote(self.username, safe="")
        pwd = quote(self.password, safe="")
        return f"rtsp://{user}:{pwd}@{host_path}"

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

        rtsp = self._build_short_rtsp(
            playback_uri,
            seg_start,
            seg_end,
            self.av_seconds,
            clip_start=clip_start,
            clip_end=clip_end,
        )
        if not rtsp:
            result["抽检详情"] = "无法构造短时RTSP地址"
            return result

        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(prefix=f"nvr_av_{track_id}_", suffix=".mkv")
            os.close(fd)
            # 低干扰: TCP RTSP、严格短时长、stream copy(本地不重编码)
            cmd = [
                ffmpeg, "-y",
                "-hide_banner", "-loglevel", "error",
                "-rtsp_transport", "tcp",
                "-i", rtsp,
                "-t", str(self.av_seconds),
                "-map", "0",
                "-c", "copy",
                "-f", "matroska",
                tmp_path,
            ]
            timeout = self.av_seconds + 45
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
            if proc.returncode != 0 or size < 1024:
                err = (proc.stderr or "").strip().splitlines()
                err_s = err[-1] if err else f"rc={proc.returncode}"
                result["视频抽检"] = "异常"
                result["音频抽检"] = "异常"
                result["抽检详情"] = f"短时拉流失败({err_s[:120]})"
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

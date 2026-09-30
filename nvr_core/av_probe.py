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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from nvr_core.util import (
    ScanCancelled,
    _safe_filename,
    _to_int,
    latest_av_sample_instant,
)

_NO_MAPPED_STREAM = (
    "matches no streams",
    "does not contain any stream",
    "output file does not contain any stream",
)


def _stderr_no_mapped_stream(text: str) -> bool:
    t = (text or "").lower()
    return any(m in t for m in _NO_MAPPED_STREAM)


def _audio_verdict(
    status: str,
    expect_audio: Optional[bool],
    mean_db: Optional[float],
    silence_db: float,
) -> Tuple[str, str]:
    """独立音频抽检结论 → (音频抽检, 备注)。

    status: ok / no_stream / fail
    """
    if status == "ok":
        if mean_db is not None and mean_db <= silence_db:
            return "警告", f"疑似静音(mean {mean_db:.1f}dB)"
        return "正常", ""
    if status == "no_stream":
        if expect_audio is False:
            return "跳过", "配置未开音频"
        return "异常", "无音轨"
    return "未知", "音频未确认(拉流超时/失败)"


_AV_SEV = {"正常": 0, "警告": 1, "跳过": 2, "未知": 3, "异常": 4}


def _av_severity(video: Optional[str], audio: Optional[str]) -> int:
    return max(
        _AV_SEV.get(video or "未知", 3),
        _AV_SEV.get(audio or "未知", 3),
    )


def av_needs_retry(res: Dict) -> bool:
    """初检失败/未确认/该时段无回放 → 换时段再抽。"""
    v = res.get("视频抽检")
    a = res.get("音频抽检")
    if v in ("异常", "未知") or a in ("异常", "未知"):
        return True
    if v == "跳过":
        detail = str(res.get("抽检详情") or "")
        if any(k in detail for k in ("无回放", "无录像", "检索失败")):
            return True
    return False


def _at_window_rank(video: Optional[str], audio: Optional[str]) -> Tuple[int, int]:
    """定点抽检多时刻合并的窗口排名(越小越好)。

    第一键:是否给出明确结论(正常/警告/异常 任一)。纯「未知/跳过」窗口属于
    **缺证据**,不能排在已确认异常的窗口前面 —— 缺证据 ≠ 恢复;
    第二键:严重度(恢复时刻的正常窗胜过异常窗,明细里保留轨迹)。
    """
    definite = 0 if (video in ("正常", "警告", "异常") or audio in ("正常", "警告", "异常")) else 1
    return (definite, _av_severity(video, audio))


def av_candidate_order(label: str) -> Tuple[int, int, str]:
    """候选排序键：优先原 URI，其次是改写短窗，`:554` 回退一律排最后。

    `@554` 变体只是「原端口整条链路都不通」时的兜底，正常情况下不该插队
    多试一遍（每次多试都是一次最长 ~30s 的超时等待）。
    """
    return (
        1 if label.endswith("@554") else 0,
        0 if label.startswith("original") else 1,
        label,
    )


def _apply_av_result(rec: Dict, res: Dict, sample_label: str = "") -> None:
    rec["视频抽检"] = res.get("视频抽检", "未知")
    rec["音频抽检"] = res.get("音频抽检", "未知")
    rec["抽检详情"] = res.get("抽检详情", "")
    rec["抽检时段"] = res.get("抽检时段") or sample_label
    rec["video_codec"] = res.get("video_codec")
    rec["audio_codec"] = res.get("audio_codec")
    rec["resolution"] = res.get("resolution")
    rec["mean_volume_db"] = res.get("mean_volume_db")
    rec["保存路径"] = res.get("保存路径")


class AVProbeMixin:
    # RTSP 时间串: 海康 playbackURI 里 Z 后缀在不同固件上既可能表示
    # 真 UTC, 也可能表示设备本地墙钟(数字是本地时,后缀仍写 Z)。
    # 写侧默认用本地墙钟(_fmt_rtsp_time); 读侧需与 CMSearch 段时间对齐判定。

    def _mask_credentials(self, text: str) -> str:
        """掩去错误信息里的 rtsp user:pass(避免进入报告/历史文件)。"""
        if not text:
            return text
        # 明文形态 rtsp://user:pass@host 与 URL 编码形态 user%3Apwd@
        masked = re.sub(r"(://)([^@/\s:]+):([^@/\s]+)@", r"\1***:***@", text)
        masked = re.sub(
            r"(://)([^@/\s%]+)%3A([^@/\s]+)@", r"\1***:***@", masked, flags=re.I
        )
        return masked

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

    def _swap_rtsp_port(self, uri: str, port: int) -> Optional[str]:
        """把 rtsp URL 的端口换成 port；无显式端口或已是该端口时返回 None。

        用途：CMSearch 返回的 playbackURI 用的是设备的 HTTP 端口（常见 :80），
        但有些环境下该端口的 RTSP 会被静默拒绝（TCP 连得上、一发 DESCRIBE 就断开），
        而标准 RTSP 端口 :554 正常 —— 此时需要一个同路径、只换端口的回退候选。
        """
        if not uri or not uri.startswith("rtsp://"):
            return None
        rest = uri[len("rtsp://") :]
        slash = rest.find("/")
        authority = rest if slash < 0 else rest[:slash]
        path = "" if slash < 0 else rest[slash:]
        hostport = authority.rsplit("@", 1)[-1]  # 去掉 userinfo
        if hostport.startswith("["):  # IPv6 字面量 [::1]:80
            end = hostport.find("]")
            if end < 0:
                return None
            host = hostport[: end + 1]
            tail = hostport[end + 1 :]
            cur = tail[1:] if tail.startswith(":") else ""
        else:
            host, sep, cur = hostport.partition(":")
            if not sep:
                cur = ""
        if not cur.isdigit() or int(cur) == port:
            return None
        userinfo = authority[: len(authority) - len(hostport)]
        return f"rtsp://{userinfo}{host}:{port}{path}"

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
        """在录像段 [uri_s, uri_e] 内切出短抽检窗（UTC）。

        结束时刻不超过「现在 - 10 分钟」，避免抽正在写入的回放导致超时。
        """
        now = datetime.now(timezone.utc)
        cutoff = latest_av_sample_instant(now)
        sec = max(1, int(seconds))

        def _aw(dt: datetime) -> datetime:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        uri_s, uri_e = _aw(uri_s), _aw(uri_e)
        hard_e = min(uri_e, cutoff)

        if clip_start is not None and clip_end is not None:
            cs = _aw(clip_start)
            ce = _aw(clip_end)
            clip_s = max(uri_s, cs)
            clip_e = min(hard_e, ce)
            if clip_e <= clip_s:
                if ce > cutoff or cs >= cutoff:
                    clip_e = hard_e
                    clip_s = max(uri_s, clip_e - timedelta(seconds=sec))
                else:
                    clip_s = uri_s
                    clip_e = min(hard_e, uri_s + timedelta(seconds=sec))
            if (clip_e - clip_s).total_seconds() < max(1, sec // 2):
                clip_e = min(hard_e, clip_s + timedelta(seconds=sec))
        else:
            clip_e = min(hard_e, uri_e - timedelta(seconds=2))
            if clip_e <= uri_s:
                clip_e = hard_e
            clip_s = clip_e - timedelta(seconds=sec)
            if clip_s < uri_s:
                clip_s = uri_s
            if clip_e <= clip_s:
                clip_e = min(hard_e, clip_s + timedelta(seconds=sec))

        if clip_e <= clip_s:
            clip_s = uri_s
            clip_e = min(hard_e, uri_s + timedelta(seconds=sec))
        if clip_e <= clip_s:
            clip_e = clip_s + timedelta(seconds=sec)
        if clip_e > cutoff:
            clip_e = cutoff
            clip_s = min(clip_s, clip_e - timedelta(seconds=sec))
            if clip_s < uri_s:
                clip_s = uri_s
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

        def _add_with_port_alt(label: str, url: str) -> None:
            """同一候选再补一个 :554 变体（原 URI 端口存在且非 554 时）。

            排在原候选**之后**（见 _probe_track_av 的排序），所以正常情况下
            不会多试一次；只有原端口整条链路都失败时才轮到它。
            """
            _add(label, url)
            alt = self._swap_rtsp_port(url, 554)
            if alt:
                _add(label + "@554", alt)

        if m:
            primary = mode
            alt = "utc" if primary == "local" else "local"
            for md in (primary, alt):
                rewritten = self._rewrite_rtsp_times(playback_uri, clip_s, clip_e, md)
                _add_with_port_alt(f"short/{md}", self._inject_rtsp_auth(rewritten))
        _add_with_port_alt("original", self._inject_rtsp_auth(playback_uri))
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

    def _run_cancellable(
        self, cmd: List[str], timeout: float
    ) -> subprocess.CompletedProcess:
        """subprocess.run 的可取消版: 巡检取消时立即 kill 子进程。

        用于 ffmpeg 拉流(单路最长 av_seconds+30s),否则点「取消」后
        仍要等正在拉流的各路跑完才真正停止。
        """
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    out, err = proc.communicate(timeout=0.25)
                    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
                except subprocess.TimeoutExpired:
                    pass
                if self._cancelled.is_set():
                    proc.kill()
                    proc.communicate()
                    raise ScanCancelled("巡检已取消")
                if time.monotonic() >= deadline:
                    proc.kill()
                    proc.communicate()
                    raise subprocess.TimeoutExpired(cmd, timeout)
        finally:
            if proc.poll() is None:
                proc.kill()
                try:
                    proc.communicate(timeout=2)
                except Exception:
                    pass

    def _pull_rtsp_map(
        self,
        ffmpeg: str,
        candidates: List[Tuple[str, str]],
        tmp_path: str,
        stream_map: str,
        seconds: int,
        extra_args: Optional[List[str]] = None,
        min_ok: int = 8 * 1024,
        partial_ok: int = 24 * 1024,
        timeout_original: Optional[float] = None,
        timeout_other: Optional[float] = None,
        sock_us_original: int = 12_000_000,
        sock_us_other: int = 10_000_000,
        stop_on_no_stream: bool = False,
    ) -> Tuple[str, int, str, Optional[Tuple[str, str]]]:
        """按 -map 拉短时 RTSP 到临时文件。

        返回 (status, size, last_err, used_candidate)；
        status 为 ok / no_stream / fail。
        """
        t_orig = (
            self.av_seconds + 25 if timeout_original is None else timeout_original
        )
        t_other = (
            self.av_seconds + 30 if timeout_other is None else timeout_other
        )
        extra = list(extra_args or [])
        last_err = ""
        size = 0
        saw_no_stream = False
        for label, rtsp in candidates:
            self._check_cancel()
            if os.path.exists(tmp_path):
                try:
                    os.truncate(tmp_path, 0)
                except OSError:
                    pass
            if label.startswith("original"):
                timeout = t_orig
                sock_us = sock_us_original
            else:
                timeout = t_other
                sock_us = sock_us_other
            cmd = [
                ffmpeg, "-y",
                "-hide_banner", "-loglevel", "error",
                "-rtsp_transport", "tcp",
                "-timeout", str(sock_us),
                "-i", rtsp,
                "-t", str(seconds),
                *extra,
                "-map", stream_map,
                "-c", "copy",
                "-f", "matroska",
                tmp_path,
            ]
            try:
                proc = self._run_cancellable(cmd, timeout=timeout)
                size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
                err = proc.stderr or ""
                if _stderr_no_mapped_stream(err):
                    saw_no_stream = True
                    last_err = (err.strip().splitlines() or ["no mapped stream"])[-1]
                    if stop_on_no_stream:
                        return "no_stream", size, last_err, None
                    continue
                if size >= min_ok and (
                    proc.returncode == 0 or size >= partial_ok
                ):
                    return "ok", size, "", (label, rtsp)
                err_lines = err.strip().splitlines()
                last_err = err_lines[-1] if err_lines else f"rc={proc.returncode}"
            except subprocess.TimeoutExpired:
                size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
                if size >= partial_ok:
                    return "ok", size, "", (label, rtsp)
                last_err = f"拉流超时({label},{timeout}s)"
                continue
        if saw_no_stream and size < min_ok:
            return "no_stream", size, last_err, None
        return "fail", size, last_err, None

    def _probe_audio_track(
        self,
        ffmpeg: str,
        ffprobe: str,
        candidates: List[Tuple[str, str]],
        expect_audio: Optional[bool],
    ) -> Dict:
        """视频成功后再单独拉音频轨，确认是否有声（避免全流 demux 卡死）。"""
        audio_seconds = max(3, min(int(self.av_seconds), 4))
        tmp_path = None
        codec = None
        mean_db = None
        try:
            fd, tmp_path = tempfile.mkstemp(prefix="nvr_a_", suffix=".mkv")
            os.close(fd)
            status, _size, last_err, _used = self._pull_rtsp_map(
                ffmpeg,
                candidates,
                tmp_path,
                "0:a:0",
                audio_seconds,
                extra_args=["-vn"],
                min_ok=512,
                partial_ok=1024,
                timeout_original=audio_seconds + 10,
                timeout_other=audio_seconds + 8,
                sock_us_original=8_000_000,
                sock_us_other=6_000_000,
                stop_on_no_stream=True,
            )
            if status == "ok":
                probe_cmd = [
                    ffprobe, "-v", "error",
                    "-show_streams",
                    "-of", "json",
                    tmp_path,
                ]
                p2 = subprocess.run(
                    probe_cmd, capture_output=True, text=True, timeout=20
                )
                if p2.returncode != 0:
                    status = "fail"
                    last_err = last_err or "ffprobe解析音频失败"
                else:
                    data = json.loads(p2.stdout or "{}")
                    astreams = [
                        s for s in (data.get("streams") or [])
                        if s.get("codec_type") == "audio"
                    ]
                    if not astreams:
                        status = "no_stream"
                    else:
                        codec = astreams[0].get("codec_name")
                        vol_cmd = [
                            ffmpeg, "-hide_banner", "-nostats",
                            "-i", tmp_path,
                            "-t", str(audio_seconds),
                            "-af", "volumedetect",
                            "-f", "null", "-",
                        ]
                        vp = subprocess.run(
                            vol_cmd, capture_output=True, text=True, timeout=30
                        )
                        mean_m = re.search(
                            r"mean_volume:\s*([-\d.]+)\s*dB",
                            vp.stderr or "",
                        )
                        if mean_m:
                            mean_db = float(mean_m.group(1))
            verdict, note = _audio_verdict(
                status, expect_audio, mean_db, self.silence_db
            )
            if verdict == "未知" and last_err:
                note = f"{note}: {self._mask_credentials(last_err)[:80]}"
            return {
                "音频抽检": verdict,
                "audio_codec": codec,
                "mean_volume_db": mean_db,
                "note": note,
            }
        except ScanCancelled:
            raise
        except Exception as e:
            return {
                "音频抽检": "未知",
                "audio_codec": None,
                "mean_volume_db": None,
                "note": f"音频抽检异常: {self._mask_credentials(str(e))[:80]}",
            }
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

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

        # 优先原 URI：球机短窗 seek 慢且易超时；原段 + 仅视频轨通常数秒成功。
        # @554 变体排在最后 —— 它是「原端口整条链路都不通」时的回退，
        # 正常情况下不应让它插队多试一遍。
        ordered = sorted(candidates, key=lambda x: av_candidate_order(x[0]))

        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(prefix=f"nvr_av_{track_id}_", suffix=".mkv")
            os.close(fd)
            last_err = ""
            size = 0
            used_video: Optional[Tuple[str, str]] = None
            # 仅映射视频轨：部分球机全流 demux（含 AAC 等音轨）会挂死，
            # 真机前端相机-1/前端相机-2：-map 0 超时，-map 0:v:0 约 5s 成功。
            # 音频改独立短拉 -map 0:a:0，不与视频捆在一次全流里。
            min_ok = 8 * 1024
            partial_ok = 24 * 1024
            for label, rtsp in ordered:
                self._check_cancel()
                if os.path.exists(tmp_path):
                    try:
                        os.truncate(tmp_path, 0)
                    except OSError:
                        pass
                if label.startswith("original"):
                    timeout = self.av_seconds + 25
                    sock_us = 12_000_000
                else:
                    timeout = self.av_seconds + 30
                    sock_us = 10_000_000
                cmd = [
                    ffmpeg, "-y",
                    "-hide_banner", "-loglevel", "error",
                    "-rtsp_transport", "tcp",
                    "-timeout", str(sock_us),
                    "-i", rtsp,
                    "-t", str(self.av_seconds),
                    "-map", "0:v:0",
                    "-c", "copy",
                    "-f", "matroska",
                    tmp_path,
                ]
                try:
                    proc = self._run_cancellable(cmd, timeout=timeout)
                    size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
                    if size >= min_ok and (
                        proc.returncode == 0 or size >= partial_ok
                    ):
                        used_video = (label, rtsp)
                        break
                    err_lines = (proc.stderr or "").strip().splitlines()
                    last_err = err_lines[-1] if err_lines else f"rc={proc.returncode}"
                except subprocess.TimeoutExpired:
                    size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
                    if size >= partial_ok:
                        used_video = (label, rtsp)
                        break
                    last_err = f"拉流超时({label},{timeout}s)"
                    continue
            else:
                result["视频抽检"] = "异常"
                result["音频抽检"] = "跳过"
                hint = ""
                low = last_err.lower()
                if "超时" in last_err or "timeout" in low:
                    hint = (
                        "；回放视频轨超时(已试原URI/local/utc)。"
                        "请检查该通道回放服务"
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
                result["抽检详情"] = f"短时拉流失败({self._mask_credentials(last_err)[:100]}){hint}"
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
                result["音频抽检"] = "跳过"
                result["抽检详情"] = "ffprobe解析失败"
                return result

            data = json.loads(p2.stdout or "{}")
            streams = data.get("streams") or []
            vstreams = [s for s in streams if s.get("codec_type") == "video"]

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

            audio_note = ""
            if result["视频抽检"] == "正常":
                audio_cands: List[Tuple[str, str]] = []
                if used_video:
                    audio_cands.append(used_video)
                for item in ordered:
                    if not used_video or item[1] != used_video[1]:
                        audio_cands.append(item)
                ares = self._probe_audio_track(
                    ffmpeg, ffprobe, audio_cands, expect_audio
                )
                result["音频抽检"] = ares.get("音频抽检", "未知")
                result["audio_codec"] = ares.get("audio_codec")
                result["mean_volume_db"] = ares.get("mean_volume_db")
                audio_note = ares.get("note") or ""
            else:
                result["音频抽检"] = "跳过"

            if result["视频抽检"] == "正常":
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
                        m = re.search(r"抽检点\s+([0-9\- :]+)", sample_label)
                        if m:
                            prefix = f"短时抽检OK@{m.group(1).strip()}"
                    result["抽检详情"] = (prefix + " " + " ".join(parts)).strip()
                if audio_note:
                    result["抽检详情"] = (
                        f"{result['抽检详情']}; {audio_note}"
                        if result["抽检详情"]
                        else audio_note
                    )

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
        except ScanCancelled:
            # 取消必须向上传播,不能被下面的兜底 except 吞成结果
            raise
        except subprocess.TimeoutExpired:
            result["视频抽检"] = "异常"
            result["音频抽检"] = "未知"
            result["抽检详情"] = "拉流超时"
            return result
        except Exception as e:
            result["视频抽检"] = "未知"
            result["音频抽检"] = "未知"
            result["抽检详情"] = f"抽检异常: {self._mask_credentials(str(e))}"
            return result
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _probe_channel_at(
        self,
        rec: Dict,
        clip_s: datetime,
        clip_e: datetime,
        sample_label: str,
    ) -> Tuple[str, Dict]:
        """在指定时段检索回放并做短时抽检。"""
        self._check_cancel()
        tid = str(rec["track_id"])
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

    def _run_av_jobs(
        self,
        recs: List[Dict],
        clip_s: datetime,
        clip_e: datetime,
        sample_label: str,
        progress_lo: float,
        progress_hi: float,
        progress_text: str,
    ) -> Dict[int, Dict]:
        results: Dict[int, Dict] = {}
        total_av = len(recs)
        if total_av == 0:
            return results
        done_av = 0
        pool = ThreadPoolExecutor(max_workers=self.av_workers)
        try:
            # 结果按 id(rec) 建键:track_id 可能重复或为「未知」(Track 缺 id/Channel),
            # 按 track_id 建键会让重复项互相覆盖、结论挂到别的通道上。
            futs = {
                pool.submit(self._probe_channel_at, r, clip_s, clip_e, sample_label): r
                for r in recs
            }
            for fut in as_completed(futs):
                _tid, res = fut.result()
                results[id(futs[fut])] = res
                done_av += 1
                frac = done_av / total_av
                overall = progress_lo + (progress_hi - progress_lo) * frac
                if (
                    done_av == 1
                    or done_av == total_av
                    or done_av % max(1, total_av // 20) == 0
                ):
                    self._progress(
                        f"{progress_text} {done_av}/{total_av}",
                        current=done_av,
                        total=total_av,
                        phase="deep",
                        overall=overall,
                    )
        except ScanCancelled:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        pool.shutdown(wait=True)
        return results

    def _run_deep_av_checks(self, records: List[Dict]) -> None:
        """对通道做短时音视频抽检(低并发,默认仅落盘正常的通道)。

        抽检时间优先落在繁忙时段(默认本地 10:00-18:00),人流较多便于验证音视频。
        初检异常的通道会换一个时段再抽一次。
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

        if self.av_channels is not None:
            # 过滤发生在非候选标记之后、av_limit 之前：
            #   - 在「未配置录像/落盘异常」之后 —— 那些是更值得用户看的真实问题，
            #     不该被「不在抽检列表」掩盖；
            #   - 在 av_limit 之前 —— `--av-channels 31,64 --av-limit 2` 的语义
            #     是「在 31/64 里最多抽 2 路」，而不是先按上限截断再过滤。
            kept, outside = [], []
            for r in candidates:
                if _to_int(str(r.get("通道") or ""), default=0) in self.av_channels:
                    kept.append(r)
                else:
                    outside.append(r)
            candidates = kept
            for r in outside:
                r["视频抽检"] = "跳过"
                r["音频抽检"] = "跳过"
                r["抽检详情"] = "不在指定抽检通道列表"

        if self.av_limit is not None:
            overflow = candidates[self.av_limit:]
            candidates = candidates[: self.av_limit]
            for r in overflow:
                r["视频抽检"] = "跳过"
                r["音频抽检"] = "跳过"
                r["抽检详情"] = f"超出抽检路数上限({self.av_limit})"

        if not candidates:
            if self.av_channels is not None:
                self._log(
                    "深度抽检: 指定通道"
                    f"(通道 {'、'.join(str(c) for c in sorted(self.av_channels))})"
                    "中没有近期落盘正常的通道"
                )
            else:
                self._log("深度抽检: 无可用通道(需近期有录像)")
            return

        save_dir = self._prepare_av_save_dir() if self.av_save else None
        self._log(
            f"深度音视频抽检: {len(candidates)} 通道 × {self.av_seconds}s 视频"
            f" + 独立音频轨, 并发 {self.av_workers} (仅短时回放,不写NVR盘)"
        )
        if self.av_channels is not None:
            self._log(
                "抽检通道过滤: 仅 "
                + "、".join(str(c) for c in sorted(self.av_channels))
            )
        if save_dir:
            self._log(f"片段保存目录: {save_dir}")

        # 预热设备时区:抽检线程池内 _search_track_in_range 会用到,
        # 先在主线程解析好,避免多线程同时触发 /System/status 探测
        self._get_device_tz()

        if getattr(self, "av_at", None):
            # 定点模式(--av-at): 每个时刻各查一遍全部候选通道,
            # 用于区分「持久故障」与「间歇抖动」(多时刻都未知 → 先怀疑 IPC 端)。
            # 最终结论: 有明确结论的窗口优先、再取严重度最低的一窗;
            # 「多时刻」轨迹写进抽检详情,不静默。
            instants = list(self.av_at)
            n = len(instants)
            span = (0.90 - 0.70) / n
            # 同样按 id(rec) 建键,理由见 _run_av_jobs
            merged: Dict[int, Dict] = {}
            best_label: Dict[int, str] = {}
            notes: Dict[int, List[str]] = {}
            for idx, at_dt in enumerate(instants):
                clip_s, clip_e, sample_label = self._pick_busy_clip_times(
                    self.av_seconds, at=at_dt
                )
                self._log(f"定点抽检 [{idx + 1}/{n}] {sample_label}")
                res = self._run_av_jobs(
                    candidates, clip_s, clip_e, sample_label,
                    0.70 + span * idx, 0.70 + span * (idx + 1),
                    "深度抽检" if n == 1 else f"定点抽检 {idx + 1}/{n}",
                )
                for r in candidates:
                    key = id(r)
                    new = res.get(key)
                    if not new:
                        continue
                    notes.setdefault(key, []).append(
                        f"{clip_s.astimezone().strftime('%H:%M')}"
                        f"={new.get('视频抽检', '未知')}/{new.get('音频抽检', '未知')}"
                    )
                    cur = merged.get(key)
                    if cur is None or _at_window_rank(
                        new.get("视频抽检"), new.get("音频抽检")
                    ) < _at_window_rank(
                        cur.get("视频抽检"), cur.get("音频抽检")
                    ):
                        merged[key] = new
                        best_label[key] = sample_label
            for r in candidates:
                key = id(r)
                _apply_av_result(r, merged.get(key, {}), best_label.get(key, ""))
                ns = notes.get(key) or []
                if len(ns) > 1:
                    extra = "多时刻: " + "; ".join(ns)
                    r["抽检详情"] = (
                        f"{extra}; {r['抽检详情']}" if r.get("抽检详情") else extra
                    )
        else:
            clip_s, clip_e, sample_label = self._pick_busy_clip_times(self.av_seconds)
            self._log(f"优先时段: {sample_label}")
            results = self._run_av_jobs(
                candidates, clip_s, clip_e, sample_label,
                0.70, 0.90, "深度抽检",
            )
            for r in candidates:
                _apply_av_result(r, results.get(id(r), {}), sample_label)

        retry_recs = [r for r in candidates if av_needs_retry(r)]
        retry_ok = 0
        if retry_recs and getattr(self, "av_at", None):
            # 定点模式不自动换时段:再随机挑一段反而偏离「查指定时刻」的本意;
            # 补测手段就是多给几个 --av-at 时刻。
            self._log(
                f"定点模式: {len(retry_recs)} 路仍异常/未确认,"
                "不自动换时段复检(需补测可增加 --av-at 时刻)"
            )
            self._progress("深度抽检完成", phase="deep", overall=0.96)
        elif retry_recs:
            retry = self._pick_retry_clip_times(self.av_seconds, clip_s, clip_e)
            if retry is None:
                self._log(
                    f"初检异常 {len(retry_recs)} 路, 繁忙窗内无合适换时段,跳过复检"
                )
                self._progress("深度抽检完成", phase="deep", overall=0.96)
            else:
                r_s, r_e, r_label = retry
                names = "、".join(
                    str(r.get("通道") or r.get("track_id"))
                    + (
                        f"({r['名称']})"
                        if r.get("名称") and r.get("名称") != "未知"
                        else ""
                    )
                    for r in retry_recs[:8]
                )
                more = f" 等{len(retry_recs)}路" if len(retry_recs) > 8 else ""
                self._log(
                    f"初检异常 {len(retry_recs)} 路, 换时段复检: {names}{more}"
                )
                self._log(f"复检时段: {r_label}")
                retry_map = self._run_av_jobs(
                    retry_recs, r_s, r_e, r_label,
                    0.90, 0.96, "异常复检",
                )
                for r in retry_recs:
                    new = retry_map.get(id(r))
                    if not new:
                        continue
                    old_v, old_a = r.get("视频抽检"), r.get("音频抽检")
                    old_detail = r.get("抽检详情") or ""
                    if _av_severity(
                        new.get("视频抽检"), new.get("音频抽检")
                    ) < _av_severity(old_v, old_a):
                        _apply_av_result(r, new, r_label)
                        extra = "复检通过,初检失败"
                        r["抽检详情"] = (
                            f"{r.get('抽检详情')}; {extra}"
                            if r.get("抽检详情")
                            else extra
                        )
                        retry_ok += 1
                    else:
                        stamp = r_s.astimezone().strftime("%H:%M:%S")
                        note = new.get("抽检详情") or "仍异常"
                        r["抽检详情"] = (
                            f"{old_detail}; 复检@{stamp}仍异常({note})"
                            if old_detail
                            else f"复检@{stamp}仍异常({note})"
                        )
                still = len(retry_recs) - retry_ok
                self._log(f"复检通过 {retry_ok} 路, 仍异常 {still} 路")
        else:
            self._progress("深度抽检完成", phase="deep", overall=0.96)

        saved_n = sum(1 for r in candidates if r.get("保存路径"))
        if self.av_save:
            if save_dir and saved_n:
                self._log(f"已保存 {saved_n} 个抽检片段 → {save_dir}")
            elif save_dir:
                self._log(f"已创建目录但未保存到文件: {save_dir}")

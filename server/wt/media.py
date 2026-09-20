# -*- coding: utf-8 -*-
"""下载视频 + ffmpeg 转码成手表能播的规格。

设计要点：
1. 不同环境的 ffmpeg 编解码器差异极大（有些机器没有 libx264），因此启动时
   自动探测可用的 H.264 编码器并按优先级降级；
2. 压完如果超标 safety ≤ max_size_mb，自动降码率重压，最多 3 轮；
3. 所有输出统一 faststart（moov 前置），保证手表端边下边播、秒开。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import requests

# 按画质/兼容性从优到次
ENCODER_CANDIDATES = (
    ("libx264", ["-profile:v", "main", "-preset", "veryfast", "-pix_fmt", "yuv420p"]),
    ("libopenh264", ["-profile:v", "constrained_baseline", "-pix_fmt", "yuv420p"]),
    ("h264_nvenc", ["-pix_fmt", "yuv420p"]),
    ("h264_qsv", ["-pix_fmt", "yuv420p"]),
    ("h264_vulkan", ["-pix_fmt", "yuv420p"]),
    ("mpeg4", ["-pix_fmt", "yuv420p"]),
)


class MediaError(RuntimeError):
    pass


def _to_kbps(value: str | int) -> int:
    """'380k' / '380K' / 380 / '380000' -> 380（统一转成 kbps 数值）。"""
    text = str(value).strip().lower().rstrip("k")
    number = int(float(text))
    # 看起来像裸比特率的（如 380000）换算成 kbps
    return number // 1000 if number > 100000 else number


class MediaProcessor:
    def __init__(self, cfg) -> None:
        v = cfg.get("video") or {}
        self.ffmpeg = str(v.get("ffmpeg_bin") or "ffmpeg")
        self.ffprobe = str(v.get("ffprobe_bin") or "ffprobe")
        self.max_duration = float(v.get("max_duration") or 45)
        self.width = int(v.get("width") or 320)
        self.height = int(v.get("height") or 320)
        self.fps = int(v.get("fps") or 24)
        self.video_bitrate = str(v.get("video_bitrate") or "380k")
        self.audio_bitrate = str(v.get("audio_bitrate") or "48k")
        self.sample_rate = int(v.get("audio_sample_rate") or 22050)
        self.mono = bool(v.get("mono_audio", True))
        self.max_size = int(float(v.get("max_size_mb") or 8) * 1024 * 1024)
        self.keep_source = bool(v.get("keep_source"))
        self._encoder: str | None = None

    # ---------------------------------------------------------- 工具
    def probe(self, path: str | os.PathLike) -> dict[str, Any]:
        cmd = [self.ffprobe, "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", str(path)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise MediaError(f"ffprobe 失败: {proc.stderr.strip()[:200]}")
        try:
            info = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise MediaError(f"ffprobe 输出解析失败: {exc}") from exc
        fmt = info.get("format") or {}
        streams = info.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        return {
            "duration": float(fmt.get("duration") or 0),
            "size": int(fmt.get("size") or 0),
            "width": video.get("width"),
            "height": video.get("height"),
            "vcodec": video.get("codec_name"),
        }

    def pick_encoder(self, refresh: bool = False) -> str:
        """探测本机可用的 H.264 编码器。"""
        if self._encoder and not refresh:
            return self._encoder
        try:
            out = subprocess.run([self.ffmpeg, "-hide_banner", "-encoders"],
                                 capture_output=True, text=True, timeout=60).stdout
        except FileNotFoundError as exc:
            raise MediaError("未找到 ffmpeg，请安装后重试") from exc
        for name, _ in ENCODER_CANDIDATES:
            if f" {name} " in out:
                self._encoder = name
                return name
        raise MediaError("当前 ffmpeg 没有任何可用的 H.264 编码器")

    # ---------------------------------------------------------- 下载
    def download(self, urls: list[str], headers: dict[str, str], dest: Path,
                 timeout: int = 300) -> Path:
        """下载一个或多个分段到本地，返回可直接交给 ffmpeg 的文件。"""
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not urls:
            raise MediaError("没有可下载的地址")

        parts: list[Path] = []
        for idx, url in enumerate(urls):
            part = dest.with_name(f"{dest.stem}.part{idx}{dest.suffix or '.mp4'}")
            with requests.get(url, headers=headers, stream=True, timeout=timeout) as resp:
                resp.raise_for_status()
                with part.open("wb") as fh:
                    for chunk in resp.iter_content(1024 * 256):
                        if chunk:
                            fh.write(chunk)
            if part.stat().st_size < 1024:
                raise MediaError(f"分段 {idx} 下载异常，只有 {part.stat().st_size} 字节")
            parts.append(part)

        if len(parts) == 1:
            target = parts[0].with_name(dest.name)
            shutil.move(str(parts[0]), str(target))
            return target

        # 多段：用 concat 解复用器无损合并
        listfile = dest.parent / f"{dest.stem}_concat.txt"
        listfile.write_text(
            "".join(f"file '{p.resolve()}'\n" for p in parts), encoding="utf-8")
        cmd = [self.ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0",
               "-i", str(listfile), "-c", "copy", str(dest)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        for p in parts:
            p.unlink(missing_ok=True)
        listfile.unlink(missing_ok=True)
        if proc.returncode != 0 or not dest.exists():
            raise MediaError(f"分段合并失败: {(proc.stderr or '')[:200]}")
        return dest

    # ---------------------------------------------------------- 转码
    def _build_cmd(self, src: Path, dst: Path, vbitrate: str) -> list[str]:
        encoder = self.pick_encoder()
        extra = dict(ENCODER_CANDIDATES)[encoder]
        audio = ["-c:a", "aac", "-b:a", self.audio_bitrate,
                 "-ar", str(self.sample_rate)]
        if self.mono:
            audio += ["-ac", "1"]
        # scale=decrease：等比缩到画布内，避免拉伸变形；pad：居中补黑边。
        # 手表屏幕接近方形，竖屏/宽屏视频都必须归一化到同一画布才能完整显示。
        scale_chain = (
            f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
            f"fps={self.fps},"
            f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
        return [
            self.ffmpeg, "-y", "-v", "error",
            "-i", str(src),
            "-t", str(self.max_duration),
            "-vf", scale_chain,
            "-c:v", encoder, *extra,
            "-b:v", vbitrate,
            "-maxrate", str(int(vbitrate.rstrip("k")) * 1.4) + "k",
            "-bufsize", str(int(vbitrate.rstrip("k")) * 3) + "k",
            *audio,
            "-movflags", "+faststart",
            str(dst),
        ]

    def transcode(self, src: Path, dst: Path) -> dict[str, Any]:
        """转码并在超限时降码率重压，返回成品信息。"""
        dst.parent.mkdir(parents=True, exist_ok=True)
        base_kbps = _to_kbps(self.video_bitrate)
        attempts = [base_kbps, int(base_kbps * 0.6), int(base_kbps * 0.4)]
        last_error = ""

        for kbps in attempts:
            vbitrate = f"{max(80, kbps)}k"
            cmd = self._build_cmd(src, dst, vbitrate)
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            if proc.returncode != 0 or not dst.exists():
                last_error = (proc.stderr or "").strip()[-300:]
                # 编码器异常时换一个再试
                self._encoder = None
                continue

            size = dst.stat().st_size
            info = self.probe(dst)
            if size <= self.max_size:
                info.update({"path": str(dst), "size": size, "bitrate": vbitrate,
                             "encoder": self._encoder})
                return info
            last_error = f"体积 {size} 超过上限 {self.max_size}"

        raise MediaError(f"转码失败（已尝试降码率 3 次）：{last_error}")

    def make_thumbnail(self, src: Path, dst: Path, at: float = 1.5) -> Path | None:
        """抽一张封面图给家长看板用。"""
        dst.parent.mkdir(parents=True, exist_ok=True)
        cmd = [self.ffmpeg, "-y", "-v", "error", "-ss", str(at), "-i", str(src),
               "-frames:v", "1", "-q:v", "5", str(dst)]
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
        return dst if proc.returncode == 0 and dst.exists() else None

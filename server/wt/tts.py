# -*- coding: utf-8 -*-
"""可选：把回复文字合成为语音，给不支持视频的手表型号做回退通道。

默认使用 edge-tts（免费、无需 Key、中文音色好）。未安装时自动跳过，
不影响主链路。想换自己的方案，只要保证 synthesize() 返回 True 即可。
"""
from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from typing import Any


def synthesize(text: str, dest: Path, cfg: dict[str, Any] | None = None) -> bool:
    """合成语音到 dest。成功返回 True。"""
    cfg = cfg or {}
    provider = ((cfg.get("tts") or {}).get("provider") if isinstance(cfg, dict) else None) or "edge"
    voice = ((cfg.get("tts") or {}).get("voice") if isinstance(cfg, dict) else None) or "zh-CN-XiaoxiaoNeural"

    if provider == "edge":
        return _edge_tts(text, dest, voice)
    if provider == "none":
        return False
    return _edge_tts(text, dest, voice)


def _edge_tts(text: str, dest: Path, voice: str) -> bool:
    try:
        edge_tts = importlib.import_module("edge_tts")
    except ImportError:
        return False

    async def _run() -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        communicate = edge_tts.Communicate(text, voice)
        tmp = dest.with_suffix(".tmp.mp3")
        await communicate.save(str(tmp))
        dest.unlink(missing_ok=True)
        tmp.rename(dest)

    try:
        asyncio.run(_run())
        return dest.exists() and dest.stat().st_size > 1024
    except Exception:
        return False

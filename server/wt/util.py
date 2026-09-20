# -*- coding: utf-8 -*-
"""通用小工具。"""
from __future__ import annotations

import html
import logging
import re
import unicodedata

_TAGS = re.compile(r"</?[a-zA-Z][^>]*>")
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format=LOG_FORMAT,
        datefmt="%H:%M:%S",
    )
    # 压掉第三方库的噪音
    for name in ("urllib3", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def clean_html(text: str) -> str:
    """去掉 html 标签（B站搜索结果标题里带 <em class="keyword">）。"""
    if not text:
        return ""
    return html.unescape(_TAGS.sub("", text)).strip()


def parse_duration(text: str | int | None) -> int:
    """'2:42' -> 162；'10:23:11' -> 37191；'162' -> 162。"""
    if text is None:
        return 0
    if isinstance(text, (int, float)):
        return int(text)
    parts = str(text).strip().split(":")
    total = 0
    for part in parts:
        try:
            total = total * 60 + int(part)
        except ValueError:
            return 0
    return total


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024:
            return f"{num:.1f}{unit}"
        num /= 1024
    return f"{num:.1f}TB"


def normalize(text: str) -> str:
    """全角转半角、去首尾空白，用于稳定生成指纹。"""
    return unicodedata.normalize("NFKC", text or "").strip()


def safe_filename(text: str, limit: int = 40) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fa5-]+", "_", text or "")
    return cleaned[:limit].strip("_") or "video"

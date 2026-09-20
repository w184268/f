# -*- coding: utf-8 -*-
"""B站检索与取流。

实测（2026-09）可用路径：
    搜索  api.bilibili.com/x/web-interface/search/type       需 UA + Referer，无需签名
    详情  api.bilibili.com/x/web-interface/view               取 cid / 时长 / 分区
    取流  api.bilibili.com/x/player/wbi/playurl               需 wbi 签名
          fnval=0 时返回单个 mp4 直链（durl），省去 DASH 音视频合并，最适合做裁切
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import urllib.parse
from pathlib import Path
from typing import Any

import requests

from .util import clean_html, parse_duration

log = logging.getLogger("wt.bili")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
REFERER = "https://www.bilibili.com/"

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

BiliError = RuntimeError


class Bilibili:
    """B站客户端。

    wbi 密钥三级获取：本地磁盘缓存 → nav 接口 → 内置兜底值。
    实测 B站现在即使返回 code=-101（未登录），payload 里仍会带上 wbi_img，
    因此 nav 走"宽容解析"，不能一看到非零 code 就判失败。
    """

    # 兜底值：公开且半年才轮换一次，nav 不可用时顶上
    FALLBACK_WBI = ("7cd084941338484aae1ad9425b84077c",
                    "4932caff0ff746eab6f01bf08b70ac45")

    def __init__(self, timeout: float = 20, retries: int = 3,
                 cache_path: str | Path | None = None) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Referer": REFERER,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        self.timeout = timeout
        self.retries = retries
        self._wbi_keys: tuple[str, str] | None = None
        self._wbi_ts = 0.0
        self.cache_path = Path(cache_path) if cache_path else None
        self._seed_session()

    # ------------------------------------------------------ 基础请求
    def _seed_session(self) -> None:
        """拿游客 cookie（buvid3/b_nut），降低被风控概率。"""
        try:
            self.session.get("https://www.bilibili.com/", timeout=10)
        except Exception:
            pass

    def _get(self, url: str, params: dict[str, Any] | None = None, **kw) -> dict[str, Any]:
        """严格模式：code 必须为 0。"""
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout, **kw)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict) and data.get("code") not in (0, None):
                    raise BiliError(f"B站返回错误 code={data.get('code')} msg={data.get('message')}")
                return data
            except Exception as exc:
                last = exc
                if "412" in str(exc):
                    self._seed_session()
                time.sleep(0.6 * (attempt + 1) + random.random() * 0.3)
        raise BiliError(f"请求失败 {url}: {last}")

    def _fetch_json(self, url: str) -> dict[str, Any]:
        """宽容模式：只要 body 是 JSON 就返回，用于 nav 这类"带错也要数据"的接口。"""
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        obj = resp.json()
        return obj if isinstance(obj, dict) else {}

    # ------------------------------------------------------ wbi 签名
    @staticmethod
    def _mixin_key(orig: str) -> str:
        return "".join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]

    def _load_cached_wbi(self) -> tuple[str, str] | None:
        if not self.cache_path or not self.cache_path.is_file():
            return None
        try:
            obj = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if time.time() - float(obj.get("ts", 0)) < 86400 and obj.get("img") and obj.get("sub"):
                return str(obj["img"]), str(obj["sub"])
        except Exception:
            pass
        return None

    def _save_cached_wbi(self, keys: tuple[str, str]) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps({"ts": time.time(), "img": keys[0], "sub": keys[1]}),
                encoding="utf-8")
        except Exception:
            pass

    def _wbi(self) -> tuple[str, str]:
        if self._wbi_keys and time.time() - self._wbi_ts < 1800:
            return self._wbi_keys

        # 1) 磁盘缓存（跨进程复用，避免频繁打 nav）
        cached = self._load_cached_wbi()
        if cached:
            self._wbi_keys, self._wbi_ts = cached, time.time()
            return cached

        keys: tuple[str, str] | None = None
        # 2) nav 接口：即使 code=-101，payload 里通常仍有 wbi_img
        try:
            data = self._fetch_json("https://api.bilibili.com/x/web-interface/nav")
            img = (data.get("data") or {}).get("wbi_img")
            if img and img.get("img_url") and img.get("sub_url"):
                keys = (img["img_url"].rsplit("/", 1)[1].split(".")[0],
                        img["sub_url"].rsplit("/", 1)[1].split(".")[0])
        except Exception:
            keys = None

        # 3) 兜底
        if not keys:
            keys = self.FALLBACK_WBI

        self._save_cached_wbi(keys)
        self._wbi_keys, self._wbi_ts = keys, time.time()
        return keys

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        img_key, sub_key = self._wbi()
        mixin = self._mixin_key(img_key + sub_key)
        params = dict(params)
        params["wts"] = int(time.time())
        params = dict(sorted(params.items()))
        params = {k: "".join(c for c in str(v) if c not in "!'()*")
                  for k, v in params.items()}
        query = urllib.parse.urlencode(params)
        params["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
        return params

    # ------------------------------------------------------ 搜索
    def search(self, keyword: str, limit: int = 12, order: str = "totalrank") -> list[dict[str, Any]]:
        data = self._get(
            "https://api.bilibili.com/x/web-interface/search/type",
            {"search_type": "video", "keyword": keyword, "order": order, "page": 1},
        )
        results = (data.get("data") or {}).get("result") or []
        out: list[dict[str, Any]] = []
        for item in results[:limit]:
            out.append({
                "bvid": item.get("bvid"),
                "aid": item.get("aid"),
                "title": clean_html(item.get("title")),
                "author": item.get("author"),
                "mid": item.get("mid"),
                "typename": item.get("typename"),
                "play": item.get("play") or 0,
                "duration": parse_duration(item.get("duration")),
                "pic": item.get("pic"),
                "url": item.get("arcurl") or f"https://www.bilibili.com/video/{item.get('bvid')}",
                "description": clean_html(item.get("description") or "")[:200],
                "pubdate": item.get("pubdate") or 0,
            })
        return out

    # ------------------------------------------------------ 详情
    def detail(self, bvid: str) -> dict[str, Any]:
        data = self._get("https://api.bilibili.com/x/web-interface/view", {"bvid": bvid})["data"]
        return {
            "bvid": data["bvid"],
            "cid": data["cid"],
            "title": data.get("title"),
            "author": (data.get("owner") or {}).get("name"),
            "duration": data.get("duration") or 0,
            "pic": data.get("pic"),
            "typename": data.get("tname_v2") or data.get("tname") or "",
            "url": f"https://www.bilibili.com/video/{data['bvid']}",
        }

    # ------------------------------------------------------ 取流
    def play_url(self, bvid: str, cid: int, quality: int = 16) -> dict[str, Any]:
        """返回 {'format': 'mp4', 'urls': [...], 'quality': n}。

        fnval=0 -> mp4 单文件直链列表（durl）。
        fnval=1 -> DASH，需要音视频分别下载后合并，作为兜底。
        """
        errors: list[str] = []
        for fnval in (0, 1):
            try:
                params = self._sign({
                    "bvid": bvid, "cid": cid, "qn": quality, "fnval": fnval,
                    "fourk": 0, "platform": "pc", "high_quality": 0,
                })
                data = self._get("https://api.bilibili.com/x/player/wbi/playurl", params)["data"]
            except Exception as exc:
                errors.append(f"fnval={fnval}: {exc}")
                continue

            if fnval == 0 and data.get("durl"):
                return {
                    "kind": "mp4",
                    "format": data.get("format", "mp4"),
                    "quality": data.get("quality", quality),
                    "urls": [seg["url"] for seg in data["durl"]],
                }
            if fnval == 1 and data.get("dash"):
                dash = data["dash"]
                videos = sorted(dash.get("video") or [], key=lambda x: x.get("height", 0))
                audio = (dash.get("audio") or [{}])[0]
                if not videos:
                    continue
                low = videos[0]
                urls = [low.get("baseUrl") or low.get("base_url")]
                if audio.get("baseUrl"):
                    urls.append(audio["baseUrl"])
                return {
                    "kind": "dash",
                    "format": "dash",
                    "quality": data.get("quality", quality),
                    "urls": [u for u in urls if u],
                }
        raise BiliError("未能取得可用的播放地址；" + " | ".join(errors))

    def download_headers(self) -> dict[str, str]:
        return {"User-Agent": UA, "Referer": REFERER}


def normalize_pic(url: str) -> str:
    """B站封面图统一走 https 并压缩规格。"""
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    return url.replace("http://", "https://")

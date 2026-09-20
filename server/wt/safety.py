# -*- coding: utf-8 -*-
"""儿童安全过滤与选片打分。

思路：先做硬性否决（成人/猎奇内容一票否决），再按儿童友好度、播放量、时长排序。
宁可少给一条，也不能让孩子看到不该看的。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .util import clean_html, parse_duration


@dataclass
class Verdict:
    ok: bool
    score: float
    reasons: list[str]

    def explain(self) -> str:
        return "；".join(self.reasons) if self.reasons else "-"


def _bigrams(text: str) -> set[str]:
    """中文按字、英文按词的二元组，用于粗算语义重叠。

    为什么需要它：B站搜索会按分词把"为什么海水是咸的"拆成
    "为什么 / 海水 / 咸"，热搜结果里很可能混进《火锅里的海带为什么要打结》
    这种只共享虚词的内容。不校验相关性的话，孩子就会收到莫名其妙的视频。
    """
    cleaned = re.sub(r"[^\w\u4e00-\u9fa5]", "", text or "")
    cleaned = cleaned.lower()
    if len(cleaned) < 2:
        return {cleaned} if cleaned else set()
    return {cleaned[i:i + 2] for i in range(len(cleaned) - 1)}


# 提问里的"虚词"：去掉之后再算相关性，让真正的名词主导结果
_QUESTION_WORDS = (
    "为什么", "为啥", "为何", "怎么", "怎样", "如何", "什么", "是不是", "会不会",
    "能不能", "有没有", "多少", "多久", "叫啥", "我想", "帮我", "请问",
    "和", "跟", "还是", "的", "了", "吗", "呢", "呀", "啊", "是",
)


def core_query(query: str) -> str:
    """剥掉疑问虚词，留下核心对象。'为什么海水是咸的' → '海水咸'。"""
    out = query or ""
    for w in _QUESTION_WORDS:
        out = out.replace(w, "")
    return out or (query or "")


def relevance(query: str, title: str) -> float:
    """查询词与标题的相关度，0~1。先去掉虚词，只看核心名词的重合。"""
    a, b = _bigrams(core_query(query)), _bigrams(title)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a))


def screen(candidate: dict[str, Any], cfg, query: str = "") -> Verdict:
    """对单个候选视频做安全筛查 + 打分。"""
    s = cfg.get("search") or {}
    min_relevance = float(s.get("min_relevance") or 0.15)
    blocked = [w for w in (s.get("blocked_keywords") or []) if w]
    hints = [w for w in (s.get("child_friendly_hints") or []) if w]
    prefer_type = [w for w in (s.get("prefer_typenames") or []) if w]
    prefer_authors = [w for w in (s.get("prefer_authors") or []) if w]
    min_play = int(s.get("min_play") or 0)
    lo, hi = (s.get("prefer_duration") or [20, 900])[:2]

    title = clean_html(candidate.get("title") or "")
    author = candidate.get("author") or ""
    typename = candidate.get("typename") or ""
    duration = int(candidate.get("duration") or 0)
    play = int(candidate.get("play") or 0)

    blob = f"{title} {author} {typename}"
    reasons: list[str] = []
    score = 50.0

    # 1) 硬性否决
    for word in blocked:
        if word and word in blob:
            return Verdict(False, 0, [f"命中黑名单词「{word}」"])
    if not title:
        return Verdict(False, 0, ["标题为空"])

    # 2) 播放量门槛：过滤低质营销号
    if min_play and play < min_play:
        return Verdict(False, 0, [f"播放量 {play} 低于阈值 {min_play}"])

    # 2.5) 相关性：标题和提问风马牛不相及的一律不要
    if query:
        rel = relevance(query, title)
        if rel < min_relevance:
            return Verdict(False, 0, [f"与提问不相关（相关度 {rel:.2f} < {min_relevance}）"])
        score += rel * 30
        reasons.append(f"相关度 {rel:.2f}")

    # 3) 时长偏好
    if duration <= 0:
        score -= 5
        reasons.append("时长未知，轻微扣分")
    elif duration < lo:
        score -= 25
        reasons.append(f"时长 {duration}s 偏短")
    elif duration > hi:
        score -= 20
        reasons.append(f"时长 {duration}s 偏长")
    else:
        score += 10
        reasons.append("时长合适")

    # 4) 儿童友好加权
    hit = [w for w in hints if w and w in blob]
    if hit:
        score += 8 * len(hit)
        reasons.append("命中儿童友好词：" + "、".join(hit[:3]))

    # 5) 优先分区 / UP 主
    if any(w and w in typename for w in prefer_type):
        score += 10
        reasons.append(f"分区「{typename}」优先")
    if prefer_authors and any(w and w in author for w in prefer_authors):
        score += 25
        reasons.append("UP 主在优先名单")

    # 6) 热度作为弱信号
    if play >= 100000:
        score += 12
        reasons.append(f"播放 {play}，热度高")
    elif play >= 20000:
        score += 6

    return Verdict(True, score, reasons)


def needs_manual_review(candidate: dict[str, Any], cfg) -> str | None:
    """返回触发人工审核的原因，不需要审核则返回 None。"""
    m = cfg.get("moderation") or {}
    keywords = [w for w in (m.get("always_review_keywords") or []) if w]
    blob = f"{clean_html(candidate.get('title') or '')} {candidate.get('author') or ''}"
    for w in keywords:
        if w in blob:
            return f"命中需复核词「{w}」"
    return None


def rank(candidates: list[dict[str, Any]], cfg, query: str = "") -> list[tuple[dict[str, Any], Verdict]]:
    """过滤 + 打分排序，返回 [(候选, 评判)]。"""
    scored: list[tuple[dict[str, Any], Verdict]] = []
    for cand in candidates:
        v = screen(cand, cfg, query=query)
        if v.ok:
            scored.append((cand, v))
    scored.sort(key=lambda x: x[1].score, reverse=True)
    return scored

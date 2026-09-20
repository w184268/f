# -*- coding: utf-8 -*-
"""把孩子的一句话变成：要不要找视频 + 搜什么词 + 怎么回复。

主路径用大模型；没有 Key 或调用失败时降级到本地规则引擎，保证链路永远不断。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from typing import Any

import requests


@dataclass
class Understanding:
    is_request: bool
    query: str
    reply: str
    note: str = ""
    engine: str = "rule"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SYSTEM_PROMPT = """你是帮家长回应孩子提问的助手。孩子用电话手表发来一句话（常含错别字、口语、语音转写错误）。
你要判断这句话是不是"想了解某个知识 / 想看某方面内容"的请求，并按规则输出 JSON。

只输出 JSON，不要任何解释或多余文字：
{
  "is_request": true 或 false,
  "query": "用于视频网站搜索的关键词，8到15字，去口语化的书面词，只保留核心对象和问题",
  "reply": "以{persona}的口吻写给孩子的一句话，30字以内，温暖自然，直接说给他找了什么；不要出现'搜索''网络''关键词'这类词",
  "note": "一句话说明理由"
}

判断规则：
1. 孩子表述含糊、有错字、方言时，要推断真实意图。"那个大恐龙叫啥"→ query 写"恐龙的种类和名字"。
2. 普通闲聊、报平安、让家长接人（如"我到学校了""你几点来接我"）→ is_request 设 false，reply 给得体回应。
3. 涉及色情/暴力/自残/危险行为/他人隐私 → is_request 设 false，note 写明"需家长人工处理"。
4. query 要用孩子能看懂的内容方向，优先科普、动画、手工、动物、实验类。
"""

FEW_SHOT = [
    {"role": "user", "content": "妈妈，为啥天上的星星不会掉下来？"},
    {"role": "assistant", "content": '{"is_request": true, "query": "星星为什么不会掉下来 科普", "reply": "宝贝这个问题问得真好，妈妈给你找了讲星星的视频，戴上手表慢慢看～", "note": "天文类科普提问"}'},
    {"role": "user", "content": "霸王龙和三角龙打架谁赢"},
    {"role": "assistant", "content": '{"is_request": true, "query": "霸王龙 三角龙 谁厉害", "reply": "哈哈这个问题有意思，来看看这两种恐龙到底谁更厉害吧！", "note": "恐龙知识，适合科普动画"}'},
    {"role": "user", "content": "我到学校啦"},
    {"role": "assistant", "content": '{"is_request": false, "query": "", "reply": "收到，好好上课，妈妈晚上去接你～", "note": "报平安，不是内容请求"}'},
    {"role": "user", "content": "怎么才能让我同桌消失"},
    {"role": "assistant", "content": '{"is_request": false, "query": "", "reply": "这个问题妈妈想当面跟你聊聊，放学我们谈一谈。", "note": "涉及伤害他人，需家长人工介入"}'},
]

_JSON_RE = re.compile(r"\{.*\}", re.S)

# ---------------------------------------------------------------- 规则引擎
QUESTION_HINTS = (
    "什么", "怎么", "怎样", "如何", "为什么", "为啥", "为何", "谁", "哪儿", "哪里",
    "是不是", "会不会", "多少", "几个", "多久", "呢", "吗", "?", "？",
)
REQUEST_HINTS = (
    "想看", "看看", "想学", "教我", "告诉我", "给我找", "查一下", "查查", "有没有",
    "讲讲", "说一下", "我看", "帮我找", "搜一下", "不会做", "怎么写", "怎么做",
)
STOPWORDS = (
    "妈妈", "爸爸", "家长", "手表", "手表里", "一下", "一个", "这个", "那个", "我想",
    "我要", "你说", "帮我", "请问", "谢谢", "求求", "快点", "现在", "可以", "能不能",
    "你好", "在吗", "宝贝", "给我", "你知", "不知道", "emmm", "那个那个",
)
NON_REQUEST_PATTERNS = (
    r"^(好的?|嗯+|哦+|知道啦?|收到|ok|okay)$",
    r"(几点|什么时候).{0,4}(来接|过来|到)",
    r"^(我)?(到|回)(学校|家|教室)了?$",
    r"(在|去)?(吃饭|睡觉|写作业|上课|厕所|写作业)",
)


def _rule_engine(text: str, persona: str = "一位耐心的家长") -> Understanding:
    clean = re.sub(r"\s+", "", text or "")
    if not clean:
        return Understanding(False, "", "", "空消息", "rule")

    for pat in NON_REQUEST_PATTERNS:
        if re.search(pat, clean, re.I):
            return Understanding(False, "", "好哒，妈妈知道啦～", "规则判定：日常沟通，非内容请求", "rule")

    low = clean.lower()
    is_req = any(h in low for h in REQUEST_HINTS) or any(h in low for h in QUESTION_HINTS)
    if not is_req or len(clean) < 4:
        return Understanding(False, "", "收到～", "规则判定：无明显知识请求", "rule")

    query = clean
    for sw in STOPWORDS:
        query = query.replace(sw, "")
    query = re.sub(r"[，。！？、,.!?~～]+", " ", query).strip()
    query = re.sub(r"\s+", " ", query)
    if len(query) > 15:
        query = query[:15]
    if not query:
        query = clean[:12]

    return Understanding(
        True, query, f"妈妈给你找了个讲「{query}」的小视频，看完回来讲给我听好不好？",
        "规则引擎提取关键词", "rule",
    )


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        # 大模型偶尔输出残缺 JSON，做一次宽松尝试
        frag = m.group(0)
        for suffix in ("}", '"}'):
            try:
                return json.loads(frag + suffix)
            except Exception:
                continue
        return None


class Brain:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        u = cfg.get("understanding") or {}
        self.enabled = bool(u.get("enabled")) and bool(u.get("api_key"))
        self.base_url = str(u.get("base_url") or "").rstrip("/")
        self.api_key = u.get("api_key") or ""
        self.model = u.get("model") or "deepseek-chat"
        self.temperature = float(u.get("temperature") or 0.3)
        self.timeout = float(u.get("timeout") or 25)
        self.fallback = bool(u.get("fallback_to_rules", True))
        self.persona = u.get("reply_persona") or "一位耐心的家长"

    def understand(self, text: str) -> Understanding:
        if not self.enabled:
            return _rule_engine(text, self.persona)
        try:
            data = self._call_llm(text)
            return Understanding(
                is_request=bool(data.get("is_request")),
                query=str(data.get("query") or "").strip()[:40],
                reply=str(data.get("reply") or "").strip()[:80],
                note=str(data.get("note") or "").strip()[:200],
                engine="llm",
            )
        except Exception as exc:  # 网络/解析失败都要能降级
            if not self.fallback:
                raise
            out = _rule_engine(text, self.persona)
            out.note = f"LLM 不可用({type(exc).__name__})，已降级：{out.note}"
            return out

    def _call_llm(self, text: str) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                # 注意：提示词里含有 JSON 示例的大括号，绝不能用 str.format，
                # 否则 { 会被当成占位符解析成 KeyError —— 这里必须用 replace
                {"role": "system", "content": SYSTEM_PROMPT.replace("{persona}", self.persona)},
                *FEW_SHOT,
                {"role": "user", "content": text[:200]},
            ],
        }
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json=payload, timeout=self.timeout,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        data = _extract_json(content)
        if not data or "is_request" not in data:
            raise ValueError("未能从模型输出中解析出 JSON")
        return data

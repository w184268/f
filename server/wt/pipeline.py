# -*- coding: utf-8 -*-
"""任务编排：收到消息 -> 理解 -> 检索 -> 选片 -> 取视频 -> 压片 -> 入待发队列。"""
from __future__ import annotations

import logging
import secrets
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .bili import Bilibili, normalize_pic
from .brain import Brain
from .media import MediaProcessor, MediaError
from .store import Store, fingerprint
from .safety import core_query as safety_core_query, needs_manual_review, rank
from .tts import synthesize

log = logging.getLogger("wt.pipeline")


class Pipeline:
    def __init__(self, cfg, store: Store) -> None:
        self.cfg = cfg
        self.store = store
        self.bili = Bilibili(cache_path=Path(cfg["storage"]["db_path"]).parent / "wbi_cache.json")
        self.media = MediaProcessor(cfg)
        self.brain = Brain(cfg)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wt-worker")
        self.media_dir = Path(cfg["storage"]["media_dir"])
        self.public_base = str(cfg["server"].get("public_base_url") or "").rstrip("/")

    # ------------------------------------------------------------------ 入口
    def submit(self, *, device_id: str, sender: str, raw_text: str) -> dict[str, Any]:
        """立即返回，真正的处理丢到后台线程。"""
        text = (raw_text or "").strip()
        fp = fingerprint(sender, text)
        dedupe_window = float((self.cfg.get("delivery") or {}).get("dedupe_window") or 0)

        if dedupe_window and self.store.recent_fingerprint_exists(fp, dedupe_window):
            log.info("重复消息，已忽略：%s", text[:40])
            return {"skipped": True, "reason": "duplicate"}

        task_id = self.store.create_task(
            device_id=device_id, sender=sender, raw_text=text, fingerprint=fp)
        self.executor.submit(self._safe_run, task_id)
        return {"task_id": task_id, "skipped": False}

    def _safe_run(self, task_id: int) -> None:
        try:
            self.run(task_id)
        except Exception as exc:  # 兜底，绝不让线程静默死亡
            log.exception("任务 %s 处理异常", task_id)
            self.store.set_state(task_id, "failed", error=f"{type(exc).__name__}: {exc}")
            self._notify_parent(task_id, f"处理失败：{exc}")

    def _safe_produce(self, task_id: int, pick: dict[str, Any]) -> None:
        """家长批准后的后台生产，异常处理与主流程一致。"""
        try:
            self._produce(task_id, pick)
        except Exception as exc:
            log.exception("任务 %s 批准后生产失败", task_id)
            self.store.set_state(task_id, "failed", error=f"{type(exc).__name__}: {exc}")
            self._notify_parent(task_id, f"出片失败：{exc}")

    # ------------------------------------------------------------------ 主流程
    def run(self, task_id: int) -> None:
        task = self.store.get(task_id)
        if not task:
            return
        text = task["raw_text"] or ""

        # 1) 理解意图
        und = self.brain.understand(text)
        self.store.log(task_id, "info",
                       f"[{und.engine}] is_request={und.is_request} query={und.query!r} {und.note}")
        self.store.update(task_id, is_request=int(und.is_request), query=und.query,
                          reply_text=und.reply, decision_note=und.note,
                          state="understood")

        # 2) 非内容请求：只回一句话（手机端会以文字形式发回手表）
        if not und.is_request:
            self.store.set_state(task_id, "no_need")
            self.store.update(task_id, state="ready")
            log.info("任务 %s：非内容请求，仅回复文本", task_id)
            return

        # 3) 配额检查
        limit = int((self.cfg.get("delivery") or {}).get("daily_quota") or 20)
        if self.store.quota_left(limit) <= 0:
            self.store.set_state(task_id, "rejected", error="今日下发额度已用完")
            return

        # 4) 搜索 + 安全过滤 + 选片
        self.store.set_state(task_id, "searching")
        max_cand = int((self.cfg.get("search") or {}).get("max_candidates") or 12)
        candidates = self.bili.search(und.query, limit=max_cand)
        self.store.log(task_id, "info", f"检索到 {len(candidates)} 个候选")

        scored = rank(candidates, self.cfg, query=und.query)

        # 兜底：如果全部候选都被判定不相关，改用剥离虚词后的核心词再搜一轮。
        # 例："我想知道太阳为什么会发光呢" 直接用原句搜容易颗粒度太细。
        if not scored:
            core = safety_core_query(und.query)
            if core and core != und.query:
                self.store.log(task_id, "info", f"宽口径重搜：{core}")
                scored = rank(self.bili.search(core, limit=max_cand), self.cfg, query=und.query)

        if not scored:
            self.store.set_state(task_id, "failed", error="没有通过安全过滤的候选视频")
            self.store.update(task_id, reply_text="妈妈没找到合适的视频，晚上回来陪你一起查～")
            self.store.update(task_id, state="ready")
            return

        self.store.update(task_id, candidates=[{
            "bvid": c["bvid"], "title": c["title"], "author": c["author"],
            "play": c["play"], "duration": c["duration"],
            "score": round(v.score, 1), "reason": v.explain(),
        } for c, v in scored[:5]])

        pick, verdict = scored[0]
        self.store.log(task_id, "info",
                       f"选中 {pick['bvid']} 《{pick['title']}》 分数={verdict.score:.1f}｜{verdict.explain()}")

        review_reason = needs_manual_review(pick, self.cfg)
        mode = str((self.cfg.get("moderation") or {}).get("mode") or "auto")
        if review_reason or mode == "review":
            self.store.update(task_id, source_id=pick["bvid"], source_title=pick["title"],
                              source_author=pick["author"], source_url=pick["url"],
                              source_duration=pick["duration"],
                              cover_url=normalize_pic(pick["pic"]),
                              needs_review=1, state="review_pending",
                              decision_note=review_reason or "家长审核模式")
            self._notify_parent(task_id, f"待审核：《{pick['title']}》 · {review_reason or ''}")
            return

        # 5) 取视频并压片
        self._produce(task_id, pick)

    def _produce(self, task_id: int, pick: dict[str, Any]) -> None:
        work_dir = self.media_dir / f"t{task_id}"
        work_dir.mkdir(parents=True, exist_ok=True)

        detail = self.bili.detail(pick["bvid"])
        stream = self.bili.play_url(pick["bvid"], detail["cid"])
        self.store.log(task_id, "info",
                       f"取流成功 kind={stream['kind']} 分段={len(stream['urls'])}")

        raw = work_dir / "source.mp4"
        self.store.set_state(task_id, "downloading")
        self.media.download(stream["urls"], self.bili.download_headers(), raw)

        info = self.media.probe(raw)
        self.store.log(task_id, "info",
                       f"原始：{detail['duration']}s {info.get('width')}x{info.get('height')} "
                       f"{info['size'] // 1024}KB")

        token = secrets.token_urlsafe(12)
        out = self.media_dir / f"t{task_id}_{token}.mp4"
        self.store.set_state(task_id, "transcoding")
        result = self.media.transcode(raw, out)

        thumb_rel: str | None = None
        thumb = self.media.make_thumbnail(out, work_dir / "thumb.jpg")
        if thumb:
            thumb_rel = f"t{task_id}/thumb.jpg"

        reply_mode = str((self.cfg.get("delivery") or {}).get("reply_mode") or "video")
        audio_path: str | None = None
        if reply_mode in ("audio", "auto"):
            audio_path = self._fallback_audio(task_id, und_text=self.store.get(task_id))

        if not self.cfg["video"].get("keep_source"):
            raw.unlink(missing_ok=True)

        self.store.update(
            task_id,
            source_id=pick["bvid"],
            source_title=detail["title"] or pick["title"],
            source_author=detail["author"] or pick["author"],
            source_url=detail["url"],
            source_duration=int(detail["duration"] or 0),
            cover_url=normalize_pic(detail["pic"] or pick["pic"]),
            video_path=out.name,
            video_bytes=result["size"],
            video_duration=result["duration"],
            delivery_token=token,
            needs_review=0,
            state="ready",
        )
        self.store.quota_consume()
        self.store.log(
            task_id, "info",
            f"成品就绪：{result['size'] // 1024}KB / {result['duration']:.0f}s / "
            f"{result['width']}x{result['height']} / {result['encoder']}")

        # 家长要知道发了什么
        self._notify_parent(
            task_id,
            f"已给孩子准备《{detail['title'] or pick['title']}》"
            f"（{result['size'] // 1024}KB）")

    def _fallback_audio(self, task_id: int, *, und_text: dict[str, Any] | None) -> str | None:
        """备选通道：把回复文字转成语音，供不支持视频的手表型号使用。"""
        try:
            text = (und_text or {}).get("reply_text") or ""
            if not text:
                return None
            path = self.media_dir / f"t{task_id}_reply.mp3"
            if synthesize(text, path, self.cfg):
                self.store.log(task_id, "info", "已生成语音回退文件")
                return path.name
        except Exception as exc:
            self.store.log(task_id, "warn", f"语音回退生成失败：{exc}")
        return None

    # ------------------------------------------------------------------ 对外
    def build_outbox_item(self, task: dict[str, Any]) -> dict[str, Any]:
        """拼出手机端需要的下发描述。"""
        item: dict[str, Any] = {
            "task_id": task["id"],
            "reply_text": task.get("reply_text") or "",
            "created_at": task.get("created_at"),
            "source_title": task.get("source_title"),
            "source_author": task.get("source_author"),
            "source_url": task.get("source_url"),
        }
        if task.get("video_path") and task.get("delivery_token"):
            item.update({
                "kind": "video",
                "video_url": f"{self.public_base}/m/{task['delivery_token']}.mp4",
                "cover_url": task.get("cover_url") or "",
                "duration": task.get("video_duration"),
                "size": task.get("video_bytes"),
            })
        else:
            item["kind"] = "text"
        return item

    def _notify_parent(self, task_id: int, message: str) -> None:
        """可选：给家长手机推一条通知（Bark / Server酱）。"""
        n = self.cfg.get("notify") or {}
        if not n.get("enabled"):
            return
        try:
            import requests

            if n.get("bark_url"):
                requests.post(str(n["bark_url"]).rstrip("/") + "/",
                              json={"title": "WatchTube", "body": message,
                                    "group": "watchtube"}, timeout=8)
            if n.get("serverchan_key"):
                requests.post(
                    f"https://sctapi.ftqq.com/{n['serverchan_key']}.send",
                    data={"title": "WatchTube", "desp": message}, timeout=8)
        except Exception as exc:
            self.store.log(task_id, "warn", f"家长通知推送失败：{exc}")

# -*- coding: utf-8 -*-
"""HTTP API：手机桥接端 ↔ 服务端，以及家长看板。"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .config import Section, load_config
from .pipeline import Pipeline
from .store import Store

log = logging.getLogger("wt.api")


# ----------------------------------------------------------------- 请求体
class IngestIn(BaseModel):
    device_id: str = Field(default="phone", max_length=64)
    sender: str = Field(default="孩子", max_length=64)
    text: str = Field(..., max_length=500)
    ts: float | None = None
    kind: str = "text"


class AckIn(BaseModel):
    task_id: int
    status: str = "delivered"
    note: str = ""


# ----------------------------------------------------------------- 装配
class AppState:
    def __init__(self) -> None:
        self.cfg: Section | None = None
        self.store: Store | None = None
        self.pipeline: Pipeline | None = None
        self.lock = threading.Lock()


_state = AppState()


def get_store() -> Store:
    assert _state.store is not None
    return _state.store


def get_pipeline() -> Pipeline:
    assert _state.pipeline is not None
    return _state.pipeline


def build_app(config_path: str | None = None) -> FastAPI:
    cfg = load_config(config_path)
    store = Store(cfg["storage"]["db_path"])
    pipeline = Pipeline(cfg, store)
    _state.cfg, _state.store, _state.pipeline = cfg, store, pipeline

    from .util import setup_logging
    setup_logging("INFO")

    app = FastAPI(title="WatchTube", version="1.0.0",
                  description="手表提问 → 自动找科普视频 → 发回手表")

    # 手机端在浏览器/WebView 调试时会用到，实际正式调用不需要，但开着更省心
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )

    async def verify_key(x_api_key: str | None = Header(default=None)) -> None:
        expected = str(cfg["server"].get("bridge_key") or "")
        # 配置没改过的默认 key，直接放行，避免第一次试用就被权限卡住
        if expected in ("", "change-me-please") or x_api_key == expected:
            return
        raise HTTPException(status_code=401, detail="invalid api key")

    # ------------------------------------------------------------ 健康检查
    @app.get("/api/v1/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "service": "watchtube",
            "public_base_url": cfg["server"].get("public_base_url") or "",
            "mode": (cfg.get("moderation") or {}).get("mode", "auto"),
            "understanding": "llm" if pipeline.brain.enabled else "rule",
            "stats": get_store().stats(),
        }

    # ------------------------------------------------------------ 收：孩子消息
    @app.post("/api/v1/ingest")
    async def ingest(payload: IngestIn, _=Depends(verify_key)) -> dict[str, Any]:
        text = payload.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="empty text")
        result = get_pipeline().submit(
            device_id=payload.device_id, sender=payload.sender, raw_text=text)
        return {"ok": True, **result}

    # ------------------------------------------------------------ 发：待下发队列
    @app.get("/api/v1/outbox")
    async def outbox(limit: int = 3, _=Depends(verify_key)) -> dict[str, Any]:
        pipe = get_pipeline()
        rows = get_store().list_ready(min(max(limit, 1), 10))
        return {"ok": True, "items": [pipe.build_outbox_item(r) for r in rows]}

    @app.post("/api/v1/ack")
    async def ack(payload: AckIn, _=Depends(verify_key)) -> dict[str, Any]:
        store = get_store()
        task = store.get(payload.task_id)
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        if payload.status == "delivered":
            store.mark_delivered(payload.task_id)
        elif payload.status == "failed":
            attempt = store.bump_attempt(payload.task_id)
            store.set_state(payload.task_id, "failed",
                            error=f"下发失败：{payload.note}"[:300])
            store.log(payload.task_id, "warn", f"第 {attempt} 次下发失败：{payload.note}")
        else:
            raise HTTPException(status_code=400, detail="unknown status")
        return {"ok": True}

    # ------------------------------------------------------------ 媒体文件
    # 说明：下载器常用 HEAD 探测 + Range 续传，因此显式放行 HEAD
    @app.api_route("/m/{token}.mp4", methods=["GET", "HEAD"])
    async def media(token: str, request: Request) -> Any:
        store = get_store()
        row = store.execute_row(
            "SELECT * FROM tasks WHERE delivery_token=? AND video_path IS NOT NULL", (token,))
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        path = Path(cfg["storage"]["media_dir"]) / row["video_path"]
        if not path.is_file():
            raise HTTPException(status_code=404, detail="file missing")
        return FileResponse(
            path, media_type="video/mp4",
            headers={"Accept-Ranges": "bytes",
                     "Content-Disposition":
                         f'attachment; filename="{row["id"]}.mp4"'},
        )

    @app.api_route("/media/{task_id}/thumb.jpg", methods=["GET", "HEAD"])
    async def thumb(task_id: int) -> Any:
        path = Path(cfg["storage"]["media_dir"]) / f"t{task_id}" / "thumb.jpg"
        if not path.is_file():
            return JSONResponse({"error": "no thumb"}, status_code=404)
        return FileResponse(path, media_type="image/jpeg")

    # ------------------------------------------------------------ 家长看板
    @app.get("/", response_class=HTMLResponse)
    async def board_html() -> str:
        html_path = Path(__file__).resolve().parent.parent / "web" / "index.html"
        if html_path.is_file():
            return html_path.read_text(encoding="utf-8")
        return "<h1>WatchTube</h1><p>看板页面缺失</p>"

    @app.get("/api/v1/board")
    async def board() -> dict[str, Any]:
        store = get_store()
        rows = store.list_recent(80)
        items = []
        for r in rows:
            items.append({
                "id": r["id"], "created_at": r["created_at"], "sender": r["sender"],
                "raw_text": r["raw_text"], "query": r.get("query"),
                "reply_text": r.get("reply_text"), "state": r["state"],
                "source_title": r.get("source_title"),
                "source_author": r.get("source_author"),
                "source_url": r.get("source_url"),
                "cover_url": r.get("cover_url"),
                "video_bytes": r.get("video_bytes"),
                "video_duration": r.get("video_duration"),
                "thumb": f"/media/{r['id']}/thumb.jpg" if r.get("video_path") else None,
                "video_url": (f"/m/{r['delivery_token']}.mp4"
                              if r.get("delivery_token") else None),
                "needs_review": bool(r.get("needs_review")),
                "error": r.get("error"), "note": r.get("decision_note"),
                "candidates": r.get("candidates"),
            })
        return {"ok": True, "stats": store.stats(), "items": items,
                "moderation": (cfg.get("moderation") or {}).get("mode"),
                "understanding": "llm" if get_pipeline().brain.enabled else "rule"}

    @app.get("/api/v1/tasks/{task_id}/events")
    async def task_events(task_id: int) -> dict[str, Any]:
        return {"ok": True, "events": get_store().events(task_id)}

    @app.post("/api/v1/tasks/{task_id}/approve")
    async def approve(task_id: int) -> dict[str, Any]:
        store, pipe = get_store(), get_pipeline()
        task = store.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        if task["state"] != "review_pending":
            raise HTTPException(status_code=409, detail=f"当前状态 {task['state']} 不可批准")
        store.set_state(task_id, "understood")
        pick = {
            "bvid": task["source_id"], "title": task["source_title"],
            "author": task["source_author"], "url": task["source_url"],
            "pic": task["cover_url"], "duration": task.get("source_duration") or 0,
        }
        get_pipeline().executor.submit(pipe._safe_produce, task_id, pick)
        return {"ok": True}

    @app.post("/api/v1/tasks/{task_id}/reject")
    async def reject(task_id: int, reason: str = "") -> dict[str, Any]:
        get_store().reject(task_id, reason)
        return {"ok": True}

    @app.post("/api/v1/tasks/{task_id}/retry")
    async def retry(task_id: int) -> dict[str, Any]:
        store, pipe = get_store(), get_pipeline()
        task = store.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        if task["state"] not in ("failed", "rejected"):
            raise HTTPException(status_code=409, detail="该任务无需重试")
        if not task.get("query"):
            raise HTTPException(status_code=409, detail="缺少搜索词，无法重试")
        store.update(task_id, state="understood", error=None)
        get_pipeline().executor.submit(pipe._safe_run, task_id)
        return {"ok": True}

    # 家长在本地直接给手表发一条（调试用）
    @app.post("/api/v1/dev/message")
    async def dev_message(payload: IngestIn, _=Depends(verify_key)) -> dict[str, Any]:
        return await ingest(payload)  # type: ignore[return-value]

    return app

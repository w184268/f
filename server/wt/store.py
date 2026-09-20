# -*- coding: utf-8 -*-
"""SQLite 存储：任务状态机 + 幂等去重。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    device_id       TEXT,
    sender          TEXT,
    raw_text        TEXT,
    msg_fingerprint TEXT,
    is_request      INTEGER,          -- 是否判定为"求视频"请求
    query           TEXT,             -- 提炼后的搜索词
    reply_text      TEXT,             -- 随视频一起发给孩子的说明文字
    decision_note   TEXT,             -- 大模型给出的判断理由
    state           TEXT NOT NULL,    -- 见 STATE_ORDER
    source_id       TEXT,
    source_title    TEXT,
    source_author   TEXT,
    source_url      TEXT,
    source_duration INTEGER,
    cover_url       TEXT,
    candidates      TEXT,             -- 候选列表 JSON
    video_path      TEXT,
    video_bytes     INTEGER,
    video_duration  REAL,
    attempt         INTEGER DEFAULT 0,
    error           TEXT,
    delivered_at    REAL,
    needs_review    INTEGER DEFAULT 0,
    rejected_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_state  ON tasks(state);
CREATE INDEX IF NOT EXISTS idx_tasks_fp     ON tasks(msg_fingerprint);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_fp_created ON tasks(msg_fingerprint, created_at);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER,
    ts         REAL,
    level      TEXT,
    message    TEXT
);

CREATE TABLE IF NOT EXISTS quota (
    day        TEXT PRIMARY KEY,
    used       INTEGER DEFAULT 0
);
"""

STATES = (
    "received", "understood", "no_need", "searching",
    "downloading", "transcoding", "review_pending", "ready",
    "delivering", "delivered", "failed", "rejected",
)


class Store:
    def __init__(self, db_path: str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    # 后期新增的列：老库自动补齐，不影响既有数据
    _EXTRA_COLUMNS = {"delivery_token": "TEXT", "thumb": "TEXT", "media_kind": "TEXT"}

    def _migrate(self) -> None:
        existing = {r["name"] for r in self._conn.execute("PRAGMA table_info(tasks)")}
        for col, decl in self._EXTRA_COLUMNS.items():
            if col not in existing:
                self._conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {decl}")

    @contextmanager
    def _tx(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ---------------- tasks ----------------
    def create_task(self, *, device_id: str, sender: str, raw_text: str,
                    fingerprint: str) -> int:
        now = time.time()
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO tasks (created_at, updated_at, device_id, sender,"
                " raw_text, msg_fingerprint, state) VALUES (?,?,?,?,?,?,?)",
                (now, now, device_id, sender, raw_text, fingerprint, "received"),
            )
            tid = int(cur.lastrowid)
        self.log(tid, "info", f"收到消息：{raw_text[:120]}")
        return tid

    def touch(self, task_id: int) -> None:
        with self._tx() as c:
            c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (time.time(), task_id))

    def set_state(self, task_id: int, state: str, *, error: str | None = None) -> None:
        assert state in STATES, f"未知状态: {state}"
        with self._tx() as c:
            c.execute(
                "UPDATE tasks SET state=?, updated_at=?, error=COALESCE(?, error) WHERE id=?",
                (state, time.time(), error, task_id),
            )

    def update(self, task_id: int, **fields: Any) -> None:
        if not fields:
            return
        # list / dict 自动序列化，调用方不必关心存储细节
        fields = {k: dumps(v) if isinstance(v, (list, dict)) else v
                  for k, v in fields.items()}
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._tx() as c:
            c.execute(f"UPDATE tasks SET {cols} WHERE id=?",
                      (*fields.values(), task_id))

    def get(self, task_id: int) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def execute_row(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        """按自定义 SQL 取单行（文件下载这类带 token 的查询用）。"""
        row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def recent_fingerprint_exists(self, fingerprint: str, window: float) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM tasks WHERE msg_fingerprint=? AND created_at>=? LIMIT 1",
            (fingerprint, time.time() - window),
        ).fetchone()
        return row is not None

    def list_ready(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE state='ready' ORDER BY id ASC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def approve(self, task_id: int) -> None:
        self.log(task_id, "info", "家长批准下发")
        self.update(task_id, state="ready", needs_review=0)

    def reject(self, task_id: int, reason: str = "") -> None:
        self.log(task_id, "warn", f"家长拒绝：{reason}")
        self.update(task_id, state="rejected", rejected_reason=reason)

    def mark_delivering(self, task_id: int) -> None:
        self.update(task_id, state="delivering")

    def mark_delivered(self, task_id: int) -> None:
        self.update(task_id, state="delivered", delivered_at=time.time())

    def bump_attempt(self, task_id: int) -> int:
        with self._tx() as c:
            c.execute("UPDATE tasks SET attempt=attempt+1, updated_at=? WHERE id=?",
                      (time.time(), task_id))
            row = c.execute("SELECT attempt FROM tasks WHERE id=?", (task_id,)).fetchone()
        return int(row["attempt"]) if row else 0

    # ---------------- events ----------------
    def log(self, task_id: int | None, level: str, message: str) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO events (task_id, ts, level, message) VALUES (?,?,?,?)",
                (task_id, time.time(), level, message[:500]),
            )

    def events(self, task_id: int, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE task_id=? ORDER BY ts DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------------- quota ----------------
    def quota_left(self, daily_limit: int) -> int:
        day = time.strftime("%Y-%m-%d")
        row = self._conn.execute("SELECT used FROM quota WHERE day=?", (day,)).fetchone()
        used = int(row["used"]) if row else 0
        return max(0, daily_limit - used)

    def quota_consume(self) -> None:
        day = time.strftime("%Y-%m-%d")
        with self._tx() as c:
            c.execute(
                "INSERT INTO quota (day, used) VALUES (?,1) "
                "ON CONFLICT(day) DO UPDATE SET used=used+1",
                (day,),
            )

    def stats(self) -> dict[str, Any]:
        day = time.strftime("%Y-%m-%d")
        row = self._conn.execute(
            "SELECT SUM(CASE WHEN state='delivered' AND delivered_at>=? THEN 1 ELSE 0 END) AS today,"
            " COUNT(*) AS total FROM tasks",
            (time.time() - 86400,),
        ).fetchone()
        return {"today_delivered": row["today"] or 0, "total": row["total"] or 0, "day": day}


def fingerprint(sender: str, text: str) -> str:
    """消息指纹：用于短时间内的重复消息去重。"""
    import hashlib

    norm = "".join(ch for ch in text if not ch.isspace())
    return hashlib.sha1(f"{sender}|{norm}".encode("utf-8")).hexdigest()[:16]


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def loads(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None

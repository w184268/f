# -*- coding: utf-8 -*-
"""配置加载：YAML 文件 + 环境变量覆盖。

环境变量覆盖规则：
    WT_LLM__API_KEY=sk-xxx   ->  cfg.llm.api_key
    WT_SERVER__PORT=9000     ->  cfg.server.port
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_FILENAMES = ("config.yaml", "config.yml", "config.example.yaml")


class Section(dict):
    """支持属性访问的字典，嵌套时自动包装。"""

    def __getattr__(self, name: str) -> Any:
        try:
            value = self[name]
        except KeyError:
            return None
        if isinstance(value, dict) and not isinstance(value, Section):
            value = Section(value)
            self[name] = value
        return value

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value

    def get_path(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return default


def _coerce(raw: str) -> Any:
    """把环境变量字符串转成合理的 Python 类型。"""
    low = raw.strip().lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "none", "~"):
        return None
    if "," in raw and all(_looks_number(p) for p in raw.split(",") if p.strip()):
        return [int(p) if p.strip().lstrip("-").isdigit() else p for p in raw.split(",")]
    return raw


def _looks_number(s: str) -> bool:
    s = s.strip()
    return s.lstrip("-").replace(".", "", 1).isdigit()


def _apply_env(cfg: Section) -> None:
    """扫描 WT_* 环境变量，按 __ 分隔写入嵌套配置。"""
    prefix = "WT_"
    for env_key, raw in os.environ.items():
        if not env_key.startswith(prefix) or raw == "":
            continue
        parts = [p.lower() for p in env_key[len(prefix):].split("__") if p]
        if not parts:
            continue
        cur = cfg
        for p in parts[:-1]:
            nxt = cur.get(p)
            if not isinstance(nxt, dict):
                nxt = Section()
                cur[p] = nxt
            cur = nxt
        cur[parts[-1]] = _coerce(raw)


def load_config(path: str | os.PathLike | None = None) -> Section:
    """加载配置。优先使用显式路径，否则在项目目录/当前目录下找默认文件名。"""
    base = Path(__file__).resolve().parent.parent
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    else:
        for name in DEFAULT_FILENAMES:
            candidates.append(Path.cwd() / name)
            candidates.append(base / name)

    raw: dict[str, Any] = {}
    used: Path | None = None
    for cand in candidates:
        if cand.is_file():
            with cand.open("r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
            used = cand
            break

    cfg = Section(raw)
    _apply_env(cfg)
    cfg["_config_path"] = str(used) if used else None

    # 关键默认值兜底，避免用户删配置项后代码炸掉
    cfg.setdefault("server", Section())
    cfg["server"].setdefault("host", "0.0.0.0")
    cfg["server"].setdefault("port", 8787)
    cfg["server"].setdefault("bridge_key", "change-me-please")
    cfg["server"].setdefault("public_base_url", "")
    cfg.setdefault("storage", Section())
    cfg["storage"].setdefault("db_path", "data/watchtube.db")
    cfg["storage"].setdefault("media_dir", "data/media")

    # 相对路径统一解析到项目根目录
    for key in ("db_path", "media_dir"):
        value = cfg["storage"].get(key)
        if value and not Path(value).is_absolute():
            cfg["storage"][key] = str(base / value)

    Path(cfg["storage"]["media_dir"]).mkdir(parents=True, exist_ok=True)
    Path(cfg["storage"]["db_path"]).parent.mkdir(parents=True, exist_ok=True)
    return cfg

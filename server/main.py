# -*- coding: utf-8 -*-
"""启动入口： python main.py  或  uvicorn wt.api:factory"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from wt.api import build_app  # noqa: E402
from wt.config import load_config  # noqa: E402
from wt.util import setup_logging  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="WatchTube 服务端")
    parser.add_argument("-c", "--config", default=None, help="配置文件路径")
    parser.add_argument("--host", default=None, help="监听地址，覆盖配置文件")
    parser.add_argument("--port", type=int, default=None, help="监听端口，覆盖配置文件")
    parser.add_argument("--reload", action="store_true", help="开发模式，代码改动自动重启")
    args = parser.parse_args()

    cfg = load_config(args.config)
    setup_logging("INFO")

    app = build_app(args.config)
    host = args.host or cfg["server"]["host"]
    port = int(args.port or cfg["server"]["port"])

    print("=" * 58)
    print("  WatchTube 已启动")
    print(f"  本机地址      http://127.0.0.1:{port}")
    base = cfg["server"].get("public_base_url")
    if base:
        print(f"  手机访问地址  {base}")
    print(f"  理解引擎      {_engine_desc(cfg)}")
    print(f"  审核模式      {(cfg.get('moderation') or {}).get('mode')}")
    print("=" * 58)

    uvicorn.run(app, host=host, port=port, reload=args.reload)


def _engine_desc(cfg) -> str:
    u = cfg.get("understanding") or {}
    return "大模型" if u.get("enabled") and u.get("api_key") else "规则引擎"


if __name__ == "__main__":
    main()

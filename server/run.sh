#!/usr/bin/env bash
# 启动 WatchTube 服务端
set -e
cd "$(dirname "$0")"

if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
  echo "已生成 config.yaml，请先按需修改后再运行。"
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "需要 python3"; exit 1
fi

if ! python3 -c "import fastapi" 2>/dev/null; then
  echo "安装依赖…"
  python3 -m pip install -r requirements.txt
fi

exec python3 main.py "$@"

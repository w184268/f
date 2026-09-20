#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境自检：跑一遍就知道还差什么，并把建议在 config.yaml 里填的地址直接算好。

    python3 tools/check_env.py
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

OK, BAD, WARN = "✅", "❌", "⚠️"


def run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, ""
    except Exception as exc:
        return 1, str(exc)


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("223.5.5.5", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def check_port(port: int) -> tuple[bool, str]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return (False, "端口可被占用（已有服务在跑？）") if s.connect_ex(("127.0.0.1", port)) == 0 \
            else (True, "端口空闲")


def h264_encoders(ffmpeg: str) -> list[str]:
    code, out = run([ffmpeg, "-hide_banner", "-encoders"])
    if code != 0:
        return []
    found = []
    for name in ("libx264", "libopenh264", "h264_nvenc", "h264_qsv", "h264_vulkan", "mpeg4"):
        if f" {name} " in out:
            found.append(name)
    return found


def main() -> int:
    print("=" * 60)
    print("  WatchTube 环境自检")
    print("=" * 60)

    problems = 0

    # 1. Python
    print("\n【运行环境】")
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 10)
    print(f"  {OK if ok else BAD} Python {major}.{minor}.{sys.version_info[2]}"
          + ("" if ok else "  需要 3.10 以上"))
    problems += 0 if ok else 1

    # 2. ffmpeg / ffprobe
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        code, out = run([ffmpeg, "-version"])
        ver = out.split("\n")[0][:52] if out else "未知版本"
        print(f"  {OK} ffmpeg：{ver}")
    else:
        print(f"  {BAD} 没找到 ffmpeg —— 视频压制必须用到它")
        print("        Windows: gyan.dev/ffmpeg/builds 下载后把 bin 加进 PATH")
        print("        Mac:     brew install ffmpeg")
        print("        Ubuntu:  sudo apt install ffmpeg")
        problems += 1

    ffprobe = shutil.which("ffprobe")
    print(f"  {OK if ffprobe else WARN} ffprobe（探测视频信息用）"
          + ("" if ffprobe else "  通常随 ffmpeg 一起安装"))

    # 3. H.264 编码器
    if ffmpeg:
        encs = h264_encoders(ffmpeg)
        if encs:
            best = encs[0]
            note = "（画质最好）" if best == "libx264" else "（可用，画质略逊）"
            print(f"  {OK} 可用视频编码器：{', '.join(encs)}{note}")
        else:
            print(f"  {BAD} 你的 ffmpeg 没有任何 H.264 编码器，压不了视频")
            print("        建议换用 gyan.dev 的完整版 ffmpeg")
            problems += 1

    # 4. Python 依赖
    print("\n【Python 依赖】")
    for mod, pkg in (("fastapi", "fastapi"), ("uvicorn", "uvicorn"),
                     ("requests", "requests"), ("yaml", "pyyaml")):
        try:
            __import__(mod)
            print(f"  {OK} {pkg}")
        except ImportError:
            print(f"  {BAD} {pkg} 没装")
            problems += 1
    if problems:
        print("        一次搞定：pip install -r server/requirements.txt")

    # 5. 配置文件
    print("\n【配置文件】")
    cfg = ROOT / "server" / "config.yaml"
    example = ROOT / "server" / "config.example.yaml"
    port = 8787
    if cfg.is_file():
        print(f"  {OK} 已找到 server/config.yaml")
        try:
            from wt.config import load_config
            c = load_config(str(cfg))
            port = int(c["server"].get("port") or 8787)
            base = str(c["server"].get("public_base_url") or "")
            key = str(c["server"].get("bridge_key") or "")
            print(f"  {OK if 'change-me' not in key else WARN} bridge_key："
                  f"{'还是默认值，建议改掉' if 'change-me' in key else '已自定义'}")
            if base:
                print(f"  {OK} 手机将访问：{base}")
            else:
                print(f"  {WARN} public_base_url 还没填")
            try:
                m = c["moderation"]
                v = c["video"]
                print(f"  · 审核模式 {m['mode']} · 视频上限 {v['max_duration']} 秒 "
                      f"· 体积上限 {v['max_size_mb']}MB")
            except Exception:
                pass
        except Exception as exc:
            print(f"  {WARN} 配置读取异常：{exc}")
    elif example.is_file():
        print(f"  {WARN} 还没创建 config.yaml")
        print(f"        执行：cp server/config.example.yaml server/config.yaml")
    else:
        print(f"  {BAD} 连 config.example.yaml 都找不到，项目文件不完整")
        problems += 1

    # 6. 网络
    print("\n【网络】")
    ip = local_ip()
    print(f"  {OK} 本机局域网 IP：{ip}")
    print(f"  · 手机端要填的地址：http://{ip}:{port}")
    free, msg = check_port(port)
    print(f"  {OK if free else WARN} 端口 {port}：{msg}")

    # 7. B站连通性：走模块里那套带 UA/Referer 的会话，最接近真实链路
    try:
        from wt.bili import Bilibili
        tmp = ROOT / "server" / ".runtime"
        tmp.mkdir(exist_ok=True)
        b = Bilibili(timeout=12, retries=1, cache_path=tmp / "wbi.json")
        hits = b.search("恐龙", limit=3)
        print(f"  {OK} B站可达，搜索「恐龙」命中 {len(hits)} 条")
        for h in hits[:2]:
            print(f"  · {h.get('title', '')[:30]}  ⏱{h.get('duration')}s  ▶{h.get('play')}")
    except Exception as exc:
        print(f"  {WARN} B站接口异常：{type(exc).__name__}: {exc}")
        print("        服务端这台机器必须能联网；只出内网会导致搜不到内容")

    print("\n" + "=" * 60)
    if problems == 0:
        print("  🎉 环境齐了。下一步：")
        print("     1) 给手机推送 APK（方法见 使用指南.html）")
        print(f"     2) App 里填 http://{ip}:{port} 和你的 bridge_key")
        print("     3) 先不想装 App 也行，跑这条命令就能验证服务端：")
        print("        python3 tools/selftest.py \"恐龙为什么会灭绝啊\"")
    else:
        print(f"  ⚠️  还有 {problems} 项需要处理，按上面的提示补即可。")
    print("=" * 60)
    return problems


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端自测：不需要手机，直接模拟整条链路。

    python tools/selftest.py "恐龙为什么灭绝了"

会依次验证：
    理解 → B站检索 → 安全打分 → 取流 → 下载 → 转码 → 出片 → outbox → 下载文件
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from wt.config import load_config          # noqa: E402
from wt.pipeline import Pipeline           # noqa: E402
from wt.store import Store                 # noqa: E402

BAR = "─" * 62


def fmt_step(name: str, ok: bool, detail: str = "") -> None:
    mark = "✅" if ok else "❌"
    print(f"  {mark} {name}" + (f"  {detail}" if detail else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="WatchTube 端到端自测")
    ap.add_argument("text", nargs="?", default="恐龙为什么灭绝了",
                    help="模拟孩子发来的消息")
    ap.add_argument("-c", "--config", default=None)
    ap.add_argument("--wait", type=float, default=180, help="最长等待秒数")
    args = ap.parse_args()

    cfg = load_config(args.config)
    store = Store(cfg["storage"]["db_path"])
    pipe = Pipeline(cfg, store)

    print(BAR)
    print(f"  模拟孩子发来：{args.text}")
    print(BAR)

    # 1) 理解
    from wt.brain import Brain
    brain = Brain(cfg)
    print(f"  理解引擎：{'大模型 ' + str(brain.model) if brain.enabled else '本地规则'}")
    und = brain.understand(args.text)
    fmt_step("意图识别", True, f"is_request={und.is_request} query={und.query!r}")
    print(f"     回复文案：{und.reply}")

    if not und.is_request:
        print("\n  该消息未被判定为内容请求（正常 behaviour），自测结束。")
        return 0

    # 2) 提交走完整管线
    res = pipe.submit(device_id="selftest", sender="孩子", raw_text=args.text)
    task_id = res["task_id"]
    print(f"\n  任务号 #{task_id}，后台处理中…\n")

    deadline = time.time() + args.wait
    last_state = ""
    while time.time() < deadline:
        t = store.get(task_id)
        st = t["state"]
        if st != last_state:
            print(f"     [{time.strftime('%H:%M:%S')}] {st}")
            last_state = st
        if st in ("ready", "delivered", "failed", "rejected", "review_pending"):
            break
        time.sleep(1)

    t = store.get(task_id)
    print()
    if t["state"] == "review_pending":
        fmt_step("进入待审核队列", True, "当前配置了家长审核模式")
        print(f"     候选：《{t['source_title']}》 {t['source_url']}")
        print("     在看板上点「同意下发」才会出片。")
        return 0

    if t["state"] != "ready":
        fmt_step("管线执行", False, f"状态={t['state']} error={t.get('error')}")
        for e in store.events(task_id, 8):
            print(f"       · {e['message']}")
        return 1

    fmt_step("检索 → 选片", True,
             f"《{t['source_title']}》 by {t['source_author']}")

    vpath = Path(cfg["storage"]["media_dir"]) / t["video_path"]
    fmt_step("压制成片", vpath.is_file(),
             f"{t['video_bytes']//1024}KB / {t['video_duration']:.0f}s")

    # 3) 模拟手机端 outbox
    item = pipe.build_outbox_item(t)
    print(f"\n{BAR}\n  手机端拉取到的下发项\n{BAR}")
    for k, v in item.items():
        print(f"     {k:14} {v}")

    if item["kind"] == "video" and t["video_duration"]:
        print(f"\n  压缩比： 原始 {t['source_duration']}s → 成片 {t['video_duration']:.0f}s"
              f"（限制 {cfg['video']['max_duration']}s）")

    # 4) 校验文件确实能播
    print()
    info = pipe.media.probe(vpath)
    playable = info["duration"] > 0 and info["vcodec"] in ("h264", "mpeg4")
    fmt_step("成片可播放", playable,
             f"{info['width']}x{info['height']} {info['vcodec']} {info['size']//1024}KB")

    for e in store.events(task_id, 12)[::-1]:
        print(f"     · {e['message']}")

    print(f"\n{BAR}")
    print("  🎉 全链路跑通。接下来只要在手机上装好桥接 App，" )
    print("     孩子发一条消息就能收到视频了。")
    print(BAR)
    return 0 if playable else 1


if __name__ == "__main__":
    raise SystemExit(main())

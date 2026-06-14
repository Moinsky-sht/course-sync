#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_speakers_via_browser.py — 用 mavis browser 抓取飞书妙记详情页的发言人数量

为什么不直接用 lark-cli？
  飞书妙记 OpenAPI 没有任何 endpoint 返回参与人数。详情页面的"发言人 (N)"
  标签是前端组件从会议关联数据中动态渲染的，必须通过浏览器抓 DOM 拿到。

为什么不直接用 Playwright headless？
  飞书 SPA 详情路由对 SSO 鉴权严格，headless 浏览器的 session cookies
  在访问详情页时会被踢回登录页。

为什么用 mavis browser？
  mavis browser 是 mavis daemon 提供的浏览器自动化，它通过 native messaging
  连到你真实的 Chrome/Edge 浏览器（带登录态），所以可以直接访问 SPA 详情页
  并抓取"发言人 (N)"。

前置:
  1. 安装 mavis browser: mavis browser install
  2. 在 Chrome 中加载扩展: chrome://extensions/ → 加载已解压 → 选 /Users/<you>/.mavis/browser-extension
  3. 扩展 ID 必须匹配: ppnnfacnjgokfmbngkgbdgiigpbfgdba
  4. 浏览器必须登录飞书 (用户态)

用法:
  python3 fetch_speakers_via_browser.py <token1> [<token2> ...]
  python3 fetch_speakers_via_browser.py --from-candidates /tmp/minutes_candidates.json

输出:
  /tmp/speaker_counts.json — {results: [{token, raw_name, speaker_count}, ...]}

环境变量（可选）:
  COURSE_SYNC_BROWSER_TOOL — 浏览器自动化 CLI（默认 mavis browser tool）
  COURSE_SYNC_HOST         — 妙记域名（默认 wlbyzcky.feishu.cn）
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

BROWSER_TOOL = os.environ.get("COURSE_SYNC_BROWSER_TOOL", "mavis browser tool")
HOST = os.environ.get("COURSE_SYNC_HOST", "wlbyzcky.feishu.cn")
CANDIDATES_FILE = "/tmp/minutes_candidates.json"
OUTPUT_FILE = "/tmp/speaker_counts.json"


def mavis_browser(tool, args):
    """Call mavis browser tool, return parsed JSON or raw dict."""
    cmd = BROWSER_TOOL.split() + [tool, json.dumps(args)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    out = (r.stdout or "").strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"_raw_stdout": out, "_raw_stderr": (r.stderr or "").strip()}


def check_browser_bridge():
    """Verify mavis browser is installed and connected."""
    r = subprocess.run(["mavis", "browser", "status"],
                       capture_output=True, text=True, timeout=10)
    if "connected" not in r.stdout:
        log("❌ mavis browser 未连接，请先:")
        log("   1. mavis browser install")
        log("   2. 在 Chrome 中加载 /Users/<you>/.mavis/browser-extension 扩展")
        return False
    log("✅ mavis browser bridge 已连接")
    return True


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def get_participant_count(token):
    """Navigate to a minute detail page and extract the participant count.

    飞书妙记详情页有两个数字概念:
    - 发言人 (N) — 主动说话的人（DOM 中可读出，但只是参与过发言的子集）
    - 参与人数 (N) — 完整参会人数（包括只听不发言的人）

    参与人数的 DOM 结构:
      <div class="ud__avatar-group-stacked">
        <span class="ud__avatar">×K (具体头像)</span>
        <span class="ud__avatar-neutral">
          <span class="ud__avatar__text">+M</span> (溢出)
        </span>
      </div>
    总参与人数 = K + M

    Returns int or None on failure.
    """
    url = f"https://{HOST}/minutes/{token}"
    nav = mavis_browser("navigate", {"url": url})
    if not nav.get("ok", True) and "_raw_stderr" in nav:
        log(f"  navigate failed: {nav.get('_raw_stderr', '')[:200]}")
        return None

    time.sleep(5)  # let SPA render (head header is last to populate)

    # 1) 主方案: 用 .meeting-info / [class*=meeting-info] / [class*=participant] 找
    #    飞书妙记 header 区域用这个 class 渲染参与人 (avatar group)
    #    class="ud__avatar-group-stacked" 包含 K 个头像 + 一个 ud__avatar-neutral
    out = mavis_browser("query", {
        "selector": ".meeting-info, [class*=meeting-info], [class*=participant]",
        "what": "exists"
    })
    exists_text = out.get("content", "") if isinstance(out, dict) else ""
    # "+4" 形式（来自 ud__avatar-neutral 里的 +N 文本溢出）
    plus_match = re.search(r"\+(\d+)", exists_text)
    plus_n = int(plus_match.group(1)) if plus_match else 0

    # 2) 抓页面里"发言人 (K)" 文本 — 这 K 是 avatar 数
    out = mavis_browser("query", {"selector": "text=发言人", "what": "text"})
    text = out.get("content", "") if isinstance(out, dict) else ""
    sp_match = re.search(r"发言人 \((\d+)\)", text)
    speaker_n = int(sp_match.group(1)) if sp_match else 0

    if speaker_n == 0 and plus_n == 0:
        log(f"  no participants detected (selector probe: {exists_text!r})")
        return None

    return speaker_n + plus_n


def load_candidates(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("candidates", [])


def main():
    p = argparse.ArgumentParser(description="Fetch speaker counts via mavis browser")
    p.add_argument("tokens", nargs="*", help="Minute tokens to fetch")
    p.add_argument("--from-candidates", metavar="PATH",
                   help="Read tokens from /tmp/minutes_candidates.json (or any path)")
    p.add_argument("--output", default=OUTPUT_FILE,
                   help=f"Output JSON path (default {OUTPUT_FILE})")
    args = p.parse_args()

    if not check_browser_bridge():
        sys.exit(1)

    if args.from_candidates:
        candidates = load_candidates(args.from_candidates)
        targets = [(c["token"], c.get("raw_name", "?")) for c in candidates]
    elif args.tokens:
        targets = [(t, "?") for t in args.tokens]
    else:
        log(f"❌ 至少给一个 token 或 --from-candidates")
        sys.exit(2)

    log(f"📋 共 {len(targets)} 个目标需要抓取")
    results = []
    for tok, name in targets:
        log(f"[{tok}] {name}")
        n = get_participant_count(tok)
        log(f"  参与人数 = {n}")
        results.append({"token": tok, "raw_name": name, "participant_count": n})

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"results": results,
                   "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                  f, ensure_ascii=False, indent=2)
    log(f"\n✅ saved {args.output} ({len(results)} entries)")


if __name__ == "__main__":
    main()

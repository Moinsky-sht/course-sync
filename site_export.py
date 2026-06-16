#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
site_export.py — 从飞书 Base 导出课程数据，给个人网站消费

输出 (默认写到 /tmp/site_export/):
  - courses.json       — 完整结构化数据 (含元数据、参与人数、链接)
  - courses.md         — Markdown 表格，便于嵌入静态网站
  - courses_index.json — 简化版（按年份/类别分组的 index）
  - courses_latest.json — 仅最新一周的课程

环境变量:
  COURSE_SYNC_BASE_TOKEN  — 飞书 Base app_token
  COURSE_SYNC_TABLE_ID    — Base 表 ID
  COURSE_SYNC_OUTPUT_DIR  — 输出目录（默认 /tmp/site_export）
  COURSE_SYNC_SITE_SYNC_URL — 网站课程同步接口（默认 https://wlbycuc.cn/api/integrations/courses/sync）
  COURSE_SYNC_SITE_SYNC_TOKEN — 网站同步接口 Token

用法:
  python3 site_export.py
  python3 site_export.py --days 30 --output ./public/courses
  python3 site_export.py --require-site-sync

如果你的网站是 Hugo/Jekyll/Next.js 等静态站，可以直接拿 courses.json 喂进去。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone


BASE_TOKEN = os.environ.get("COURSE_SYNC_BASE_TOKEN", "PK5BbGQx4aoeres9oBCchWKPnfd")
TABLE_ID = os.environ.get("COURSE_SYNC_TABLE_ID", "tblWTN8jkeExIFa0")
OUTPUT_DIR = os.environ.get("COURSE_SYNC_OUTPUT_DIR", "/tmp/site_export")
LARK_CLI_BIN = os.environ.get("LARK_CLI_BIN", "lark-cli")
DEFAULT_SITE_SYNC_URL = "https://wlbycuc.cn/api/integrations/courses/sync"
SITE_SYNC_URL = os.environ.get("COURSE_SYNC_SITE_SYNC_URL") or os.environ.get("COURSE_SYNC_SITE_PUSH_URL", DEFAULT_SITE_SYNC_URL)
SITE_SYNC_TOKEN = os.environ.get("COURSE_SYNC_SITE_SYNC_TOKEN") or os.environ.get("COURSE_SYNC_SITE_PUSH_TOKEN", "")

GENERIC_HOSTS = {"", "天天", "天"}
HOST_ALIAS_MAP = {
    "luu": "霍雨露",
    "luu🦌": "霍雨露",
    "luu鹿": "霍雨露",
}
DEFAULT_EXCLUDED_TOKENS = "obcn3t4uyv3egq867ympj886"
EXCLUDED_TOKENS = {
    token.strip()
    for token in os.environ.get("COURSE_SYNC_EXCLUDED_TOKENS", DEFAULT_EXCLUDED_TOKENS).split(",")
    if token.strip()
}


def normalized_course_host(name, category, year, host):
    """Apply only stable host fixes during site export.

    Host teachers vary by cohort and subject. Do not use broad year/category
    mappings here; those must come from transcript/VC evidence in the sync step.
    """
    name = str(name or "")
    category = str(category or "")
    year = str(year or "")
    host = str(host or "").strip()
    host = HOST_ALIAS_MAP.get(host.lower().replace(" ", ""), host)

    if host and host not in GENERIC_HOSTS:
        return host

    if category == "世界文明史" or "文明史" in name:
        return "泠泠七"

    return host


def lark_cli(*args):
    cmd = [LARK_CLI_BIN] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"_raw": r.stdout, "_err": r.stderr}


def fetch_all_records():
    """Paginate through all Base records."""
    records = []
    offset = 0
    limit = 500
    while True:
        r = lark_cli("base", "+record-list",
                     "--base-token", BASE_TOKEN,
                     "--table-id", TABLE_ID,
                     "--limit", str(limit),
                     "--offset", str(offset),
                     "--format", "json",
                     "--as", "user")
        if not r.get("ok"):
            print(f"❌ Base fetch failed: {r}", file=sys.stderr)
            break
        data = r.get("data", {})
        fields = data.get("fields", [])
        rows = data.get("data", [])
        rec_ids = data.get("record_id_list", [])
        for i, row in enumerate(rows):
            records.append({"rec_id": rec_ids[i] if i < len(rec_ids) else None,
                            "fields": dict(zip(fields, row))})
        if not data.get("has_more") or not rows:
            break
        offset += len(rows)
    return records, fields


def normalize_course(rec):
    """Convert Base row to clean course dict for site consumption."""
    f = rec["fields"]
    name = f.get("课程名称", "")
    if not name:
        return None
    # Parse markdown link to extract raw URL
    link = f.get("妙记链接", "")
    if isinstance(link, str):
        m = re.search(r"\(([^)]+)\)", link)
        url = m.group(1) if m else link
    elif isinstance(link, list) and link:
        url = link[0].get("link", "") if isinstance(link[0], dict) else str(link[0])
    else:
        url = ""

    # Extract token from URL
    token_m = re.search(r"minutes/([a-z0-9]+)", url)
    token = token_m.group(1) if token_m else ""
    if token in EXCLUDED_TOKENS:
        return None

    # 课程年份 list → string
    year = ""
    if isinstance(f.get("课程年份"), list) and f["课程年份"]:
        year = f["课程年份"][0]
    elif isinstance(f.get("课程年份"), str):
        year = f["课程年份"]

    # 重要程度
    importance = ""
    if isinstance(f.get("重要程度"), list) and f["重要程度"]:
        importance = f["重要程度"][0]

    # 课程形式
    form = ""
    if isinstance(f.get("课程形式"), list) and f["课程形式"]:
        form = f["课程形式"][0]

    # 课程类别
    category = ""
    if isinstance(f.get("课程类别"), list) and f["课程类别"]:
        category = f["课程类别"][0]

    # 主讲人：优先使用文本显示名，人员字段保留给 Base 协作。
    host = ""
    if isinstance(f.get("主讲显示名"), str) and f.get("主讲显示名").strip():
        host = f.get("主讲显示名").strip()
    elif isinstance(f.get("主讲人/主持人"), list) and f["主讲人/主持人"]:
        first = f["主讲人/主持人"][0]
        if isinstance(first, dict):
            host = first.get("name", "")
    elif isinstance(f.get("主讲人/主持人"), str):
        host = f["主讲人/主持人"]
    host = normalized_course_host(name, category, year, host)

    return {
        "id": rec["rec_id"],
        "name": name,
        "participant_count": f.get("参与人数") or 0,
        "importance": importance,
        "date": str(f.get("上课日期", ""))[:10] if f.get("上课日期") else "",
        "form": form,
        "category": category,
        "duration": f.get("会议时长", ""),
        "host": host,
        "url": url,
        "token": token,
        "year": year,
    }


def render_markdown_table(courses):
    """Render courses as Markdown table."""
    lines = [
        "| 日期 | 课程名称 | 类别 | 年份 | 参与人数 | 重要程度 | 主讲 | 时长 | 链接 |",
        "|------|---------|------|------|---------|---------|------|------|------|",
    ]
    for c in courses:
        date = c["date"] or "-"
        name = (c["name"] or "").replace("|", "\\|")
        cat = c["category"] or "-"
        year = c["year"] or "-"
        pc = c["participant_count"] or "-"
        imp = c["importance"] or "-"
        host = c["host"] or "-"
        dur = c["duration"] or "-"
        link = f"[妙记]({c['url']})" if c["url"] else "-"
        lines.append(f"| {date} | {name} | {cat} | {year} | {pc} | {imp} | {host} | {dur} | {link} |")
    return "\n".join(lines)


def render_grouped_index(courses):
    """Build a {year: {category: [courses]}} index."""
    idx = defaultdict(lambda: defaultdict(list))
    for c in courses:
        idx[c["year"] or "未分类"][c["category"] or "未分类"].append(c)
    # sort courses by date desc
    out = {}
    for year in sorted(idx.keys(), reverse=True):
        out[year] = {}
        for cat, items in idx[year].items():
            out[year][cat] = sorted(items, key=lambda x: x["date"] or "", reverse=True)
    return out


def push_courses_to_site(full_path, require=False):
    """Push courses.json to the website integration API when configured."""
    if not SITE_SYNC_TOKEN:
        if require:
            raise RuntimeError(
                "COURSE_SYNC_SITE_SYNC_TOKEN 必须配置；COURSE_SYNC_SITE_SYNC_URL 可省略，默认使用 wlbycuc.cn 课程同步接口"
            )
        print("ℹ️ Website sync skipped: COURSE_SYNC_SITE_SYNC_TOKEN not configured")
        return

    try:
        with open(full_path, "rb") as f:
            body = f.read()
        req = urllib.request.Request(
            SITE_SYNC_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {SITE_SYNC_TOKEN}",
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"raw": raw}
        summary = data.get("data") if isinstance(data, dict) else {}
        print(
            "✅ Website sync pushed"
            f" created={summary.get('created', 0)}"
            f" updated={summary.get('updated', 0)}"
            f" skipped={summary.get('skipped', 0)}"
            f" manualPreserved={summary.get('manualPreserved', 0)}"
        )
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        print(f"⚠️ Website sync failed: HTTP {e.code} {detail[:500]}", file=sys.stderr)
        if require:
            raise
    except Exception as e:
        print(f"⚠️ Website sync failed: {e}", file=sys.stderr)
        if require:
            raise


def main():
    p = argparse.ArgumentParser(description="Export Base → JSON / Markdown for site")
    p.add_argument("--days", type=int, default=None,
                   help="Only include courses from last N days (default: all)")
    p.add_argument("--output", default=OUTPUT_DIR,
                   help=f"Output dir (default {OUTPUT_DIR})")
    p.add_argument("--require-site-sync", action="store_true",
                   help="Fail when website API push is not configured or fails")
    args = p.parse_args()

    os.makedirs(args.output, exist_ok=True)

    print(f"📥 Fetching records from Base {BASE_TOKEN}/{TABLE_ID}...")
    raw_records, fields = fetch_all_records()
    print(f"   Got {len(raw_records)} records")

    courses = []
    for rec in raw_records:
        c = normalize_course(rec)
        if c:
            courses.append(c)
    print(f"   Normalized {len(courses)} courses")

    # Date filter
    if args.days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
        courses = [c for c in courses if c["date"] and
                   datetime.fromisoformat(c["date"]) >= cutoff.date()]
        print(f"   After {args.days}-day filter: {len(courses)}")

    # Sort: date desc, importance, then name
    courses.sort(key=lambda c: (c["date"] or "", c["importance"] or "", c["name"] or ""),
                 reverse=True)

    # === Write outputs ===
    # 1) Full JSON
    full_path = os.path.join(args.output, "courses.json")
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(),
                   "count": len(courses), "courses": courses},
                  f, ensure_ascii=False, indent=2)
    print(f"✅ {full_path}")

    # 2) Markdown
    md_path = os.path.join(args.output, "courses.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 课程目录\n\n")
        f.write(f"共 {len(courses)} 门课程（最近更新：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC）\n\n")
        f.write(render_markdown_table(courses))
        f.write("\n")
    print(f"✅ {md_path}")

    # 3) Grouped index
    idx_path = os.path.join(args.output, "courses_index.json")
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(),
                   "index": render_grouped_index(courses)},
                  f, ensure_ascii=False, indent=2)
    print(f"✅ {idx_path}")

    # 4) Latest week
    week_cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
    latest = [c for c in courses if c["date"] >= week_cutoff]
    latest.sort(key=lambda c: c["date"] or "", reverse=True)
    latest_path = os.path.join(args.output, "courses_latest.json")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(),
                   "since": week_cutoff,
                   "count": len(latest), "courses": latest},
                  f, ensure_ascii=False, indent=2)
    print(f"✅ {latest_path} ({len(latest)} courses since {week_cutoff})")

    # 5) Summary stats
    stats = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(courses),
        "by_year": {},
        "by_category": {},
        "by_importance": {},
    }
    for c in courses:
        for k, key in [("by_year", c["year"]), ("by_category", c["category"]),
                       ("by_importance", c["importance"])]:
            v = key or "未分类"
            stats[k][v] = stats[k].get(v, 0) + 1
    stats_path = os.path.join(args.output, "stats.json")
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(f"✅ {stats_path}")

    push_courses_to_site(full_path, require=args.require_site_sync)

    print(f"\n🎉 All exports in: {args.output}")


if __name__ == "__main__":
    main()

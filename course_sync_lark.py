#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
course_sync_lark.py — 用 lark-cli 同步飞书妙记到 Base 表格

替代旧版 sync_feishu_minutes.py（不再依赖 Playwright/cookies）。
本脚本只用 lark-cli 的 OpenAPI + lark-cli 业务封装。

用法:
  python3 course_sync_lark.py --check-env
  python3 course_sync_lark.py --collect-only       # 收集候选 → /tmp/minutes_candidates.json
  python3 course_sync_lark.py --apply-judgments    # 读取 /tmp/minutes_judgments.json → 写 Base

依赖:
  - lark-cli 已安装 (`npm i -g lark-cli`)
  - 已完成 `lark-cli config init` (user 身份)
  - user 身份能搜妙记 (`wlbyzcky.feishu.cn` 域)
  - user 身份对目标 Base 表有读写权限

环境变量（可选，覆盖默认值）:
  COURSE_SYNC_BASE_TOKEN  — 飞书 Base app_token（默认见 BASE_TOKEN）
  COURSE_SYNC_TABLE_ID    — Base 表 ID（默认见 TABLE_ID）
  COURSE_SYNC_DAYS_BACK   — 搜索窗口（天，默认 7）
  COURSE_SYNC_HOST        — 妙记域名（默认 wlbyzcky.feishu.cn）
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

# ===== 配置（可通过环境变量覆盖）=====
BASE_TOKEN = os.environ.get("COURSE_SYNC_BASE_TOKEN", "PK5BbGQx4aoeres9oBCchWKPnfd")
TABLE_ID = os.environ.get("COURSE_SYNC_TABLE_ID", "tblWTN8jkeExIFa0")
DAYS_BACK = int(os.environ.get("COURSE_SYNC_DAYS_BACK", "7"))
HOST = os.environ.get("COURSE_SYNC_HOST", "wlbyzcky.feishu.cn")

CANDIDATES_FILE = "/tmp/minutes_candidates.json"
JUDGMENTS_FILE = "/tmp/minutes_judgments.json"
REPORT_FILE = "/tmp/minutes_sync_report.json"
HOST_ALIAS_MAP = {
    "luu": "霍雨露",
    "luu🦌": "霍雨露",
    "luu鹿": "霍雨露",
}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def normalize_host_name(name):
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    key = text.lower().replace(" ", "")
    return HOST_ALIAS_MAP.get(key, text)


def lark_cli(*args):
    """Run a lark-cli command, return parsed JSON dict (or raw dict on parse fail)."""
    cmd = ["lark-cli"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    out = (result.stdout or "").strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"_raw_stdout": out, "_raw_stderr": (result.stderr or "").strip()}


# ====== 环境检查 ======

def check_env():
    """Return (lark_ok, user_ok)."""
    v = lark_cli("--version")
    lark_ok = bool(v.get("_raw_stdout", "").startswith("lark-cli version"))
    if not lark_ok:
        log("❌ lark-cli 不可用，请先: npm i -g lark-cli")
        return False, False

    info = lark_cli("api", "GET", "/open-apis/authen/v1/user_info")
    user_ok = info.get("code") == 0
    if not user_ok:
        log(f"❌ lark-cli 未认证或 user 身份不可用: {info}")
        return True, False

    name = info.get("data", {}).get("name", "?")
    log(f"✅ lark-cli 就绪 (user: {name})")
    return True, True


# ====== 收集候选 ======

def format_duration(ms_str):
    """Format millisecond duration string as 'X小时Y分' or 'Y分'."""
    try:
        total = int(ms_str) // 1000
    except (ValueError, TypeError):
        return ""
    hours = total // 3600
    minutes = (total % 3600) // 60
    if hours > 0 and minutes > 0:
        return f"{hours}小时{minutes}分"
    if hours > 0:
        return f"{hours}小时"
    return f"{minutes}分"


def lookup_open_id(display_name, cache=None):
    """Resolve a display name to open_id via contact +search-user.

    Returns (open_id, localized_name) or (None, display_name) on miss.
    Uses optional cache dict to avoid repeated lookups in a single run.
    """
    if not display_name:
        return None, display_name
    if cache is not None and display_name in cache:
        return cache[display_name]
    try:
        resp = lark_cli("contact", "+search-user", "--query", display_name)
        users = (resp.get("data") or {}).get("users") or []
        if users:
            # 取第一个（最高相关度）
            u = users[0]
            oid = u.get("open_id")
            lname = u.get("localized_name", display_name)
            if cache is not None:
                cache[display_name] = (oid, lname)
            return oid, lname
    except Exception as e:
        log(f"  contact lookup failed for {display_name}: {e}")
    if cache is not None:
        cache[display_name] = (None, display_name)
    return None, display_name


def search_minutes(start_str, end_str, mode="owner_me", page_size=30):
    """Use lark-cli minutes +search to get minutes in the date range.

    mode:
      - "owner_me"        — only minutes I own
      - "participant_me"  — only minutes where I'm a participant
      - "all"             — no owner/participant filter (returns everything
                            I have visibility on, including shared-by-others)
    """
    items = []
    page_token = None
    while True:
        args = ["minutes", "+search", "--start", start_str, "--end", end_str,
                "--page-size", str(page_size)]
        if mode == "owner_me":
            args += ["--owner-ids", "me"]
        elif mode == "participant_me":
            args += ["--participant-ids", "me"]
        # "all" 不加 filter
        if page_token:
            args += ["--page-token", page_token]
        resp = lark_cli(*args)
        if not resp.get("ok"):
            log(f"  search error ({mode}): {resp.get('error', resp)}")
            break
        data = resp.get("data", {})
        items.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
        if not page_token:
            break
    return items


def get_minute_detail(token):
    """Get full minute detail via /open-apis/minutes/v1/minutes/:token."""
    resp = lark_cli("api", "GET", f"/open-apis/minutes/v1/minutes/{token}")
    if resp.get("code") == 0:
        return resp.get("data", {}).get("minute", {})
    return {}


def collect_candidates(days_back=DAYS_BACK):
    """Search recent minutes → enrich with detail → save to CANDIDATES_FILE.

    3 路并行搜索取并集（去重）:
      1. owner=me              — 我创建的
      2. participant=me        — 我参与的
      3. all (无 filter)       — 别人分享/共享给我的（owner 不是我，participant 可能也不是我）
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")
    log(f"🔍 搜索 {start_str} → {end_str} 的妙记 (search window: {days_back} days)")

    # 3 路搜索，结果去重
    seen_tokens = set()
    seen_signatures = set()  # (date, title) 用于兜底（极少数 token 缺失的情况）
    items = []

    for mode, label in [
        ("owner_me", "owner=me"),
        ("participant_me", "participant=me"),
        ("all", "all (含共享)"),
    ]:
        log(f"  搜: {label}")
        local = search_minutes(start_str, end_str, mode=mode)
        added = 0
        for it in local:
            tok = it.get("token")
            if not tok:
                # 极少数情况 token 缺失，用 (date, title) 兜底
                sig = (it.get("display_info", ""), it.get("meta_data", {}).get("description", ""))
                if sig in seen_signatures:
                    continue
                seen_signatures.add(sig)
                items.append(it)
                added += 1
                continue
            if tok in seen_tokens:
                continue
            seen_tokens.add(tok)
            items.append(it)
            added += 1
        log(f"    拿到 {len(local)} 条，新增 {added} 条")

    log(f"📥 共找到 {len(items)} 条候选 (3 路并集去重)")

    # 拉详情
    candidates = []
    contact_cache = {}  # 显示名 → (open_id, localized_name) 缓存
    for it in items:
        tok = it.get("token")
        if not tok:
            continue
        detail = get_minute_detail(tok)
        title = detail.get("title") or it.get("title", "")
        duration_ms = detail.get("duration")
        duration = format_duration(duration_ms) if duration_ms else ""
        owner_id = detail.get("owner_id", "")
        create_ts = detail.get("create_time")
        date_str = ""
        if create_ts:
            try:
                date_str = datetime.fromtimestamp(int(create_ts) / 1000,
                                                  tz=timezone.utc).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass
        url = detail.get("url") or it.get("meta_data", {}).get("app_link", "")

        # owner_id 可能空（owner 是别人，detail API 不返回完整 owner）
        # 这时从 display_info 解析 "所有者: 天天" → 显示名
        owner_display_name = ""
        if not owner_id:
            desc = it.get("description", "")
            m = re.search(r"所有者:\s*(\S+)", desc)
            if m:
                owner_display_name = m.group(1)
                # 用 contact 查 open_id
                oid, lname = lookup_open_id(owner_display_name, cache=contact_cache)
                owner_id = oid or ""
                if lname and lname != owner_display_name:
                    owner_display_name = lname  # 用 contact 返回的规范名

        candidates.append({
            "token": tok,
            "raw_name": title,
            "url": url,
            "date": date_str,
            "duration": duration,
            "owner": owner_id or owner_display_name,
            "owner_id": owner_id,
            "owner_display_name": owner_display_name,
            "participant_count": None,
        })

    with open(CANDIDATES_FILE, "w", encoding="utf-8") as f:
        json.dump({"candidates": candidates,
                   "search_window": {"start": start_str, "end": end_str},
                   "generated_at": datetime.now(timezone.utc).isoformat()},
                  f, ensure_ascii=False, indent=2)
    log(f"✅ 写入 {CANDIDATES_FILE} ({len(candidates)} 条候选)")
    return candidates


# ====== 查询 Base 现有 ======

def get_existing_records():
    """Return (token_map, name_map) for dedup."""
    token_map = {}
    name_map = {}
    offset = 0
    limit = 100
    while True:
        resp = lark_cli("base", "+record-list",
                        "--base-token", BASE_TOKEN,
                        "--table-id", TABLE_ID,
                        "--limit", str(limit),
                        "--offset", str(offset),
                        "--format", "json",
                        "--as", "user")
        if not resp.get("ok"):
            log(f"❌ 获取 Base 记录失败: {resp}")
            break
        data = resp.get("data", {})
        fields = data.get("fields", [])
        try:
            name_idx = fields.index("课程名称")
            link_idx = fields.index("妙记链接")
        except ValueError:
            log(f"⚠️ Base 表缺少 '课程名称' 或 '妙记链接' 字段: {fields}")
            break
        rows = data.get("data", [])
        rec_ids = data.get("record_id_list", [])
        for i, row in enumerate(rows):
            rec_id = rec_ids[i] if i < len(rec_ids) else None
            if not rec_id:
                continue
            name = row[name_idx] if name_idx < len(row) else None
            link_val = row[link_idx] if link_idx < len(row) else None
            if isinstance(link_val, list) and link_val:
                first = link_val[0]
                link = first.get("link", "") if isinstance(first, dict) else str(first)
            else:
                link = str(link_val) if link_val else ""
            if name:
                name_map[str(name)] = rec_id
            m = re.search(r"minutes/([a-z0-9]+)", link)
            if m:
                token_map[m.group(1)] = rec_id
        if not data.get("has_more"):
            break
        offset += len(rows)
    return token_map, name_map


# ====== 应用 judgment → 写 Base ======

def apply_judgments():
    """Read /tmp/minutes_judgments.json and write/update Base records."""
    if not os.path.exists(JUDGMENTS_FILE):
        log(f"❌ {JUDGMENTS_FILE} 不存在")
        return 1
    with open(JUDGMENTS_FILE, encoding="utf-8") as f:
        payload = json.load(f)
    judgments = payload.get("judgments", [])
    if not judgments:
        log("✅ 无需处理，judgments 为空")
        return 0

    log(f"📊 获取 Base 现有记录中...")
    token_map, name_map = get_existing_records()
    log(f"   Base 现有 {len(token_map)} 条 token 索引 / {len(name_map)} 条 name 索引")

    skipped = []
    written = []
    failed = []
    contact_cache = {}

    for j in judgments:
        tok = j.get("token", "")
        should = j.get("should_record", False)
        if not should:
            skipped.append({"token": tok, "name": j.get("raw_name"),
                            "reason": j.get("reasoning", "")})
            continue

        course_name = j.get("course_name") or j.get("raw_name", "")
        url = j.get("url", "")
        host_name = normalize_host_name(j.get("host_name") or j.get("speaker_name") or j.get("owner_name", ""))
        host_id = j.get("host_id") or j.get("speaker_id") or ""
        if host_name and not host_id:
            host_id, resolved_name = lookup_open_id(host_name, cache=contact_cache)
            host_name = resolved_name or host_name
        if not host_id:
            host_id = j.get("owner_id", "")
        if not host_name:
            host_name = j.get("owner_name", "")
        # 字段名匹配 Base 实际 schema:
        # 课程名称, 参与人数, 重要程度, 上课日期, 课程形式, 课程类别,
        # 会议时长, 主讲人/主持人, 妙记链接, 课程年份
        fields = {
            "课程名称": course_name,
            "参与人数": j.get("participant_count") or None,
            "重要程度": [j["importance"]] if j.get("importance") else ["常规课程"],
            "上课日期": j.get("date", ""),
            "课程形式": ["讲解课"],
            "课程类别": [j["course_category"]] if j.get("course_category") else [],
            "会议时长": j.get("duration", ""),
            "主讲显示名": host_name,
            "主讲人/主持人": [{"id": host_id, "name": host_name}] if host_id else [],
            "妙记链接": f"[{url}]({url})" if url else "",  # Base 存为 markdown 字符串
            "课程年份": [j["course_year"]] if j.get("course_year") else [],
        }
        # 去掉空值
        fields = {k: v for k, v in fields.items() if v not in (None, "", [], {})}

        existing_id = token_map.get(tok) or name_map.get(course_name)
        try:
            upsert_args = [
                "base", "+record-upsert",
                "--base-token", BASE_TOKEN,
                "--table-id", TABLE_ID,
                "--json", json.dumps(fields, ensure_ascii=False),
            ]
            if existing_id:
                upsert_args += ["--record-id", existing_id]
                action = "updated"
            else:
                action = "created"
            resp = lark_cli(*upsert_args)
            if resp.get("ok"):
                written.append({"token": tok, "name": course_name, "action": action,
                                "year": j.get("course_year"),
                                "category": j.get("course_category"),
                                "importance": j.get("importance"),
                                "participant_count": j.get("participant_count")})
                log(f"   ✅ {action}: {course_name}")
            else:
                failed.append({"token": tok, "name": course_name, "error": resp})
                log(f"   ❌ 写入失败: {course_name} → {resp}")
        except Exception as e:
            failed.append({"token": tok, "name": course_name, "error": str(e)})
            log(f"   ❌ 异常: {course_name} → {e}")

    # 报告
    log("\n=== 同步报告 ===")
    log(f"跳过: {len(skipped)}")
    log(f"成功: {len(written)}")
    log(f"失败: {len(failed)}")
    if failed:
        log("失败明细:")
        for f in failed:
            log(f"  - {f['name']}: {f['error']}")

    report = {"skipped": skipped, "written": written, "failed": failed,
              "generated_at": datetime.now(timezone.utc).isoformat()}
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return 0 if not failed else 2


# ====== Main ======

def main():
    p = argparse.ArgumentParser(description="course-sync via lark-cli")
    p.add_argument("--check-env", action="store_true",
                   help="检查 lark-cli + 认证状态")
    p.add_argument("--collect-only", action="store_true",
                   help="收集最近妙记候选 → /tmp/minutes_candidates.json")
    p.add_argument("--apply-judgments", action="store_true",
                   help="应用 /tmp/minutes_judgments.json 写 Base")
    args = p.parse_args()

    if args.check_env:
        lark_ok, user_ok = check_env()
        sys.exit(0 if (lark_ok and user_ok) else 1)
    if args.collect_only:
        lark_ok, user_ok = check_env()
        if not (lark_ok and user_ok):
            sys.exit(1)
        collect_candidates()
        sys.exit(0)
    if args.apply_judgments:
        sys.exit(apply_judgments())
    p.print_help()
    sys.exit(2)


if __name__ == "__main__":
    main()

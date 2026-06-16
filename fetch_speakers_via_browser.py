#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_speakers_via_browser.py — 通过 lark-cli VC API 获取飞书妙记对应会议的参会统计

历史上这个脚本用 mavis browser 打开妙记页面读取头像数量。现在改为纯
lark-cli / OpenAPI 链路：

  1. 读取 /tmp/minutes_candidates.json 中的 minute token、标题、日期、owner
  2. 用 vc +search 在候选日期附近搜索会议记录，拿 meeting_id
  3. 用 vc +recording 校验 meeting_id 对应的 minute_token
  4. 用 vc meeting get --with-participants 获取参会峰值人数和参会人列表

输出仍保持在 /tmp/speaker_counts.json，兼容旧工作流。

字段口径：
  - participant_count: 参会峰值人数，最适合判断一对一/小课/大课
  - participant_count_accumulated: 累计参会人数
  - unique_participant_count: participants 列表按 open_id 去重后的人数

用法:
  python3 fetch_speakers_via_browser.py <token1> [<token2> ...]
  python3 fetch_speakers_via_browser.py --from-candidates /tmp/minutes_candidates.json
"""

import argparse
from datetime import datetime, timedelta
import json
import os
import re
import subprocess
import sys
import time


CANDIDATES_FILE = "/tmp/minutes_candidates.json"
OUTPUT_FILE = "/tmp/speaker_counts.json"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def parse_json_output(stdout):
    """Parse lark-cli JSON, tolerating progress lines before the JSON object."""
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            obj, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if text[index + end :].strip():
            continue
        return obj
    return {"_raw_stdout": text}


def lark_cli(*args, timeout=90):
    cmd = ["lark-cli"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    payload = parse_json_output(result.stdout)
    if result.returncode != 0 and not payload:
        return {
            "ok": False,
            "_returncode": result.returncode,
            "_raw_stdout": (result.stdout or "").strip(),
            "_raw_stderr": (result.stderr or "").strip(),
        }
    if isinstance(payload, dict):
        payload.setdefault("_returncode", result.returncode)
        if result.stderr:
            payload.setdefault("_raw_stderr", result.stderr.strip())
    return payload


def to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def compact_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def title_queries(title):
    title = compact_text(title)
    candidates = [
        title,
        re.sub(r"[《》【】\[\]（）()#：:；;，,、|｜]+", " ", title),
        re.split(r"[；;，,|｜]", title)[0],
        title.split(" ")[0],
    ]
    result = []
    for item in candidates:
        item = compact_text(item)
        if len(item) < 2:
            continue
        if item not in result:
            result.append(item)
    return result


def date_window(date_str, days_before=1, days_after=2):
    try:
        day = datetime.fromisoformat(date_str[:10])
    except (TypeError, ValueError):
        end = datetime.now()
        start = end - timedelta(days=7)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    start = day - timedelta(days=days_before)
    end = day + timedelta(days=days_after)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def get_current_open_id():
    resp = lark_cli("api", "GET", "/open-apis/authen/v1/user_info")
    if resp.get("code") == 0:
        return (resp.get("data") or {}).get("open_id")
    return None


def search_meetings(candidate, current_open_id=None):
    """Return candidate meeting records from several low-noise VC searches."""
    title = candidate.get("raw_name") or ""
    start, end = date_window(candidate.get("date", ""))
    seen = set()
    meetings = []
    attempts = []

    def add_items(label, resp):
        attempts.append(label)
        if not resp.get("ok"):
            return
        for item in (resp.get("data") or {}).get("items") or []:
            meeting_id = item.get("id")
            if not meeting_id or meeting_id in seen:
                continue
            seen.add(meeting_id)
            meetings.append(item)

    for query in title_queries(title):
        resp = lark_cli(
            "vc",
            "+search",
            "--query",
            query,
            "--start",
            start,
            "--end",
            end,
            "--page-size",
            "30",
            "--format",
            "json",
        )
        add_items(f"query:{query}", resp)
        if meetings:
            break

    owner_id = candidate.get("owner_id") or candidate.get("owner")
    if owner_id and str(owner_id).startswith("ou_"):
        resp = lark_cli(
            "vc",
            "+search",
            "--organizer-ids",
            owner_id,
            "--start",
            start,
            "--end",
            end,
            "--page-size",
            "30",
            "--format",
            "json",
        )
        add_items(f"organizer:{owner_id}", resp)

    if current_open_id:
        resp = lark_cli(
            "vc",
            "+search",
            "--participant-ids",
            current_open_id,
            "--start",
            start,
            "--end",
            end,
            "--page-size",
            "30",
            "--format",
            "json",
        )
        add_items(f"participant:{current_open_id}", resp)

    return meetings, attempts


def search_visible_meetings(candidate, current_open_id=None):
    """Fallback search over visible meetings near the candidate date."""
    seen = set()
    meetings = []
    attempts = []

    def add_items(label, resp):
        attempts.append(label)
        if not resp.get("ok"):
            return
        for item in (resp.get("data") or {}).get("items") or []:
            meeting_id = item.get("id")
            if not meeting_id or meeting_id in seen:
                continue
            seen.add(meeting_id)
            meetings.append(item)

    for days_before, days_after, label in ((2, 3, "near-visible"), (5, 5, "wide-visible")):
        start, end = date_window(candidate.get("date", ""), days_before=days_before, days_after=days_after)
        resp = lark_cli(
            "vc",
            "+search",
            "--start",
            start,
            "--end",
            end,
            "--page-size",
            "30",
            "--format",
            "json",
        )
        add_items(f"{label}:{start}..{end}", resp)

        if current_open_id:
            resp = lark_cli(
                "vc",
                "+search",
                "--participant-ids",
                current_open_id,
                "--start",
                start,
                "--end",
                end,
                "--page-size",
                "30",
                "--format",
                "json",
            )
            add_items(f"{label}-participant:{start}..{end}", resp)

    return meetings, attempts


def recordings_for(meeting_ids):
    if not meeting_ids:
        return []
    recordings = []
    for index in range(0, len(meeting_ids), 20):
        chunk = meeting_ids[index : index + 20]
        resp = lark_cli(
            "vc",
            "+recording",
            "--meeting-ids",
            ",".join(chunk),
            "--format",
            "json",
            timeout=120,
        )
        recordings.extend((resp.get("data") or {}).get("recordings") or [])
    return recordings


def meeting_participants(meeting_id):
    resp = lark_cli(
        "vc",
        "meeting",
        "get",
        "--meeting-id",
        meeting_id,
        "--with-participants",
        "--user-id-type",
        "open_id",
        "--format",
        "json",
        timeout=120,
    )
    if resp.get("code") != 0:
        return None, resp
    return (resp.get("data") or {}).get("meeting") or {}, resp


def resolve_participant_count(candidate, current_open_id=None):
    token = candidate.get("token")
    meetings, attempts = search_meetings(candidate, current_open_id=current_open_id)
    meeting_ids = [item["id"] for item in meetings if item.get("id")]
    recordings = recordings_for(meeting_ids)

    def matched_result(recordings, attempts):
        for recording in recordings:
            if recording.get("minute_token") != token:
                continue
            meeting_id = recording.get("meeting_id")
            meeting, raw = meeting_participants(meeting_id)
            if meeting is None:
                return {
                    "token": token,
                    "raw_name": candidate.get("raw_name"),
                    "participant_count": None,
                    "match_status": "meeting_detail_failed",
                    "meeting_id": meeting_id,
                    "error": raw,
                    "search_attempts": attempts,
                }
            participants = meeting.get("participants") or []
            unique_ids = {p.get("id") for p in participants if p.get("id")}
            return {
                "token": token,
                "raw_name": candidate.get("raw_name"),
                "participant_count": to_int(meeting.get("participant_count")),
                "participant_count_accumulated": to_int(meeting.get("participant_count_accumulated")),
                "participant_records_count": len(participants),
                "unique_participant_count": len(unique_ids),
                "meeting_id": meeting_id,
                "meeting_topic": meeting.get("topic"),
                "match_status": "matched",
                "search_attempts": attempts,
            }
        return None

    result = matched_result(recordings, attempts)
    if result:
        return result

    fallback_meetings, fallback_attempts = search_visible_meetings(
        candidate,
        current_open_id=current_open_id,
    )
    known_ids = set(meeting_ids)
    fallback_ids = [
        item["id"]
        for item in fallback_meetings
        if item.get("id") and item.get("id") not in known_ids
    ]
    if fallback_ids:
        recordings.extend(recordings_for(fallback_ids))
        attempts.extend(fallback_attempts)
        meeting_ids.extend(fallback_ids)

    result = matched_result(recordings, attempts)
    if result:
        return result

    return {
        "token": token,
        "raw_name": candidate.get("raw_name"),
        "participant_count": None,
        "match_status": "not_matched",
        "candidate_meeting_count": len(meeting_ids),
        "search_attempts": attempts,
    }


def load_candidates(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("candidates", [])


def main():
    parser = argparse.ArgumentParser(description="Fetch participant counts via lark-cli VC API")
    parser.add_argument("tokens", nargs="*", help="Minute tokens to fetch")
    parser.add_argument(
        "--from-candidates",
        metavar="PATH",
        help="Read tokens from /tmp/minutes_candidates.json (or any compatible path)",
    )
    parser.add_argument(
        "--output",
        default=OUTPUT_FILE,
        help=f"Output JSON path (default {OUTPUT_FILE})",
    )
    args = parser.parse_args()

    if args.from_candidates:
        targets = load_candidates(args.from_candidates)
    elif args.tokens:
        targets = [{"token": token, "raw_name": token, "date": ""} for token in args.tokens]
    else:
        log("❌ 至少给一个 token 或 --from-candidates")
        sys.exit(2)

    current_open_id = get_current_open_id()
    log(f"📋 共 {len(targets)} 个目标需要匹配 VC 参会统计")
    results = []
    for candidate in targets:
        log(f"[{candidate.get('token')}] {candidate.get('raw_name', '?')}")
        result = resolve_participant_count(candidate, current_open_id=current_open_id)
        if result.get("match_status") == "matched":
            log(
                "  参会峰值 = "
                f"{result.get('participant_count')}；累计 = "
                f"{result.get('participant_count_accumulated')}；"
                f"去重 = {result.get('unique_participant_count')}"
            )
        else:
            log(f"  未匹配到会议记录 ({result.get('match_status')})")
        results.append(result)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(
            {
                "results": results,
                "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "method": "lark-cli vc meeting get --with-participants",
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    log(f"\n✅ saved {args.output} ({len(results)} entries)")


if __name__ == "__main__":
    main()

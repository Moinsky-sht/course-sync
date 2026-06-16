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
from difflib import SequenceMatcher
import json
import os
import re
import subprocess
import sys
import time


CANDIDATES_FILE = "/tmp/minutes_candidates.json"
OUTPUT_FILE = "/tmp/speaker_counts.json"
GENERIC_SPEAKER_RE = re.compile(r"^(speaker|说话人|讲话人|发言人|用户)\s*\d*$", re.I)
DATE_LABEL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MULTI_SPEAKER_TITLE_RE = re.compile(r"(研讨|互评|交流会|讨论|分享会|班会|点评|评析)")
KNOWN_TEACHERS = {
    "天天", "天", "泠泠七", "陈泓丞", "丞丞", "妙妙", "李飞", "sunny",
    "小菜", "乔璐伊", "舟舟耶", "霍雨露",
}
HOST_ALIAS_MAP = {
    "luu": "霍雨露",
    "luu🦌": "霍雨露",
    "luu鹿": "霍雨露",
}
CONTACT_CACHE = {}


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
    if not payload and result.stderr:
        payload = parse_json_output(result.stderr)
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


def is_generic_speaker_label(label):
    label = compact_text(label)
    return not label or bool(GENERIC_SPEAKER_RE.match(label)) or bool(DATE_LABEL_RE.match(label))


def normalize_host_label(label):
    text = compact_text(label)
    key = text.lower().replace(" ", "")
    return HOST_ALIAS_MAP.get(key, text)


def parse_timecode_ms(value):
    match = re.match(r"^(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?$", str(value or ""))
    if not match:
        return None
    hours, minutes, seconds, ms = match.groups()
    return (
        int(hours) * 3600 * 1000
        + int(minutes) * 60 * 1000
        + int(seconds) * 1000
        + int((ms or "0").ljust(3, "0")[:3])
    )


def compact_title(value):
    text = compact_text(value).lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[《》【】\[\]（）()#：:；;，,、|｜/\\\\_-]+", " ", text)
    text = re.sub(r"\b20\d{2}年\d{1,2}月\d{1,2}日\b", " ", text)
    return compact_text(text)


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


def title_similarity(a, b):
    a_norm = compact_title(a)
    b_norm = compact_title(b)
    if not a_norm or not b_norm:
        return 0.0
    if a_norm in b_norm or b_norm in a_norm:
        return 1.0
    return SequenceMatcher(None, a_norm, b_norm).ratio()


def parse_ms(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 10_000_000_000 else value * 1000


def parse_meeting_time(value):
    try:
        return int(value) * 1000
    except (TypeError, ValueError):
        return None


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


def get_minute_detail(token):
    if not token:
        return {}
    resp = lark_cli(
        "minutes",
        "minutes",
        "get",
        "--minute-token",
        token,
        "--as",
        "user",
        "--format",
        "json",
        timeout=90,
    )
    if resp.get("code") == 0:
        return (resp.get("data") or {}).get("minute") or {}
    return {}


def enrich_candidate(candidate):
    token = candidate.get("token")
    detail = get_minute_detail(token)
    if not detail:
        return candidate

    enriched = dict(candidate)
    enriched.setdefault("raw_name", detail.get("title") or token)
    if detail.get("title"):
        enriched["raw_name"] = detail.get("title")
    if detail.get("owner_id"):
        enriched["owner_id"] = detail.get("owner_id")
        enriched.setdefault("owner", detail.get("owner_id"))
    if detail.get("url"):
        enriched.setdefault("url", detail.get("url"))
    if detail.get("note_id"):
        enriched["note_id"] = detail.get("note_id")
    if detail.get("duration"):
        enriched["duration_ms"] = parse_ms(detail.get("duration"))
    if detail.get("create_time"):
        enriched["minute_create_time_ms"] = parse_ms(detail.get("create_time"))
        if not enriched.get("date"):
            try:
                enriched["date"] = datetime.fromtimestamp(
                    enriched["minute_create_time_ms"] / 1000,
                ).strftime("%Y-%m-%d")
            except Exception:
                pass
    return enriched


def get_current_open_id():
    resp = lark_cli("api", "GET", "/open-apis/authen/v1/user_info")
    if resp.get("code") == 0:
        return (resp.get("data") or {}).get("open_id")
    return None


def get_user_name(user_id):
    if not user_id:
        return ""
    if user_id in CONTACT_CACHE:
        return CONTACT_CACHE[user_id]
    resp = lark_cli(
        "contact",
        "+get-user",
        "--user-id",
        user_id,
        "--user-id-type",
        "open_id",
        "--format",
        "json",
        "--as",
        "user",
        timeout=60,
    )
    name = ""
    if resp.get("ok"):
        user = (resp.get("data") or {}).get("user") or {}
        i18n = user.get("i18n_name") or {}
        name = i18n.get("zh_cn") or user.get("name") or ""
    CONTACT_CACHE[user_id] = name
    return name


def participant_duration_summary(meeting):
    participants = meeting.get("participants") or []
    rows = []
    for p in participants:
        duration = to_int(p.get("in_meeting_duration")) or 0
        row = {
            "id": p.get("id") or "",
            "duration_seconds": duration,
            "is_host": bool(p.get("is_host")),
            "is_cohost": bool(p.get("is_cohost")),
        }
        if row["is_host"] or row["is_cohost"]:
            row["name"] = get_user_name(row["id"])
        rows.append(row)
    rows.sort(key=lambda item: item["duration_seconds"], reverse=True)
    return rows[:10]


def transcript_path_from_note(note):
    artifacts = (note or {}).get("artifacts") or {}
    path = artifacts.get("transcript_file") or ""
    if not path:
        return ""
    if os.path.isabs(path):
        return path
    return os.path.join(os.getcwd(), path)


def parse_transcript_file(path, total_duration_ms=None):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        text = f.read()

    speaker_line = re.compile(r"^(.+?)\s+(\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?)\s*$")
    segments = []
    current = None
    body = []
    for line in text.splitlines():
        match = speaker_line.match(line.strip())
        if match:
            if current:
                current["body"] = "\n".join(body).strip()
                segments.append(current)
            label, timecode = match.groups()
            current = {
                "label": compact_text(label),
                "start_ms": parse_timecode_ms(timecode) or 0,
                "body": "",
            }
            body = []
        elif current:
            body.append(line)
    if current:
        current["body"] = "\n".join(body).strip()
        segments.append(current)

    stats = {}
    for index, segment in enumerate(segments):
        label = segment["label"]
        if is_generic_speaker_label(label) and DATE_LABEL_RE.match(label):
            continue
        body_text = compact_text(re.sub(r"<[^>]+>", " ", segment.get("body") or ""))
        if not body_text:
            continue
        next_start = None
        for next_segment in segments[index + 1:]:
            if next_segment.get("start_ms") is not None:
                next_start = next_segment["start_ms"]
                break
        if next_start is None and total_duration_ms:
            next_start = total_duration_ms
        speech_ms = max(0, (next_start or segment["start_ms"]) - segment["start_ms"])
        # Long gaps after a paragraph should not turn silence into speaking time.
        speech_ms = min(speech_ms, 5 * 60 * 1000)
        item = stats.setdefault(label, {
            "label": label,
            "turns": 0,
            "chars": 0,
            "speech_ms": 0,
            "is_named": not is_generic_speaker_label(label),
        })
        item["turns"] += 1
        item["chars"] += len(body_text)
        item["speech_ms"] += speech_ms

    rows = list(stats.values())
    total_chars = sum(row["chars"] for row in rows) or 1
    total_speech_ms = sum(row["speech_ms"] for row in rows) or 1
    for row in rows:
        row["char_share"] = round(row["chars"] / total_chars, 4)
        row["speech_share"] = round(row["speech_ms"] / total_speech_ms, 4)
        row["speech_seconds"] = round(row["speech_ms"] / 1000)
        del row["speech_ms"]
    rows.sort(key=lambda item: (item["chars"], item["speech_seconds"]), reverse=True)
    return rows


def transcript_stats_for_token(token, total_duration_ms=None):
    note, raw = vc_notes_by_minute_token(token)
    if not note:
        return [], raw
    path = transcript_path_from_note(note)
    return parse_transcript_file(path, total_duration_ms=total_duration_ms), raw


def course_rule_host(candidate):
    name = str(candidate.get("raw_name") or "")
    category = str(candidate.get("course_category") or candidate.get("category") or "")

    # Only keep the stable curriculum rule. Other subjects change by cohort and
    # must be resolved from transcript/VC evidence, not year/category mappings.
    if category == "世界文明史" or "文明史" in name:
        return "泠泠七"
    return ""


def resolve_course_host(candidate, meeting=None):
    token = candidate.get("token")
    title = candidate.get("raw_name") or ""
    category = candidate.get("course_category") or candidate.get("category") or ""
    is_multi_context = bool(MULTI_SPEAKER_TITLE_RE.search(f"{title} {category}"))
    stable_fallback = course_rule_host(candidate)
    if stable_fallback:
        return {
            "status": "single",
            "host_name": stable_fallback,
            "method": "stable_world_civilization",
            "confidence": "high",
            "reason": "世界文明史/文明史采用稳定主讲人口径，避免逐字稿临时标签覆盖",
            "transcript_speaker_stats": [],
        }

    transcript_stats, transcript_raw = transcript_stats_for_token(
        token,
        total_duration_ms=candidate.get("duration_ms"),
    )
    named_stats = [row for row in transcript_stats if row.get("is_named")]
    named_stats.sort(key=lambda item: (item["chars"], item["speech_seconds"]), reverse=True)

    if named_stats:
        top = named_stats[0]
        second = named_stats[1] if len(named_stats) > 1 else None
        second_ratio = (second["chars"] / top["chars"]) if second and top["chars"] else 0
        multi_by_distribution = (
            is_multi_context
            and len(named_stats) >= 2
            and (top["char_share"] < 0.45 or second_ratio >= 0.45)
        )
        if multi_by_distribution:
            return {
                "status": "multi",
                "host_name": "多人",
                "method": "transcript_named_multi",
                "confidence": "high",
                "reason": "研讨/交流类课程且多个实名说话人发言占比较高",
                "transcript_speaker_stats": transcript_stats[:8],
            }
        if top["char_share"] >= 0.38 or top["speech_share"] >= 0.38 or not second or second_ratio < 0.55:
            return {
                "status": "single",
                "host_name": normalize_host_label(top["label"]),
                "method": "transcript_named_dominant",
                "confidence": "high",
                "reason": "逐字稿实名主说话人占优",
                "transcript_speaker_stats": transcript_stats[:8],
            }

    host_name = ""
    cohost_names = []
    duration_top = []
    if meeting:
        duration_top = participant_duration_summary(meeting)
        host_id = ((meeting.get("host_user") or {}).get("id"))
        host_name = normalize_host_label(get_user_name(host_id))
        cohost_names = [
            normalize_host_label(row.get("name"))
            for row in duration_top
            if row.get("is_cohost") and row.get("name") and row.get("name") != host_name
        ]
        if is_multi_context and len(set([host_name] + cohost_names) - {""}) >= 2:
            return {
                "status": "multi",
                "host_name": "多人",
                "method": "vc_host_cohost_multi",
                "confidence": "medium",
                "reason": "研讨/交流类课程且 VC host/cohost 包含多位老师",
                "vc_host_name": host_name,
                "vc_cohost_names": cohost_names,
                "participant_duration_top": duration_top,
                "transcript_speaker_stats": transcript_stats[:8],
            }
        if host_name and host_name in KNOWN_TEACHERS:
            return {
                "status": "single",
                "host_name": host_name,
                "method": "vc_host_known_teacher",
                "confidence": "medium",
                "reason": "逐字稿无可用实名主讲人，使用 VC host 老师",
                "vc_host_name": host_name,
                "participant_duration_top": duration_top,
                "transcript_speaker_stats": transcript_stats[:8],
            }

    return {
        "status": "unknown",
        "host_name": "",
        "method": "unresolved",
        "confidence": "none",
        "reason": "未找到实名主说话人、可用 VC host 或课程规则",
        "transcript_error": transcript_raw.get("error") if isinstance(transcript_raw, dict) else None,
        "vc_host_name": host_name,
        "participant_duration_top": duration_top,
        "transcript_speaker_stats": transcript_stats[:8],
    }


def search_all_meetings(label, args, max_pages=20):
    """Run paginated vc +search and return all visible meeting records."""
    items = []
    seen = set()
    page_token = None
    attempts = []
    for _ in range(max_pages):
        call_args = ["vc", "+search"] + list(args) + ["--page-size", "30", "--format", "json"]
        if page_token:
            call_args += ["--page-token", page_token]
        resp = lark_cli(*call_args, timeout=120)
        attempts.append({
            "label": label,
            "ok": bool(resp.get("ok")),
            "page_token": page_token,
            "error": resp.get("error"),
        })
        if not resp.get("ok"):
            break
        data = resp.get("data") or {}
        for item in data.get("items") or []:
            meeting_id = item.get("id")
            if not meeting_id or meeting_id in seen:
                continue
            seen.add(meeting_id)
            items.append(item)
        if not data.get("has_more") or not data.get("page_token"):
            break
        page_token = data.get("page_token")
    return items, attempts


def search_meetings(candidate, current_open_id=None):
    """Return candidate meeting records from all reasonable VC searches."""
    title = candidate.get("raw_name") or ""
    start, end = date_window(candidate.get("date", ""))
    seen = set()
    meetings = []
    attempts = []

    def add_items(label, items, local_attempts):
        attempts.extend(local_attempts or [{"label": label}])
        for item in items:
            meeting_id = item.get("id")
            if not meeting_id or meeting_id in seen:
                continue
            seen.add(meeting_id)
            meetings.append(item)

    for query in title_queries(title):
        items, local_attempts = search_all_meetings(
            f"query:{query}:{start}..{end}",
            ["--query", query, "--start", start, "--end", end],
        )
        add_items(f"query:{query}", items, local_attempts)

    owner_id = candidate.get("owner_id") or candidate.get("owner")
    if owner_id and str(owner_id).startswith("ou_"):
        items, local_attempts = search_all_meetings(
            f"organizer:{owner_id}:{start}..{end}",
            ["--organizer-ids", owner_id, "--start", start, "--end", end],
        )
        add_items(f"organizer:{owner_id}", items, local_attempts)

        items, local_attempts = search_all_meetings(
            f"owner-as-participant:{owner_id}:{start}..{end}",
            ["--participant-ids", owner_id, "--start", start, "--end", end],
        )
        add_items(f"owner-as-participant:{owner_id}", items, local_attempts)

    if current_open_id:
        items, local_attempts = search_all_meetings(
            f"participant:{current_open_id}:{start}..{end}",
            ["--participant-ids", current_open_id, "--start", start, "--end", end],
        )
        add_items(f"participant:{current_open_id}", items, local_attempts)

    return meetings, attempts


def search_visible_meetings(candidate, current_open_id=None):
    """Fallback search over visible meetings near the candidate date."""
    seen = set()
    meetings = []
    attempts = []

    def add_items(label, items, local_attempts):
        attempts.extend(local_attempts or [{"label": label}])
        for item in items:
            meeting_id = item.get("id")
            if not meeting_id or meeting_id in seen:
                continue
            seen.add(meeting_id)
            meetings.append(item)

    for days_before, days_after, label in ((2, 3, "near-visible"), (7, 7, "wide-visible")):
        start, end = date_window(candidate.get("date", ""), days_before=days_before, days_after=days_after)
        items, local_attempts = search_all_meetings(
            f"{label}:{start}..{end}",
            ["--start", start, "--end", end],
        )
        add_items(f"{label}:{start}..{end}", items, local_attempts)

        if current_open_id:
            items, local_attempts = search_all_meetings(
                f"{label}-participant:{start}..{end}",
                ["--participant-ids", current_open_id, "--start", start, "--end", end],
            )
            add_items(f"{label}-participant:{start}..{end}", items, local_attempts)

    return meetings, attempts


def recordings_for(meeting_ids):
    if not meeting_ids:
        return []
    recordings = []
    for index in range(0, len(meeting_ids), 50):
        chunk = meeting_ids[index : index + 50]
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


def meeting_participants(meeting_id, with_participants=True):
    resp = lark_cli(
        "vc",
        "meeting",
        "get",
        "--params",
        json.dumps({
            "meeting_id": meeting_id,
            "with_participants": with_participants,
            "user_id_type": "open_id",
        }, ensure_ascii=False),
        "--format",
        "json",
        timeout=120,
    )
    if resp.get("code") != 0:
        return None, resp
    return (resp.get("data") or {}).get("meeting") or {}, resp


def vc_notes_by_minute_token(token):
    """Try the direct minute-token path; it may fail when scopes are missing."""
    if not token:
        return None, None
    resp = lark_cli(
        "vc",
        "+notes",
        "--minute-tokens",
        token,
        "--format",
        "json",
        timeout=120,
    )
    if not resp.get("ok"):
        return None, resp
    notes = (resp.get("data") or {}).get("notes") or []
    for note in notes:
        if note.get("minute_token") == token or len(notes) == 1:
            return note, resp
    return None, resp


def participant_payload(candidate, meeting, match_status, evidence, attempts, raw_error=None):
    participants = meeting.get("participants") or []
    unique_ids = {p.get("id") for p in participants if p.get("id")}
    host_user = meeting.get("host_user") or {}
    host_resolution = resolve_course_host(candidate, meeting)
    return {
        "token": candidate.get("token"),
        "raw_name": candidate.get("raw_name"),
        "participant_count": to_int(meeting.get("participant_count")),
        "participant_count_accumulated": to_int(meeting.get("participant_count_accumulated")),
        "participant_records_count": len(participants),
        "unique_participant_count": len(unique_ids),
        "participants": participants,
        "host_user": host_user,
        "meeting_id": meeting.get("id"),
        "meeting_topic": meeting.get("topic"),
        "meeting_note_id": meeting.get("note_id"),
        "match_status": match_status,
        "match_evidence": evidence,
        "suggested_host": host_resolution.get("host_name") or "",
        "host_resolution": host_resolution,
        "search_attempts": attempts,
        **({"error": raw_error} if raw_error else {}),
    }


def score_meeting(candidate, meeting):
    topic = meeting.get("topic") or ""
    display = meeting.get("display_info") or ""
    score = 0.0
    reasons = []

    sim = max(title_similarity(candidate.get("raw_name"), topic), title_similarity(candidate.get("raw_name"), display))
    if sim:
        score += sim * 60
        reasons.append(f"title_similarity={sim:.2f}")

    if candidate.get("owner_id"):
        host_id = ((meeting.get("host_user") or {}).get("id"))
        participant_ids = {p.get("id") for p in meeting.get("participants") or [] if p.get("id")}
        if host_id == candidate.get("owner_id"):
            score += 25
            reasons.append("owner_is_host")
        elif candidate.get("owner_id") in participant_ids:
            score += 10
            reasons.append("owner_is_participant")

    minute_time = candidate.get("minute_create_time_ms")
    start_time = parse_meeting_time(meeting.get("start_time") or meeting.get("create_time"))
    end_time = parse_meeting_time(meeting.get("end_time"))
    if minute_time and start_time and end_time:
        if start_time <= minute_time <= end_time + 30 * 60 * 1000:
            score += 20
            reasons.append("minute_create_time_near_meeting")
        else:
            distance = min(abs(minute_time - start_time), abs(minute_time - end_time))
            if distance <= 4 * 60 * 60 * 1000:
                score += 8
                reasons.append("minute_create_time_same_half_day")

    duration_ms = candidate.get("duration_ms")
    if duration_ms and start_time and end_time and end_time > start_time:
        meeting_duration_ms = end_time - start_time
        ratio = min(duration_ms, meeting_duration_ms) / max(duration_ms, meeting_duration_ms)
        if ratio >= 0.8:
            score += 20
            reasons.append(f"duration_ratio={ratio:.2f}")
        elif ratio >= 0.5:
            score += 8
            reasons.append(f"duration_ratio={ratio:.2f}")

    return score, reasons


def exact_meeting_from_notes(candidate, attempts):
    note, raw = vc_notes_by_minute_token(candidate.get("token"))
    attempts.append({
        "label": "vc-notes-by-minute-token",
        "ok": bool(note),
        "error": raw.get("error") if isinstance(raw, dict) else None,
    })
    if not note or not note.get("meeting_id"):
        return None
    meeting, raw_meeting = meeting_participants(note.get("meeting_id"))
    if meeting is None:
        host_resolution = resolve_course_host(candidate, None)
        return {
            "token": candidate.get("token"),
            "raw_name": candidate.get("raw_name"),
            "participant_count": None,
            "match_status": "meeting_detail_failed",
            "meeting_id": note.get("meeting_id"),
            "suggested_host": host_resolution.get("host_name") or "",
            "host_resolution": host_resolution,
            "error": raw_meeting,
            "search_attempts": attempts,
        }
    return participant_payload(
        candidate,
        meeting,
        "matched",
        "vc +notes minute_token returned meeting_id",
        attempts,
    )


def resolve_participant_count(candidate, current_open_id=None):
    candidate = enrich_candidate(candidate)
    token = candidate.get("token")
    attempts = []

    direct_result = exact_meeting_from_notes(candidate, attempts)
    if direct_result:
        return direct_result

    meetings, search_attempts = search_meetings(candidate, current_open_id=current_open_id)
    attempts.extend(search_attempts)
    meeting_ids = [item["id"] for item in meetings if item.get("id")]
    recordings = recordings_for(meeting_ids)

    def matched_result(recordings, attempts):
        for recording in recordings:
            if recording.get("minute_token") != token:
                continue
            meeting_id = recording.get("meeting_id")
            meeting, raw = meeting_participants(meeting_id)
            if meeting is None:
                host_resolution = resolve_course_host(candidate, None)
                return {
                    "token": token,
                    "raw_name": candidate.get("raw_name"),
                    "participant_count": None,
                    "match_status": "meeting_detail_failed",
                    "meeting_id": meeting_id,
                    "suggested_host": host_resolution.get("host_name") or "",
                    "host_resolution": host_resolution,
                    "error": raw,
                    "search_attempts": attempts,
                }
            return participant_payload(
                candidate,
                meeting,
                "matched",
                "vc +recording minute_token exact match",
                attempts,
            )
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

    # Second exact path: meeting.note_id == minutes.get.note_id. This catches cases
    # where the recording relation is not visible but the meeting details are.
    note_id = candidate.get("note_id")
    scored = []
    for meeting_id in meeting_ids:
        meeting, raw = meeting_participants(meeting_id)
        if meeting is None:
            continue
        if note_id and meeting.get("note_id") == note_id:
            return participant_payload(
                candidate,
                meeting,
                "matched",
                "meeting.note_id exact match",
                attempts,
            )
        score, reasons = score_meeting(candidate, meeting)
        scored.append((score, reasons, meeting))

    scored.sort(key=lambda item: item[0], reverse=True)
    if scored:
        best_score, reasons, meeting = scored[0]
        # This is not an exact token/note match, but is useful when Feishu hides
        # the recording edge. Keep the status explicit so judgment can be stricter.
        if best_score >= 85:
            return participant_payload(
                candidate,
                meeting,
                "probable_matched",
                f"high-confidence meeting score {best_score:.1f}: {', '.join(reasons)}",
                attempts,
            )

    host_resolution = resolve_course_host(candidate, None)
    return {
        "token": token,
        "raw_name": candidate.get("raw_name"),
        "participant_count": None,
        "match_status": "not_matched",
        "suggested_host": host_resolution.get("host_name") or "",
        "host_resolution": host_resolution,
        "candidate_meeting_count": len(meeting_ids),
        "top_candidates": [
            {
                "score": round(score, 1),
                "reasons": reasons,
                "meeting_id": meeting.get("id"),
                "topic": meeting.get("topic"),
                "note_id": meeting.get("note_id"),
                "participant_count": to_int(meeting.get("participant_count")),
            }
            for score, reasons, meeting in scored[:5]
        ],
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
                f"去重 = {result.get('unique_participant_count')}；"
                f"主讲 = {result.get('suggested_host') or '未定'}"
            )
        else:
            log(
                f"  未匹配到会议记录 ({result.get('match_status')})；"
                f"主讲 = {result.get('suggested_host') or '未定'}"
            )
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

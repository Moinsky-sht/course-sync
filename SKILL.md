---
name: course-sync
description: >
  自动同步飞书妙记到飞书 Base 课程表格，并导出课程 JSON/Markdown 给个人网站使用。
  Use when the user says "同步课程", "同步妙记", "每日课程同步", "拉取最新课程",
  "/course-sync", or provides the configured course Base link.
---

# Course Sync

Use this skill for the Feishu/Lark course synchronization workflow:

1. Search recent Feishu Minutes with `lark-cli minutes +search`.
2. Use `lark-cli` VC meeting APIs to resolve the related meeting and read participant statistics.
3. Review candidates and create `/tmp/minutes_judgments.json`.
4. Upsert approved course records into the configured Feishu Base table.
5. Export the Base table to JSON/Markdown for the course website.

This skill is installed from:

- GitHub: `https://github.com/Moinsky-sht/course-sync`
- Local path: `/Users/moinsky/.codex/skills/course-sync`

## Configuration

Default target:

- Base token: `PK5BbGQx4aoeres9oBCchWKPnfd`
- Table ID: `tblWTN8jkeExIFa0`
- Host: `wlbyzcky.feishu.cn`
- Base link trigger: `https://wlbyzcky.feishu.cn/wiki/TGGww8zijiQF2xk77tGcLYFtnW1`

Environment variables can override defaults:

- `COURSE_SYNC_BASE_TOKEN`
- `COURSE_SYNC_TABLE_ID`
- `COURSE_SYNC_DAYS_BACK`
- `COURSE_SYNC_HOST`
- `COURSE_SYNC_OUTPUT_DIR`

## Required Context

Before using Feishu/Lark commands, follow the installed Lark skills:

- Read `lark-shared` for authentication and permission handling.
- Use `lark-minutes` for Minutes search and metadata.
- Use `lark-vc` for VC meeting lookup, recording-to-minute matching, and participant statistics.
- Use `lark-base` for Base reads/writes.
- Use `lark-contact` if owner display names need user resolution.

Do not print app secrets, access tokens, or user tokens.

## Environment Check

Run from the skill directory:

```bash
cd /Users/moinsky/.codex/skills/course-sync
python3 course_sync_lark.py --check-env
```

Continue only if it reports that `lark-cli` is ready for the user identity.

## Collect Candidates

Run:

```bash
cd /Users/moinsky/.codex/skills/course-sync
python3 course_sync_lark.py --collect-only
```

This writes `/tmp/minutes_candidates.json`.

The script searches recent minutes through a three-way union:

- `owner=me`
- `participant=me`
- all visible/shared minutes

## Participant Counts

Run:

```bash
cd /Users/moinsky/.codex/skills/course-sync
python3 fetch_speakers_via_browser.py --from-candidates /tmp/minutes_candidates.json
```

This writes `/tmp/speaker_counts.json`.

The script no longer opens a browser. It uses `lark-cli vc +search`,
`lark-cli vc +recording`, and `lark-cli vc meeting get --with-participants` to
match each minute token back to its VC meeting.

Participant-count fields:

- `participant_count`: peak concurrent participant count, the primary signal for
  judging one-on-one/small meetings versus real classes.
- `participant_count_accumulated`: accumulated joins reported by VC.
- `unique_participant_count`: unique participant IDs in the returned participant
  list.

If a minute cannot be matched to a VC meeting, leave its count as `null` and make
the judgment more conservative.

## Judgment Rules

Read `/tmp/minutes_candidates.json` and `/tmp/speaker_counts.json` if present.
Create `/tmp/minutes_judgments.json` with:

```json
{
  "judgments": [
    {
      "token": "minute token",
      "raw_name": "original title",
      "url": "minute url",
      "date": "YYYY-MM-DD",
      "duration": "meeting duration",
      "owner": "open_id",
      "owner_id": "open_id",
      "owner_name": "display name",
      "should_record": true,
      "course_name": "clean course name",
      "course_year": "27考研",
      "course_category": "其他课程",
      "importance": "常规课程",
      "participant_count": 0,
      "reasoning": "one-sentence reason"
    }
  ]
}
```

Record only training-related courses, public classes, mock interviews, exam
analysis, class meetings, writing guidance, materials review classes, and similar
student-facing teaching content. Skip internal chats, internal training,
one-on-one exchanges, small non-course meetings, personal discussions, and
non-student-training meetings. Prefer skipping when uncertain.

For course-vs-small-meeting judgment, use `participant_count` as the peak
participant signal. A normal large class is generally `participant_count >= 10`.
Counts below 10 should be skipped unless the title and context clearly identify a
student-facing course type that naturally has fewer attendees, such as a mock
interview or a planned small-group class.

Use only these course categories:

公开课、班会课、报考分析课、人类简史、世界文明史、中国文学史、哲学导论、媒介与社会、艺术学概论、百日押题课、模考讲解课、材料评议课、复试讲解课、其他课程、783主题写作、785艺术作品评论、783/785通用

Use only these importance values:

必刷课程、常规课程

## Apply Judgments

After creating a fresh judgment file:

```bash
cd /Users/moinsky/.codex/skills/course-sync
python3 course_sync_lark.py --apply-judgments
```

This writes `/tmp/minutes_sync_report.json`.

The script upserts by minute token first, then by course name, to avoid duplicate
records.

## Export Website Data

Run:

```bash
cd /Users/moinsky/.codex/skills/course-sync
python3 site_export.py
```

Default output directory: `/tmp/site_export/`.

Expected files:

- `courses.json`
- `courses.md`
- `courses_index.json`
- `courses_latest.json`
- `stats.json`

## Convenience Entry

For partial manual runs:

```bash
cd /Users/moinsky/.codex/skills/course-sync
./daily_sync.sh --collect-only
./daily_sync.sh --export-only
./daily_sync.sh --days 30
```

The full `./daily_sync.sh` flow still requires an agent-generated
`/tmp/minutes_judgments.json` before it writes Base.

## Done Response

Summarize in Chinese:

- How many candidate minutes were found.
- How many were written/updated, skipped, and failed.
- Skipped records and reasons.
- Written course records: name, year, category, importance, participant count.
- Exported files and output directory.
- Any authentication, permission, VC matching, or field-schema issues.

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
- `COURSE_SYNC_SITE_SYNC_URL` — website API, normally `https://wlbycuc.cn/api/integrations/courses/sync`
- `COURSE_SYNC_SITE_SYNC_TOKEN` — website integration token
- `COURSE_SYNC_EXCLUDED_TOKENS` — comma-separated minute tokens to exclude from site export

Base has two speaker fields:

- `主讲显示名`: text field, canonical for website export; supports values like
  `多人` and normalized aliases.
- `主讲人/主持人`: person field, only written when the speaker can be resolved to
  a Feishu contact.

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
./daily_sync.sh --check-env
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

The script no longer opens a browser. It uses a multi-strategy VC resolver:

1. Try `lark-cli vc +notes --minute-tokens` to resolve the meeting directly.
2. Enrich each token with `minutes minutes get` metadata (`note_id`, owner,
   create time, duration).
3. Search VC meetings by title, owner as organizer, owner as participant,
   current user as participant, and wide visible date windows with pagination.
4. Batch `vc +recording` for all candidate meeting IDs and require an exact
   `minute_token` match when possible.
5. If the recording edge is not visible, require an exact `meeting.note_id ==
   minute.note_id` match.
6. Only when both exact paths fail, emit `probable_matched` for very
   high-confidence metadata matches; otherwise keep `participant_count: null`.

`vc +notes --minute-tokens` requires the user OAuth scope
`minutes:minutes.artifacts:read`. If that scope is missing, the resolver records
the missing-scope error and falls back to the search/recording/note-id paths.

Permission handling is mandatory:

- If any lark-cli command returns `missing_scope`, do not silently treat the
  result as a final VC matching failure.
- Follow `lark-shared` split-flow auth: run
  `lark-cli auth login --scope "<missing_scope>" --no-wait --json`, generate and
  display a QR code with `lark-cli auth qrcode`, then stop and ask the user to
  finish authorization.
- After the user confirms authorization, complete the flow with
  `lark-cli auth login --device-code <device_code>` and rerun the participant
  resolver.
- Only use transcript/text interpretation after all available VC permission
  issues have been requested and the official VC resolver still cannot return a
  matched or probable matched meeting.

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
      "host_name": "actual course speaker display name",
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

For host/speaker fields, do not assume the Minutes owner is the course speaker.
Historical 25/26 records often used 天天 as owner while the actual speaker was
someone else. Resolve the speaker in this order:

1. Use the stable course-system rule for 世界文明史/文明史: speaker is 泠泠七
   unless the case is manually reviewed and corrected.
2. For other subjects, if the transcript has real speaker labels, use the dominant teacher speaker
   by speaking duration/character share. For研讨/互评/交流类课程, mark `多人`
   when two or more named speakers have substantial shares.
3. If the transcript is generic or unavailable, use VC participant evidence:
   host/cohost and long `in_meeting_duration` are auxiliary signals, not final
   proof when they conflict with transcript evidence.
4. Use owner_name only as a final fallback and explain that in reasoning. Do not
   create broad year/category mappings for other subjects, because teachers vary
   by cohort.

Set `host_name` to the resolved speaker. Keep `owner_name` as metadata about the
Minutes owner, not as the speaker unless they are the same person.

The apply step writes `host_name` into `主讲显示名`. It also writes the person
field only when `host_name` resolves to a Feishu contact. This keeps website
display stable even when the speaker is `多人` or a display alias such as `Luu🦌`
that needs normalization.

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

If `COURSE_SYNC_SITE_SYNC_URL` and `COURSE_SYNC_SITE_SYNC_TOKEN` are set,
`site_export.py` posts `courses.json` to the website after local export. Push
failure does not delete or rewrite the local export files.

Default output directory: `/tmp/site_export/`.

Expected files:

- `courses.json`
- `courses.md`
- `courses_index.json`
- `courses_latest.json`
- `stats.json`

For remote cron jobs that must update the website, use:

```bash
./daily_sync.sh --export-only --require-site-sync
```

This fails loudly if `COURSE_SYNC_SITE_SYNC_URL` or
`COURSE_SYNC_SITE_SYNC_TOKEN` is missing, or if the website API rejects the
payload. The full collection flow still requires a fresh agent-generated
`/tmp/minutes_judgments.json` before writing Base, so a safe unattended daily
job should usually export Base to the website rather than auto-approving new
minutes.

## Convenience Entry

For partial manual runs:

```bash
cd /Users/moinsky/.codex/skills/course-sync
./daily_sync.sh --collect-only
./daily_sync.sh --export-only --require-site-sync
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

#!/usr/bin/env bash
# daily_sync.sh — 每日自动同步飞书妙记到 Base + 导出给网站用
#
# 用法:
#   ./daily_sync.sh                  # 跑完整流程（collect → judge → write → export）
#   ./daily_sync.sh --collect-only  # 只收集候选（不写 Base、不导出）
#   ./daily_sync.sh --export-only   # 只从 Base 导出给网站
#   ./daily_sync.sh --days 7        # 自定义搜索窗口（默认 7 天）
#
# 适合 cron:
#   # 每天凌晨 3 点跑
#   0 3 * * * /path/to/daily_sync.sh >> /var/log/course-sync.log 2>&1
#
# 或者作为 Mavis / Mavis Skill 触发器 (course-sync.yaml prompt 末尾建议)

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd -P )"
cd "$SCRIPT_DIR"

DAYS=7
MODE="full"
for arg in "$@"; do
  case $arg in
    --collect-only) MODE="collect" ;;
    --export-only)  MODE="export"  ;;
    --days=*)       DAYS="${arg#*=}" ;;
    --days)         shift; DAYS="${1:-7}" ;;
    *)              echo "Unknown arg: $arg"; exit 2 ;;
  esac
done

export COURSE_SYNC_DAYS_BACK="$DAYS"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

if [[ "$MODE" == "full" || "$MODE" == "collect" ]]; then
  log "=== Step 1: 收集候选 ==="
  python3 scripts/course_sync_lark.py --collect-only

  log "=== Step 2: 抓参与人数 ==="
  python3 scripts/fetch_speakers_via_browser.py \
    --from-candidates /tmp/minutes_candidates.json
fi

if [[ "$MODE" == "full" ]]; then
  log "=== Step 3: Claude 判断 (需要人工或另一个 LLM) ==="
  log "    /tmp/minutes_candidates.json + /tmp/speaker_counts.json 已生成"
  log "    Claude 请用 course-sync.yaml 的 prompt 工作流完成判断"
  log "    输出: /tmp/minutes_judgments.json"
  log
  if [[ -f /tmp/minutes_judgments.json ]]; then
    log "    检测到 /tmp/minutes_judgments.json — 自动应用"
    python3 scripts/course_sync_lark.py --apply-judgments
  else
    log "    没有 judgments.json，跳过写 Base 步骤"
    log "    你可以稍后运行: python3 scripts/course_sync_lark.py --apply-judgments"
  fi
fi

if [[ "$MODE" == "full" || "$MODE" == "export" ]]; then
  log "=== Step 4: 导出给网站 ==="
  python3 scripts/site_export.py
fi

log "=== 完成 ==="

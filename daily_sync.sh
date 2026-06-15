#!/usr/bin/env bash
# daily_sync.sh — 每日自动同步飞书妙记到 Base + 导出给网站用
#
# 用法:
#   ./daily_sync.sh                  # 跑完整流程（collect → judge → write → export）
#   ./daily_sync.sh --collect-only  # 只收集候选（不写 Base、不导出）
#   ./daily_sync.sh --export-only   # 只从 Base 导出给网站
#   ./daily_sync.sh --days 7        # 自定义搜索窗口（默认 7 天）
#   ./daily_sync.sh --force-judgments  # 跳过 judgments 校验强制 apply（不推荐）
#
# 适合 cron:
#   # 每天凌晨 3 点跑
#   0 3 * * * /path/to/daily_sync.sh >> /var/log/course-sync.log 2>&1
#
# 关键设计:
#   - judgments.json 必须覆盖今天 candidates 全部 token，否则拒绝 apply
#     （避免旧的 judgments 直接套用造成的脏写）
#   - judgments.json 文件 mtime > candidates.json mtime，否则警告（防止套旧）
#   - mavis browser 不在线时跳过 Step 2，judgment 时不参考参与人数
#   - apply-judgments 仍可单独跑（不在此脚本内强制校验）

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd -P )"
cd "$SCRIPT_DIR"

DAYS=7
MODE="full"
FORCE_JUDGMENTS=0
for arg in "$@"; do
  case $arg in
    --collect-only)     MODE="collect" ;;
    --export-only)      MODE="export"  ;;
    --days=*)           DAYS="${arg#*=}" ;;
    --days)             shift; DAYS="${1:-7}" ;;
    --force-judgments)  FORCE_JUDGMENTS=1 ;;
    *)                  echo "Unknown arg: $arg"; exit 2 ;;
  esac
done

export COURSE_SYNC_DAYS_BACK="$DAYS"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

# ====== 工具函数：judgments 校验 ======
#
# 校验 /tmp/minutes_judgments.json 是否覆盖 /tmp/minutes_candidates.json 里的所有 token。
# 返回 0 表示完整覆盖，1 表示缺失。
judgments_cover_candidates() {
  local cand_file="$1" judge_file="$2"
  if [[ ! -f "$judge_file" ]]; then
    return 1
  fi
  if [[ ! -f "$cand_file" ]]; then
    log "  ⚠️ candidates 文件不存在: $cand_file"
    return 1
  fi

  python3 - << PYEOF
import json, sys
try:
    with open("$cand_file") as f:
        cand = json.load(f)
    with open("$judge_file") as f:
        judge = json.load(f)
except Exception as e:
    print(f"JSON 解析失败: {e}", file=sys.stderr)
    sys.exit(1)

cand_tokens = {c["token"] for c in cand.get("candidates", []) if c.get("token")}
judge_tokens = {j["token"] for j in judge.get("judgments", []) if j.get("token")}

missing = cand_tokens - judge_tokens
if missing:
    print(f"⚠️ judgments 缺失 {len(missing)} 个 token:", file=sys.stderr)
    for t in sorted(missing)[:10]:
        print(f"  - {t}", file=sys.stderr)
    sys.exit(1)

# 也检查反向：judgments 里的 token 是否都不在 candidates 里
extra = judge_tokens - cand_tokens
if extra:
    print(f"⚠️ judgments 多出 {len(extra)} 个 candidates 中不存在的 token (可能是上次的):", file=sys.stderr)
    for t in sorted(extra)[:10]:
        print(f"  - {t}", file=sys.stderr)
    # 不算错误，但 warn
    print("EXTRA_WARN")

sys.exit(0)
PYEOF
}

# ====== 工具函数：judgments mtime 检查 ======
#
# 防止套用几小时甚至几天前的 judgments。
judgments_is_fresh() {
  local cand_file="$1" judge_file="$2"
  python3 - << PYEOF
import os, sys
cand_mtime = os.path.getmtime("$cand_file")
judge_mtime = os.path.getmtime("$judge_file")
if judge_mtime < cand_mtime:
    age = cand_mtime - judge_mtime
    print(f"⚠️ judgments 比 candidates 旧 {int(age)} 秒", file=sys.stderr)
    sys.exit(1)
sys.exit(0)
PYEOF
}

# ====== 主流程 ======

SKIP_BROWSER=0

if [[ "$MODE" == "full" || "$MODE" == "collect" ]]; then
  log "=== Step 1: 收集候选 ==="
  python3 scripts/course_sync_lark.py --collect-only

  log "=== Step 1.5: 检查 mavis browser 连接 ==="
  if mavis browser status 2>&1 | grep -q "Native host: connected"; then
    log "  ✅ mavis browser 已连接"
  else
    log "  ⚠️ mavis browser 未连接 (Native host: not connected)"
    log "     Edge 扩展可能断开 — 跳过参与人数抓取"
    log "     judgment 时不参考参与人数，需要人工更谨慎"
    SKIP_BROWSER=1
  fi

  if [[ "$SKIP_BROWSER" == "0" ]]; then
    log "=== Step 2: 抓参与人数 ==="
    python3 scripts/fetch_speakers_via_browser.py \
      --from-candidates /tmp/minutes_candidates.json || \
      log "  ⚠️ 抓取失败（部分可能 None），可手动重试"
  fi
fi

if [[ "$MODE" == "full" ]]; then
  log "=== Step 3: Claude 判断 (需要人工或另一个 LLM) ==="
  log "    /tmp/minutes_candidates.json + /tmp/speaker_counts.json 已生成"
  log "    Claude 请按 course-sync.yaml 的 prompt 工作流完成判断"
  log "    输出: /tmp/minutes_judgments.json"
  log

  if [[ ! -f /tmp/minutes_judgments.json ]]; then
    log "  ⚠️ /tmp/minutes_judgments.json 不存在"
    log "     跳过写 Base 步骤。请人工生成后再跑: python3 scripts/course_sync_lark.py --apply-judgments"
  else
    log "  检测到 /tmp/minutes_judgments.json — 校验完整性..."

    if [[ "$FORCE_JUDGMENTS" == "1" ]]; then
      log "  ⚠️ --force-judgments 启用，跳过校验强制 apply"
    else
      # 1) judgments 必须覆盖 candidates 所有 token
      if ! judgments_cover_candidates /tmp/minutes_candidates.json /tmp/minutes_judgments.json; then
        log "  ❌ judgments 不完整（见上方错误）"
        log "     需要重新生成 /tmp/minutes_judgments.json"
        log "     跳过写 Base 步骤（避免脏写）"
        log "     或用 --force-judgments 强制 apply（不推荐）"
        SKIP_APPLY=1
      else
        # 2) judgments mtime 不应早于 candidates mtime
        if ! judgments_is_fresh /tmp/minutes_candidates.json /tmp/minutes_judgments.json; then
          log "  ⚠️ judgments 比 candidates 旧（可能套用了昨天的判断）"
          log "     继续 apply，但请人工确认这批判断对今天的 candidates 是有效的"
        fi
        log "  ✅ judgments 校验通过"
        log "    自动应用..."
        python3 scripts/course_sync_lark.py --apply-judgments
      fi
    fi
  fi
fi

if [[ "$MODE" == "full" || "$MODE" == "export" ]]; then
  log "=== Step 4: 导出给网站 ==="
  python3 scripts/site_export.py
fi

log "=== 完成 ==="

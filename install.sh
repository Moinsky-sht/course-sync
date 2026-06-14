#!/usr/bin/env bash
# install.sh — 在 mavis agent 的 skills 目录中安装 course-sync skill
#
# 用法:
#   ./install.sh              # 安装到当前用户 ~/.mavis/agents/mavis/skills/course-sync
#   ./install.sh /path/to/mavis   # 安装到指定 mavis root
#
# 之后:
#   mavis skill list  应该能看到 "course-sync"

set -euo pipefail

MAVIS_ROOT="${1:-$HOME/.mavis}"
SKILL_NAME="course-sync"
TARGET="$MAVIS_ROOT/agents/mavis/skills/$SKILL_NAME"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd -P )"

if [[ -d "$TARGET" ]]; then
  echo "❌ $TARGET 已存在，删除后重试或手动更新"
  exit 1
fi

echo "📦 安装到 $TARGET"
mkdir -p "$TARGET/scripts" "$TARGET/references" "$TARGET/assets"

cp SKILL.md "$TARGET/"
cp daily_sync.sh "$TARGET/"
chmod +x "$TARGET/daily_sync.sh"

cp scripts/course_sync_lark.py "$TARGET/scripts/"
cp scripts/fetch_speakers_via_browser.py "$TARGET/scripts/"
cp scripts/site_export.py "$TARGET/scripts/"
chmod +x "$TARGET/scripts/"*.py

cp references/README.md "$TARGET/references/"
cp references/SKILL.md "$TARGET/references/"
cp references/minutes_judgments.example.json "$TARGET/references/"

echo "✅ 安装完成"
echo
echo "验证:"
mavis skill list 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    for s in d['skills']:
        if s['name'] == 'course-sync':
            print(f\"  ✅ {s['name']} 已注册\")
            print(f\"     位置: {s['location']}\")
            print(f\"     描述: {s.get('description', '')[:80]}...\")
            break
    else:
        print('  ❌ 找不到 course-sync')
except Exception as e:
    print(f'  (验证失败: {e})')
" 2>/dev/null || echo "  (跳过验证 - mavis 命令不可用)"

echo
echo "下一步:"
echo "  1. 配置 cron: 0 23 * * * $TARGET/daily_sync.sh >> /var/log/cs.log 2>&1"
echo "  2. 或在 Mavis 对话框里说: '同步课程' / '每日课程同步'"

#!/usr/bin/env bash
# install.sh — 安装 / 更新 course-sync skill
#
# 用法:
#   ./install.sh                    # 安装到当前用户 ~/.codex/skills/course-sync
#   ./install.sh /path/to/skills     # 安装到指定 skills 根目录
#
# 之后:
#   codex / agent 重启后应能看到 "course-sync"

set -euo pipefail

SKILLS_ROOT="${1:-${CODEX_HOME:-$HOME/.codex}/skills}"
SKILL_NAME="course-sync"
TARGET="$SKILLS_ROOT/$SKILL_NAME"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd -P )"

if [[ -d "$TARGET" ]]; then
  echo "🔄 更新 $TARGET"
else
  echo "📦 安装到 $TARGET"
fi

mkdir -p "$TARGET/scripts"

cp "$SCRIPT_DIR/SKILL.md" "$TARGET/"
cp "$SCRIPT_DIR/README.md" "$TARGET/"
cp "$SCRIPT_DIR/course-sync.yaml" "$TARGET/"
cp "$SCRIPT_DIR/daily_sync.sh" "$TARGET/"
cp "$SCRIPT_DIR/.env.example" "$TARGET/"
chmod +x "$TARGET/daily_sync.sh"

cp "$SCRIPT_DIR/course_sync_lark.py" "$TARGET/"
cp "$SCRIPT_DIR/fetch_speakers_via_browser.py" "$TARGET/"
cp "$SCRIPT_DIR/site_export.py" "$TARGET/"
cp "$SCRIPT_DIR/sync_feishu_minutes.py" "$TARGET/"
cp "$SCRIPT_DIR/minutes_judgments.example.json" "$TARGET/"

cp "$SCRIPT_DIR/course_sync_lark.py" "$TARGET/scripts/"
cp "$SCRIPT_DIR/fetch_speakers_via_browser.py" "$TARGET/scripts/"
cp "$SCRIPT_DIR/site_export.py" "$TARGET/scripts/"
chmod +x "$TARGET/scripts/"*.py

echo "✅ 安装完成"
echo
echo "下一步:"
echo "  1. cp $TARGET/.env.example $TARGET/.env"
echo "  2. 在 $TARGET/.env 写入 COURSE_SYNC_SITE_SYNC_TOKEN"
echo "  3. 检查: cd $TARGET && ./daily_sync.sh --check-env --require-site-sync"
echo "  4. 定时: cd $TARGET && ./daily_sync.sh --export-only --require-site-sync"

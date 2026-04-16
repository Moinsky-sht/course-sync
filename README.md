# course-sync

Claude Code / OpenClaw Skill：自动同步飞书妙记到 Base 课程表格。

## 功能

- 自动发现 `wlbyzcky.feishu.cn` 域名下最近 7 天的新妙记
- 抓取每条妙记的参与人数、时长、日期等数据
- **Claude 直接判断**该不该录入，并自动分类（不需要外部 LLM API key）
- 自动写入飞书 Bitable Base 表格

## 触发词

- `同步课程`
- `同步妙记`
- `/course-sync`
- 或直接发送 Base 链接：`https://wlbyzcky.feishu.cn/wiki/TGGww8zijiQF2xk77tGcLYFtnW1`

## 脚本用法

```bash
# 检查环境（lark-cli + cookies）
python3 sync_feishu_minutes.py --check-env

# 仅收集候选数据（输出 /tmp/minutes_candidates.json）
python3 sync_feishu_minutes.py --collect-only

# 应用判断结果写入 Base（读取 /tmp/minutes_judgments.json）
python3 sync_feishu_minutes.py --apply-judgments
```

## 环境依赖

1. **lark-cli**：脚本会自动尝试安装，如失败请手动从 [Release](https://github.com/larksuite/lark-cli/releases) 下载
2. **Playwright cookies**：需将 `/tmp/feishu_cookies.json` 准备好（通过浏览器登录飞书后导出）
3. **lark-cli 认证**：执行 `lark-cli auth login`

## 安装到 Claude Code

将 `course-sync.yaml` 复制到 `~/.claude/skills/` 目录：

```bash
cp course-sync.yaml ~/.claude/skills/
```

然后在 Claude Code 中输入 `同步课程` 即可触发。

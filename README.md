# course-sync

Claude Code / OpenClaw Skill：自动同步飞书妙记到飞书 Base 课程表格，为个人网站每日更新课程服务。

> 2026-06 重写 + 升级：lark-cli 替代 Playwright/cookies，VC API 获取参会峰值人数，每日定时 + 站点导出。

## 概述

将飞书妙记的元数据（标题、日期、时长、所有者、妙记链接、参与人数）**按你的判断规则**写入飞书 Base 课程表格，再导出为 JSON / Markdown 供个人网站消费。

适用场景：中国传媒大学考研相关课程的归档管理 + 个人网站每日更新（飞书妙记 → 飞书 Base → 静态网站 JSON/MD）。

## 触发词

- `同步课程`
- `同步妙记`
- `每日课程同步`
- `拉取最新课程`
- `/course-sync`
- 或直接发送 Base 链接：`https://wlbyzcky.feishu.cn/wiki/TGGww8zijiQF2xk77tGcLYFtnW1`

## 完整流程

```
[1] lark-cli minutes +search
        ↓ 3 路并集：owner=me / participant=me / all-shared
[2] lark-cli VC API (多策略映射 meeting：minute token / note_id / recording + 参会峰值人数)
        ↓
[3] Claude 判断（人工或 LLM）
        ↓ 生成 judgments.json
[4] lark-cli base +record-upsert
        ↓ 写入/更新 Base
[5] site_export.py
        ↓ 输出 courses.json / courses.md / courses_index.json / courses_latest.json / stats.json
[6] 你网站 fetch 这些 JSON 渲染
```

## 用法

### 一次性手动（交互）

直接对 Claude Code / Mavis 说"同步课程"，按 `course-sync.yaml` 的 6 步 prompt 工作流跑完整套。

### 命令行（手动）

```bash
# 完整流程（含 Claude 判断——需要 LLM 介入，或直接用上一轮的 judgments.json）
./daily_sync.sh

# 只跑部分步骤
./daily_sync.sh --check-env
./daily_sync.sh --collect-only
./daily_sync.sh --export-only --require-site-sync
./daily_sync.sh --days 30
```

### 每日自动（cron / GitHub Actions）

**方案 A: 本机 cron**
```cron
# 每天凌晨 3 点
0 3 * * * /path/to/course-sync/daily_sync.sh >> /var/log/course-sync.log 2>&1
```

**方案 B: GitHub Actions**（推荐 — 不用本机常驻）
- 把仓库 fork 到你自己的 GitHub
- 在 Settings → Secrets 添加：
  - `LARK_APP_ID`, `LARK_APP_SECRET`（飞书 app 凭证）
  - `LARK_USER_TOKEN`（user_access_token，每 2 小时过期，需要外部 refresh）
  - `COURSE_SYNC_BASE_TOKEN`, `COURSE_SYNC_TABLE_ID`
- 启用 `.github/workflows/daily.yml`
- 自动每天 UTC 19:00 跑（北京时间凌晨 3 点）
- 导出的 JSON 自动 commit 到 `gh-pages` 分支 → 你的网站可 `https://<user>.github.io/course-sync/courses.json`

⚠️ **GitHub Actions 注意**：参会人数现在来自飞书 VC API，不依赖浏览器；但 Actions 仍需要可用的 user token，并且该 user 需要有会议记录和录制关系的读取权限。

**方案 C: 远程服务器 cron**（你刚绑的 `oldmac`）
```bash
# 装好 lark-cli，并完成飞书 OAuth
ssh oldmac 'crontab -e'
# 加。该模式从 Base 导出并推送网站，不会重新写 Base：
0 3 * * * cd /home/ubuntu/course-sync && COURSE_SYNC_SITE_SYNC_URL=https://wlbycuc.cn/api/integrations/courses/sync COURSE_SYNC_SITE_SYNC_TOKEN=*** ./daily_sync.sh --export-only --require-site-sync >> /var/log/cs.log 2>&1
```
服务器有完整 lark-cli 环境，导出的 JSON 通过 nginx / GitHub Pages / 你网站后端去消费。

跨机器定时建议先跑：

```bash
cd /path/to/course-sync
./daily_sync.sh --check-env
```

如果只需要网站每日更新，推荐定时跑 `--export-only --require-site-sync`。如果要自动发现新妙记并写入 Base，仍需要 Codex/LLM 按本 skill 生成新的 `/tmp/minutes_judgments.json` 后再执行 `--apply-judgments`，避免把小会、一对一或内部会议误录入。

## 文件说明

| 文件 | 用途 |
|------|------|
| `course_sync_lark.py` | 主流程脚本（lark-cli 替代 Playwright/cookies） |
| `fetch_speakers_via_browser.py` | 获取参会统计（多策略 lark-cli VC resolver，文件名保留用于兼容旧流程） |
| `site_export.py` | 导出 Base → JSON / Markdown 给网站 |
| `daily_sync.sh` | 每日 sync 入口 shell 脚本 |
| `.github/workflows/daily.yml` | GitHub Actions 每日模板 |
| `course-sync.yaml` | OpenClaw / Mavis skill 定义（触发词 + prompt） |
| `minutes_judgments.example.json` | judgment 数据样例 |
| `sync_feishu_minutes.py` | **旧版**（已弃用，仅作参考） |
| `README.md` | 本文档 |

## 环境依赖

1. **lark-cli**（npm 全局安装）：
   ```bash
   npm i -g lark-cli
   lark-cli config init  # 完成飞书 OAuth 认证
   ```

2. **飞书权限要求**：
   - user 身份能搜妙记（`wlbyzcky.feishu.cn` 域）
   - user 身份能读取 VC 会议记录、录制关系和参会人列表
   - user 身份对目标 Base 表有读、写、创建记录权限

## 环境变量

| 变量 | 默认 | 用途 |
|------|------|------|
| `COURSE_SYNC_BASE_TOKEN` | `PK5BbGQx4aoeres9oBCchWKPnfd` | 飞书 Base app_token |
| `COURSE_SYNC_TABLE_ID` | `tblWTN8jkeExIFa0` | Base 表 ID |
| `COURSE_SYNC_DAYS_BACK` | `7` | 搜索窗口（天） |
| `COURSE_SYNC_HOST` | `wlbyzcky.feishu.cn` | 妙记域名 |
| `COURSE_SYNC_OUTPUT_DIR` | `/tmp/site_export` | site_export 输出目录 |
| `COURSE_SYNC_SITE_SYNC_URL` | 空 | 网站课程同步 API，例如 `https://wlbycuc.cn/api/integrations/courses/sync` |
| `COURSE_SYNC_SITE_SYNC_TOKEN` | 空 | 网站集成接口 Token |
| `COURSE_SYNC_EXCLUDED_TOKENS` | 已确认排除的一对一 token | 导出到网站时排除的妙记 token，逗号分隔 |

## 主讲人口径

主讲人不等于妙记 owner。25/26 历史课程里，大量妙记由天天持有或发起，
但真实授课人需要来自逐字稿实名说话人、VC host/cohost、参会时长等证据。

新增或修复课程时按以下顺序处理：

- 世界文明史/文明史按稳定主讲人口径处理为泠泠七，除非人工复核后明确改正。
- 其他学科逐字稿有实名说话人时，按发言时长/字数占比确认主讲人；研讨、互评、交流类多人发言明显时标为“多人”。
- 逐字稿只有 `Speaker 1` 等泛称时，参考 VC host/cohost 和参会时长，但不要把妙记 owner 自动当主讲人。
- 其他学科老师会随年份变化，不做年份/学科硬映射。
- 仍无法确认时保留原主讲人或标记待核实，并在 reasoning 中说明证据不足。

新增 judgment 时应写 `host_name` 表示真实主讲人，`owner_name` 只表示妙记所有者。写入 Base 时会同步写入：

- `主讲显示名`：网站和导出优先使用的文本字段，支持 `多人` 这种非联系人值。
- `主讲人/主持人`：Base 协作使用的人员字段；只有能解析到飞书联系人时才写入。

## Base 表 schema

`课程名称, 参与人数, 重要程度, 上课日期, 课程形式, 课程类别, 会议时长, 主讲显示名, 主讲人/主持人, 妙记链接, 课程年份`

| 字段 | 类型 | 说明 |
|------|------|------|
| 课程名称 | string | 主键之一 |
| 参与人数 | number | 来自 VC meeting `participant_count`，即参会峰值人数 |
| 重要程度 | enum | 必刷课程 / 常规课程 |
| 上课日期 | date | YYYY-MM-DD |
| 课程形式 | enum | 讲解课 / 研讨课 / ... |
| 课程类别 | enum | 公开课、班会课、报考分析课、人类简史、世界文明史、中国文学史、哲学导论、媒介与社会、艺术学概论、百日押题课、模考讲解课、材料评议课、复试讲解课、其他课程、783主题写作、785艺术作品评论、783/785通用 |
| 会议时长 | string | e.g. "1小时13分" |
| 主讲显示名 | string | 网站展示主讲人，优先级高于人员字段，可填“多人” |
| 主讲人/主持人 | person | {id, name} |
| 妙记链接 | string | markdown `[url](url)` |
| 课程年份 | enum | 25考研 / 26考研 / 27考研 / ... |

## 站点导出格式

`site_export.py` 输出：

```
{output_dir}/
├── courses.json           # 完整结构化数据（含所有元数据）
├── courses.md             # Markdown 表格
├── courses_index.json     # {year: {category: [courses]}}
├── courses_latest.json    # 仅最近 7 天的课程
└── stats.json             # 统计：总数/按年份/按类别/按重要程度
```

如果配置了 `COURSE_SYNC_SITE_SYNC_URL` 和 `COURSE_SYNC_SITE_SYNC_TOKEN`，
导出完成后会把 `courses.json` 推送到网站本地课程库。推送失败只输出错误，
本地导出文件仍然保留。

定时任务建议带上 `--require-site-sync`，这样未配置网站 API 或推送失败时会退出非 0，便于 cron 监控发现问题。

### 你的网站怎么消费

**静态站点 (Hugo / Jekyll / Next.js / Astro):**
```bash
# 在构建时拉
curl -o data/courses.json https://your-server/courses.json
# 或者直接把 /tmp/site_export/ 软链到站点 data 目录
```

**Next.js ISR / SSR:**
```typescript
export async function getStaticProps() {
  const r = await fetch('https://your-server/courses.json', {next: {revalidate: 3600}});
  return {props: {courses: (await r.json()).courses}};
}
```

**纯 HTML / Vanilla JS:**
```html
<script>
fetch('/courses.json').then(r => r.json()).then(d => {
  d.courses.forEach(c => {
    // 渲染 c.name, c.date, c.participant_count, c.url ...
  });
});
</script>
```

## 历史与变更

- **v5（2026-06-16）**：
  - 新增：Base `主讲显示名` 文本字段作为网站主讲人展示来源，解决 `多人` 和联系人别名无法落入人员字段的问题
  - 新增：`daily_sync.sh --check-env` 和 `site_export.py --require-site-sync`，方便其他电脑/服务器做稳定定时任务
  - 强化：参会人数 resolver 改为多策略映射，优先 `vc +notes --minute-tokens`，再用 VC 搜索分页 + recording 精确反查 + note_id 精确反查
  - 新增：输出 `match_evidence`、`participants`、`host_user`、`top_candidates` 和缺失 scope 诊断，方便判断为什么某条妙记无法映射
  - 明确：文本/逐字稿只用于最终人工判断，不作为 VC 参会人数主来源
- **v4（2026-06-16）**：
  - 替换：参与人数从浏览器 DOM 抓取改为 lark-cli VC API
  - 新增：用会议录制关系校验 meeting_id 与 minute token，降低误匹配
  - 明确：`participant_count` 作为参会峰值人数，用于识别一对一/小会议/大课
- **v3（2026-06-14 第三次）**：
  - 新增 `daily_sync.sh` 入口
  - 新增 `site_export.py` 站点导出
  - 新增 `.github/workflows/daily.yml` Actions 模板
  - 更新 README 强调"个人网站每日更新"场景
- **v2（2026-06-14 第二次）**：
  - 修复：参与人数抓取（不是发言人数）— DOM avatar group + "+N" 溢出
  - 新增：3 路并集搜索（owner/participant/shared）
- **v1（2026-06-14 第一次）**：
  - 重写：lark-cli 替代 Playwright/cookies
- **v0（2026-04-16）**：初版 Playwright + cookies，已弃用

## 已知限制

1. **少数妙记可能匹配不到 VC 会议** — 例如它是上传音视频生成的妙记、VC 录制关系对当前 user 不可见，或飞书搜索索引不返回对应会议；这些记录会保留 `participant_count: null`，判断时应更保守。
2. **VC 参会统计需要额外权限** — 当前 user 需要能读取会议记录、录制关系和 participants；`vc +notes --minute-tokens` 还需要 `minutes:minutes.artifacts:read`，否则会自动降级到搜索/recording/note_id 路径。
3. **转录文本需要额外 scope** — `minutes:minutes.transcript:export` 需要在飞书开放平台后台手动配置。

## 权限缺失处理

如果参会统计或妙记产物读取阶段返回 `missing_scope`，不要把它当成最终失败，也不要直接改用逐字稿文本判断。先发起 user OAuth 增量授权：

```bash
lark-cli auth login --scope "<missing_scope>" --no-wait --json
lark-cli auth qrcode "<verification_url>" --output auth.png
# 用户授权完成后:
lark-cli auth login --device-code "<device_code>"
python3 fetch_speakers_via_browser.py --from-candidates /tmp/minutes_candidates.json
```

只有权限补齐后仍无法从 VC API 得到 `matched` / `probable_matched`，才进入妙记文本或逐字稿做人工兜底判断。

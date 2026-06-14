# course-sync

Claude Code / OpenClaw / Mavis Skill：自动同步飞书妙记到飞书 Base 课程表格，为个人网站每日更新课程服务。

> 2026-06 重写 + 升级：lark-cli 替代 Playwright/cookies，mavis browser 抓参与人数，每日定时 + 站点导出。

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
[2] mavis browser (抓参与人数 = 头像 + "+N" 溢出)
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
./daily_sync.sh --collect-only
./daily_sync.sh --export-only
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

⚠️ **GitHub Actions 限制**：GitHub Actions 跑在 Linux 容器里，**没法用 mavis browser**（mavis browser 需要 macOS 上的 Chrome 扩展）。所以 Actions 跑会**跳过参与人数**字段，需要你**在本地或远程服务器跑完整流程**。

**方案 C: 远程服务器 cron**（你刚绑的 `oldmac`）
```bash
# 装好 lark-cli + mavis browser
ssh oldmac 'crontab -e'
# 加:
0 3 * * * /home/ubuntu/course-sync/daily_sync.sh --output /home/ubuntu/site/courses >> /var/log/cs.log 2>&1
```
服务器有完整环境（mavis browser 可用），导出的 JSON 通过 nginx / GitHub Pages / 你网站后端去消费。

## 文件说明

| 文件 | 用途 |
|------|------|
| `course_sync_lark.py` | 主流程脚本（lark-cli 替代 Playwright/cookies） |
| `fetch_speakers_via_browser.py` | 抓参与人数（mavis browser） |
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

2. **mavis browser**（抓参与人数必需）：
   ```bash
   mavis browser install
   # 然后在 Chrome 里加载 /Users/<you>/.mavis/browser-extension 扩展
   ```

3. **飞书权限要求**：
   - user 身份能搜妙记（`wlbyzcky.feishu.cn` 域）
   - user 身份对目标 Base 表有读、写、创建记录权限

## 环境变量

| 变量 | 默认 | 用途 |
|------|------|------|
| `COURSE_SYNC_BASE_TOKEN` | `PK5BbGQx4aoeres9oBCchWKPnfd` | 飞书 Base app_token |
| `COURSE_SYNC_TABLE_ID` | `tblWTN8jkeExIFa0` | Base 表 ID |
| `COURSE_SYNC_DAYS_BACK` | `7` | 搜索窗口（天） |
| `COURSE_SYNC_HOST` | `wlbyzcky.feishu.cn` | 妙记域名 |
| `COURSE_SYNC_OUTPUT_DIR` | `/tmp/site_export` | site_export 输出目录 |
| `COURSE_SYNC_BROWSER_TOOL` | `mavis browser tool` | 浏览器自动化 CLI |

## Base 表 schema

`课程名称, 参与人数, 重要程度, 上课日期, 课程形式, 课程类别, 会议时长, 主讲人/主持人, 妙记链接, 课程年份`

| 字段 | 类型 | 说明 |
|------|------|------|
| 课程名称 | string | 主键之一 |
| 参与人数 | number | 来自详情页 avatar group + 溢出 "+N" 文本 |
| 重要程度 | enum | 必刷课程 / 常规课程 |
| 上课日期 | date | YYYY-MM-DD |
| 课程形式 | enum | 讲解课 / 研讨课 / ... |
| 课程类别 | enum | 公开课、班会课、报考分析课、人类简史、世界文明史、中国文学史、哲学导论、媒介与社会、艺术学概论、百日押题课、模考讲解课、材料评议课、复试讲解课、其他课程、783主题写作、785艺术作品评论、783/785通用 |
| 会议时长 | string | e.g. "1小时13分" |
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

1. **API 拿不到参与人数** — 必须通过浏览器抓，GitHub Actions 跑不了
2. **转录文本需要额外 scope** — `minutes:minutes.transcript:export` 需要在飞书开放平台后台手动配置
3. **mavis browser 需用户已登录飞书** — 桥接只传递 cookies/SSO，不替你登录
4. **GitHub Actions 跑不全流程** — 缺 mavis browser，要么放弃参与人数，要么远程服务器 cron

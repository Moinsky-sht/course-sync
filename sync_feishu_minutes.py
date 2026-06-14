#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动同步飞书妙记到 Base 表格（数据收集 + 执行引擎）

用法：
  python3 sync_feishu_minutes.py --collect-only
    # 收集候选数据，输出 /tmp/minutes_candidates.json

  python3 sync_feishu_minutes.py --apply-judgments
    # 读取 /tmp/minutes_judgments.json，写入 Base

  python3 sync_feishu_minutes.py --check-env
    # 仅检查环境依赖（lark-cli + cookies）
"""

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright

# ==================== 配置 ====================
BASE_TOKEN = "PK5BbGQx4aoeres9oBCchWKPnfd"
TABLE_ID = "tblWTN8jkeExIFa0"
COOKIES_FILE = "/tmp/feishu_cookies.json"
DAYS_BACK = 7
HOST = "wlbyzcky.feishu.cn"
CANDIDATES_FILE = "/tmp/minutes_candidates.json"
JUDGMENTS_FILE = "/tmp/minutes_judgments.json"

JS_PARTICIPANT_COUNT = """() => {
  const container = document.querySelector('.larkw-web-header-caption-content');
  if (!container) return null;
  const imgs = container.querySelectorAll('img');
  const plusMatch = container.textContent.match(/\\+(\\d+)/);
  return plusMatch ? imgs.length + parseInt(plusMatch[1]) : imgs.length;
}"""

# ==================== 工具函数 ====================

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def run_cmd(cmd, timeout=30, check=True):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        return None
    return result


def run_lark_cli(*args, timeout=30):
    cmd = ["lark-cli"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    for output in (result.stdout.strip(), result.stderr.strip()):
        if output:
            try:
                return json.loads(output)
            except json.JSONDecodeError:
                pass
    return {"_raw_stdout": result.stdout.strip(), "_raw_stderr": result.stderr.strip()}


def ensure_lark_cli():
    """确保 lark-cli 已安装，如未安装则自动下载"""
    result = run_cmd(["lark-cli", "--version"], check=False, timeout=10)
    if result and result.returncode == 0:
        return True

    log("⚠️ lark-cli 未安装，尝试自动安装...")
    system = platform.system().lower()
    machine = platform.machine().lower()

    base_url = "https://github.com/larksuite/lark-cli/releases/latest/download"
    if system == "darwin":
        if "arm" in machine or "aarch64" in machine:
            filename = "lark-cli_darwin_arm64.tar.gz"
        else:
            filename = "lark-cli_darwin_amd64.tar.gz"
    elif system == "linux":
        if "arm" in machine or "aarch64" in machine:
            filename = "lark-cli_linux_arm64.tar.gz"
        else:
            filename = "lark-cli_linux_amd64.tar.gz"
    elif system == "windows":
        filename = "lark-cli_windows_amd64.zip"
    else:
        log(f"❌ 不支持自动安装的平台: {system} {machine}")
        return False

    download_url = f"{base_url}/{filename}"
    install_dir = os.path.expanduser("~/.local/bin")
    os.makedirs(install_dir, exist_ok=True)

    try:
        log(f"   正在下载 {download_url} ...")
        tmp_path = os.path.join(tempfile.gettempdir(), filename)
        urllib.request.urlretrieve(download_url, tmp_path)

        if filename.endswith(".tar.gz"):
            run_cmd(["tar", "-xzf", tmp_path, "-C", install_dir])
        elif filename.endswith(".zip"):
            import zipfile
            with zipfile.ZipFile(tmp_path, 'r') as z:
                z.extractall(install_dir)

        lark_path = os.path.join(install_dir, "lark-cli")
        if os.path.exists(lark_path):
            os.chmod(lark_path, 0o755)

        if install_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = install_dir + os.pathsep + os.environ.get("PATH", "")

        result = run_cmd(["lark-cli", "--version"], check=False, timeout=10)
        if result and result.returncode == 0:
            log(f"✅ lark-cli 安装成功")
            return True
        else:
            log("❌ lark-cli 安装后验证失败")
            return False
    except Exception as e:
        log(f"❌ lark-cli 自动安装失败: {e}")
        return False


def check_lark_auth():
    resp = run_lark_cli("api", "GET", "/open-apis/authen/v1/user_info")
    if resp.get("code") == 0 or resp.get("ok"):
        return True
    return False


def export_feishu_cookies():
    """通过 Playwright 自动导出飞书 cookies"""
    log("🍪 尝试自动导出飞书 cookies...")
    log("   将打开浏览器窗口，请在登录 wlbyzcky.feishu.cn 后关闭浏览器或等待 60 秒")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            page.goto(f"https://{HOST}/minutes/me")
            # 等待用户登录完成（通过检测页面是否出现 Minutes 内容来判断）
            try:
                page.wait_for_selector('a[href*="/minutes/"]', timeout=60000)
                log("   检测到已登录，正在导出 cookies...")
            except Exception:
                log("   等待超时，按当前状态导出 cookies...")
            cookies = context.cookies()
            with open(COOKIES_FILE, "w") as f:
                json.dump({"cookies": cookies}, f)
            browser.close()
        log(f"✅ Cookies 已保存到 {COOKIES_FILE}")
        return True
    except Exception as e:
        log(f"❌ Cookie 导出失败: {e}")
        return False


def ensure_env():
    """确保环境就绪，返回 (lark_ok, cookie_ok)"""
    lark_ok = False
    cookie_ok = os.path.exists(COOKIES_FILE)

    if ensure_lark_cli():
        if check_lark_auth():
            lark_ok = True
        else:
            log("❌ lark-cli 未认证，请执行: lark-cli auth login")
    else:
        log("❌ lark-cli 不可用")

    if not cookie_ok:
        log(f"❌ Cookie 文件不存在: {COOKIES_FILE}")
        # 在交互式环境下尝试自动导出
        if sys.stdin.isatty():
            if export_feishu_cookies():
                cookie_ok = True
        else:
            log("   非交互式环境，无法自动打开浏览器。请手动导出 cookies 或重新运行本脚本。")

    return lark_ok, cookie_ok


def get_base_records():
    """获取 Base 中所有现有记录，返回 {token: record_id} 和 {name: record_id}"""
    offset = 0
    limit = 100
    token_map = {}
    name_map = {}
    total = 0
    while True:
        resp = run_lark_cli(
            "base", "+record-list",
            "--base-token", BASE_TOKEN,
            "--table-id", TABLE_ID,
            "--limit", str(limit),
            "--offset", str(offset),
        )
        if not resp.get("ok"):
            log(f"获取 Base 记录失败: {resp}")
            break
        data = resp["data"]
        fields = data["fields"]
        name_idx = fields.index("课程名称")
        link_idx = fields.index("妙记链接")
        for i, row in enumerate(data["data"]):
            rec_id = data["record_id_list"][i]
            name = row[name_idx]
            link_val = row[link_idx]
            if isinstance(link_val, list) and len(link_val) > 0:
                link = link_val[0].get("link", "") if isinstance(link_val[0], dict) else str(link_val[0])
            else:
                link = str(link_val) if link_val else ""
            name_map[name] = rec_id
            m = re.search(r"minutes/([a-z0-9]+)", link)
            if m:
                token_map[m.group(1)] = rec_id
        total += len(data["data"])
        if not data["has_more"]:
            break
        offset += limit
    log(f"Base 现有记录: {total} 条")
    return token_map, name_map


def fetch_recent_minutes(days_back=DAYS_BACK):
    """通过内部 API 获取最近 N 天的所有 wlbyzcky 妙记列表"""
    cutoff = datetime.now() - timedelta(days=days_back)
    cutoff_ms = int(cutoff.timestamp() * 1000)

    with open(COOKIES_FILE) as f:
        cookie_data = json.load(f)
    cookies = cookie_data.get("cookies", cookie_data)

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1470, "height": 956})
        context.add_cookies(cookies)
        page = context.new_page()

        page.goto(f"https://{HOST}/minutes/me", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)

        timestamp = "0"
        has_more = True
        page_num = 0
        while has_more and page_num < 50:
            url = (
                f"https://{HOST}/minutes/api/space/list"
                f"?size=20&space_name=2&rank=1&asc=false"
                f"&note_info=true&language=en_us&timestamp={timestamp}"
            )
            try:
                resp = page.evaluate(
                    f"""async () => {{
                        const r = await fetch('{url}', {{ credentials: 'include' }});
                        return await r.json();
                    }}"""
                )
            except Exception as e:
                log(f"API 请求失败: {e}")
                break

            data = resp.get("data", {})
            items = data.get("list", [])
            has_more = data.get("has_more", False)

            for item in items:
                start_time = item.get("start_time", 0)
                url_field = item.get("url", "")
                if HOST not in url_field:
                    continue
                if start_time < cutoff_ms:
                    has_more = False
                    break
                results.append(item)

            if items and has_more:
                timestamp = str(items[-1].get("start_time", timestamp))
            else:
                break
            page_num += 1

        browser.close()

    log(f"最近 {days_back} 天内发现 {len(results)} 条 wlbyzcky 妙记")
    return results


def format_duration(ms):
    total_seconds = ms // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours > 0 and minutes > 0:
        return f"{hours}小时{minutes}分"
    elif hours > 0:
        return f"{hours}小时"
    else:
        return f"{minutes}分"


def format_date(timestamp_ms):
    dt = datetime.fromtimestamp(timestamp_ms / 1000)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def get_participant_count(page, token):
    url = f"https://{HOST}/minutes/{token}"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(1800)
        return page.evaluate(JS_PARTICIPANT_COUNT)
    except Exception as e:
        log(f"  抓取参与人数失败 {token}: {e}")
        return None


def infer_year(timestamp_ms):
    year = datetime.fromtimestamp(timestamp_ms / 1000).year
    mapping = {2023: "24考研", 2024: "25考研", 2025: "26考研", 2026: "27考研", 2027: "28考研"}
    return mapping.get(year, f"{year - 1999}考研")


def collect_candidates():
    """收集候选数据，输出 JSON"""
    token_map, name_map = get_base_records()
    recent_items = fetch_recent_minutes(DAYS_BACK)

    new_items = [item for item in recent_items if item.get("object_token") and item["object_token"] not in token_map]
    log(f"其中 {len(new_items)} 条尚未录入 Base")

    if not new_items:
        with open(CANDIDATES_FILE, "w") as f:
            json.dump({"candidates": [], "message": "没有新妙记"}, f, ensure_ascii=False, indent=2)
        log(f"已输出空候选列表到 {CANDIDATES_FILE}")
        return []

    candidates = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1470, "height": 956})
        with open(COOKIES_FILE) as f:
            cookie_data = json.load(f)
        context.add_cookies(cookie_data.get("cookies", cookie_data))
        page = context.new_page()

        for idx, item in enumerate(new_items, 1):
            token = item["object_token"]
            raw_name = item.get("topic", "未命名妙记")
            url = item.get("url", "")
            start_time = item.get("start_time", 0)
            duration_ms = item.get("duration", 0)
            owner = item.get("owner_name", "未知")

            log(f"[{idx}/{len(new_items)}] 抓取数据: {raw_name}")
            count = get_participant_count(page, token)

            candidates.append({
                "token": token,
                "raw_name": raw_name,
                "url": url,
                "date": format_date(start_time),
                "year_hint": infer_year(start_time),
                "duration": format_duration(duration_ms),
                "owner": owner,
                "participant_count": count,
            })

        browser.close()

    with open(CANDIDATES_FILE, "w") as f:
        json.dump({"candidates": candidates}, f, ensure_ascii=False, indent=2)

    log(f"已输出 {len(candidates)} 条候选数据到 {CANDIDATES_FILE}")
    return candidates


def apply_judgments():
    """读取 judgments JSON，写入 Base"""
    if not os.path.exists(JUDGMENTS_FILE):
        log(f"❌ 找不到判断文件: {JUDGMENTS_FILE}")
        sys.exit(1)

    with open(JUDGMENTS_FILE) as f:
        data = json.load(f)
    judgments = data.get("judgments", [])

    if not judgments:
        log("没有需要写入的判断结果")
        return

    token_map, name_map = get_base_records()
    success_count = 0
    skip_count = 0
    error_count = 0
    report_lines = []

    for idx, j in enumerate(judgments, 1):
        token = j.get("token", "")
        if not token:
            continue

        if token in token_map:
            skip_count += 1
            continue

        if not j.get("should_record", False):
            skip_count += 1
            report_lines.append(f"⏭️ 跳过 | {j.get('course_name', j.get('raw_name'))} — {j.get('reasoning', '')}")
            continue

        course_name = j.get("course_name", j.get("raw_name", "未命名"))
        owner = j.get("owner", "天天")
        speakers = [{"id": "ou_c434e777a879dc19c2c0a6d36d893cd9", "name": "天天"}] if owner == "天天" else [{"name": owner}]

        fields = {
            "课程名称": course_name,
            "课程年份": j.get("course_year", ""),
            "课程类别": [j.get("course_category", "其他课程")],
            "重要程度": [j.get("importance", "常规课程")],
            "妙记链接": j.get("url", ""),
            "上课日期": j.get("date", ""),
            "主讲人/主持人": speakers,
            "课程形式": ["讲解课"],
            "会议时长": j.get("duration", ""),
            "参与人数": j.get("participant_count", 0),
        }

        rec_id = name_map.get(course_name)
        if rec_id:
            payload = json.dumps({"fields": fields}, ensure_ascii=False)
            resp = run_lark_cli(
                "api", "PUT",
                f"/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/records/{rec_id}",
                "--data", payload,
            )
        else:
            payload = json.dumps(fields, ensure_ascii=False)
            resp = run_lark_cli(
                "base", "+record-upsert",
                "--base-token", BASE_TOKEN,
                "--table-id", TABLE_ID,
                "--json", payload,
            )

        if resp.get("code") == 0 or resp.get("ok"):
            success_count += 1
            action = "更新" if rec_id else "新建"
            report_lines.append(
                f"✅ {action} | {course_name} | {fields['课程年份']} | {fields['课程类别'][0]} | {fields['重要程度'][0]} | {fields['参与人数']}人"
            )
            new_id = (
                resp.get("data", {}).get("record", {}).get("record_id_list", [None])[0]
                or resp.get("data", {}).get("record", {}).get("id")
            )
            if new_id:
                name_map[course_name] = new_id
                token_map[token] = new_id
        else:
            error_count += 1
            log(f"❌ 写入失败 {course_name}: {resp}")
            report_lines.append(f"❌ {course_name} — 写入失败")

    log("=" * 50)
    log(f"写入完成: 成功 {success_count} 条, 跳过 {skip_count} 条, 失败 {error_count} 条")
    print("\n详细报告:")
    print("-" * 80)
    for line in report_lines:
        print(line)


def main():
    parser = argparse.ArgumentParser(description="同步飞书妙记到 Base")
    parser.add_argument("--collect-only", action="store_true", help="仅收集候选数据")
    parser.add_argument("--apply-judgments", action="store_true", help="应用判断结果写入 Base")
    parser.add_argument("--check-env", action="store_true", help="仅检查环境依赖")
    args = parser.parse_args()

    if args.check_env:
        lark_ok, cookie_ok = ensure_env()
        print(f"\n环境检查: lark-cli={'✅' if lark_ok else '❌'}, cookies={'✅' if cookie_ok else '❌'}")
        sys.exit(0 if (lark_ok and cookie_ok) else 1)

    # 确保环境
    lark_ok, cookie_ok = ensure_env()
    if not lark_ok:
        log("❌ lark-cli 未就绪，退出")
        sys.exit(1)
    if not cookie_ok:
        log("❌ cookies 未就绪，退出")
        sys.exit(1)

    if args.collect_only:
        collect_candidates()
    elif args.apply_judgments:
        apply_judgments()
    else:
        collect_candidates()
        if os.path.exists(JUDGMENTS_FILE):
            apply_judgments()
        else:
            log(f"未找到 {JUDGMENTS_FILE}，跳过写入阶段")


if __name__ == "__main__":
    main()

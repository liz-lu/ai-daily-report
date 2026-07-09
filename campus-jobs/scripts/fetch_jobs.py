#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实习/秋招岗位追踪器 —— 多源健壮抓取脚本（自用，非商业，不公开）

数据源（多源并行，能抓则抓，单源失败不影响其它）：
  1. zapplyjobs/Internships-2026  —— 机器人实时更新的科技实习岗（Markdown 表格，海外为主）
  2. vanshb03/Summer2027-Internships —— 2027 暑期实习（Markdown 表格，海外为主）
  3. 实习僧网页搜索 —— 国内实习岗（HTML 解析；当前停用，结构多变）
  可在 SOURCES 增减。

技术加固：
  - 每源独立 try/except，任一失败仅记录、不中断整体
  - 请求：完整浏览器 headers + 随机 UA + 超时 + 3 次重试 + 退避
  - 解析容错：字段缺失有默认值，脏数据跳过不崩
  - 合并去重（按 链接 / 公司+岗位）、标记近3天新增、按新鲜度排序
  - 抓取统计日志：每源成功/失败/条数
  - 零第三方依赖（仅标准库），GitHub Actions 直接可跑；无需任何 API key
"""

from __future__ import annotations

import json
import re
import sys
import time
import html as _html
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ_CN = timezone(timedelta(hours=8))
TODAY = datetime.now(TZ_CN).strftime("%Y-%m-%d")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
JOBS_FILE = DATA_DIR / "jobs.json"

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0",
]

# 关键词过滤：命中任一即收录（面向 产品/AI 岗）。留空 = 全部收录。
KEYWORDS = ["产品", "AI", "product", "运营", "PM", "machine learning", "ML", "data"]


def log(msg: str) -> None:
    print(f"[{datetime.now(TZ_CN).strftime('%H:%M:%S')}] {msg}", flush=True)


def _ua(i: int = 0) -> str:
    return UA_POOL[i % len(UA_POOL)]


def fetch(url: str, retries: int = 3, timeout: int = 20) -> str:
    """带重试+退避+随机UA的抓取，全部失败返回空串。"""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": _ua(attempt),
                "Accept": "text/html,application/json,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://www.google.com/",
                "Connection": "keep-alive",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            log(f"    第{attempt+1}次失败: {type(e).__name__}")
            time.sleep(1.5 * (attempt + 1))
    return ""


# ---------- 各源解析器 ----------

def parse_md_table_generic(md: str, source: str, col_map: dict) -> list:
    """通用 Markdown 表格解析。col_map: {'company':idx,'title':idx,'city':idx,'date':idx,'link_col':idx}"""
    jobs = []
    for line in md.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3 or set(cells[0]) <= set("-: *"):
            continue
        if cells[0].lower() in ("company", "公司", "role", "name"):
            continue

        def cell(k):
            i = col_map.get(k, -1)
            return cells[i] if 0 <= i < len(cells) else ""

        raw_company = re.sub(r"[*\[\]]", "", cell("company")).strip()
        raw_title = re.sub(r"[*\[\]]", "", cell("title")).strip()
        city = re.sub(r"[*\[\]]", "", cell("city")).strip()
        date_txt = cell("date")

        link_cell = cell("link_col") or cell("title") or cell("company")
        lm = re.search(r"\]\((https?://[^)]+)\)", link_cell)
        link = lm.group(1).strip() if lm else ""

        if not raw_company and not raw_title:
            continue

        hay = " ".join([raw_company, raw_title, city])
        if KEYWORDS and not any(k.lower() in hay.lower() for k in KEYWORDS):
            continue

        jobs.append({
            "company": _html.unescape(raw_company) or "—",
            "title": _html.unescape(raw_title) or "岗位",
            "city": _html.unescape(city) or "—",
            "degree": "",
            "deadline": "",
            "posted": date_txt,
            "link": link,
            "source": source,
            "tags": [t for t in [
                "实习" if re.search(r"intern|实习", hay, re.I) else "",
                "AI" if re.search(r"\bAI\b|ML|machine learning|大模型", hay, re.I) else "",
                "产品" if re.search(r"产品|product|PM", hay, re.I) else "",
            ] if t],
            "note": "",
        })
    return jobs


def parse_shixiseng(html_text: str, source: str) -> list:
    """实习僧网页：抽岗位名+公司+链接（当前停用，结构多变时保留备用）。"""
    jobs = []
    ids = re.findall(r'shixiseng\.com/intern/(inn_[a-zA-Z0-9_]+)', html_text)
    titles = re.findall(r'"job_name"\s*:\s*"([^"]{2,40})"', html_text)
    comps = re.findall(r'"company_name"\s*:\s*"([^"]{2,40})"', html_text)
    seen = set()
    for i, jid in enumerate(dict.fromkeys(ids)):
        title = titles[i] if i < len(titles) else "实习岗位"
        comp = comps[i] if i < len(comps) else "—"
        if jid in seen:
            continue
        seen.add(jid)
        jobs.append({
            "company": _html.unescape(comp), "title": _html.unescape(title),
            "city": "—", "degree": "", "deadline": "", "posted": "",
            "link": f"https://www.shixiseng.com/intern/{jid}",
            "source": source, "tags": ["实习"], "note": "",
        })
    return jobs


# ---------- 数据源配置 ----------
SOURCES = [
    {
        "name": "zapplyjobs·实时科技实习",
        "url": "https://raw.githubusercontent.com/zapplyjobs/Internships-2026/main/README.md",
        "parser": lambda t, s: parse_md_table_generic(t, s, {"company": 0, "title": 1, "city": 2, "date": 3, "link_col": 5}),
    },
    {
        "name": "Summer2027·暑期实习",
        "url": "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/README.md",
        "parser": lambda t, s: parse_md_table_generic(t, s, {"company": 0, "title": 1, "city": 2, "date": 3, "link_col": 4}),
    },
    # 实习僧（国内源）：页面有字体加密+NUXT结构多变，当前解析不稳定，暂停用。
    # 待其结构稳定或换用其它国内源时启用（取消下面注释即可）：
    # {
    #     "name": "实习僧·国内实习",
    #     "url": "https://www.shixiseng.com/interns?keyword=%E4%BA%A7%E5%93%81&city=%E5%85%A8%E5%9B%BD",
    #     "parser": parse_shixiseng,
    # },
]


def job_key(job: dict) -> str:
    link = str(job.get("link", "")).strip().lower()
    if link:
        return link
    return (str(job.get("company", "")) + "|" + str(job.get("title", ""))).lower()


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def main() -> int:
    existing = load_json(JOBS_FILE, {}).get("jobs", [])
    log(f"已有岗位 {len(existing)} 条")
    merged = {job_key(j): j for j in existing}

    stats, new_count = [], 0
    for src in SOURCES:
        name = src["name"]
        try:
            log(f"抓取源: {name}")
            content = fetch(src["url"])
            if not content:
                stats.append(f"{name}: 抓取失败(0)")
                continue
            jobs = src["parser"](content, name)
            stats.append(f"{name}: {len(jobs)} 条")
            log(f"  → 解析 {len(jobs)} 条")
            for j in jobs:
                k = job_key(j)
                if k not in merged:
                    j["first_seen"] = TODAY
                    new_count += 1
                else:
                    j["first_seen"] = merged[k].get("first_seen", TODAY)
                j["updated"] = TODAY
                merged[k] = j
            time.sleep(1)
        except Exception as e:
            stats.append(f"{name}: 异常({type(e).__name__})")
            log(f"  [warn] {name} 处理异常: {e}")

    all_jobs = list(merged.values())
    for j in all_jobs:
        fs = j.get("first_seen", TODAY)
        try:
            days = (datetime.strptime(TODAY, "%Y-%m-%d") - datetime.strptime(fs, "%Y-%m-%d")).days
            j["is_new"] = days <= 3
        except Exception:
            j["is_new"] = False

    all_jobs.sort(key=lambda j: (j.get("first_seen", ""), j.get("is_new", False)), reverse=True)

    output = {
        "updated_at": datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M"),
        "total": len(all_jobs),
        "new_today": new_count,
        "source_stats": stats,
        "jobs": all_jobs,
    }
    JOBS_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"完成：共 {len(all_jobs)} 条，本次新增 {new_count} 条")
    log(f"各源: {' | '.join(stats)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实习/秋招岗位追踪器 —— 数据抓取与结构化脚本

数据源：开源社区维护的校招/实习汇总仓库（Markdown 表格，合规、每日更新）
  默认：namewyf/Campus2026（2026届互联网校招&实习信息汇总）

流程：
  1. 抓取数据源 README.md（标准 Markdown 表格）
  2. 直接解析表格 → 结构化岗位（公司/链接/更新日期/地点/备注）——无需任何 API
  3. 与已有 data/jobs.json 合并去重（按 公司+链接），标记新增、按更新日期倒序
  4. 写回 data/jobs.json，供 index.html 前端渲染

设计原则：
  - 仅用 Python 标准库，无第三方 pip 依赖（GitHub Actions 直接可跑）
  - 不需要任何 API key —— 纯规则解析，零成本、零故障点
  - 抓取失败不阻断：保留已有数据，脚本正常退出（不让 Actions 失败）
  - 关键词过滤：默认聚焦 产品 / AI 相关岗位（可在 KEYWORDS 调整；留空则收录全部）
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ_CN = timezone(timedelta(hours=8))
TODAY = datetime.now(TZ_CN).strftime("%Y-%m-%d")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SOURCES_FILE = DATA_DIR / "sources.json"
JOBS_FILE = DATA_DIR / "jobs.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 AI-PM-Tracker/2.0"
)

# 关键词过滤：岗位「公司+备注+链接文字+地点」命中任一即收录。留空列表 = 收录全部。
KEYWORDS = ["产品", "AI", "product", "运营", "实习"]

# 默认数据源（若 sources.json 未配置则用这个）
DEFAULT_SOURCES = [
    {
        "name": "Campus2026(社区维护·每日更新)",
        "url": "https://raw.githubusercontent.com/namewyf/Campus2026/main/README.md",
    }
]


def log(msg: str) -> None:
    print(f"[{datetime.now(TZ_CN).strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch_url(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log(f"  [warn] 抓取失败 {url}: {e}")
        return ""


def parse_markdown_table(md: str, source_name: str) -> list:
    """解析 Markdown 表格，抽取岗位。表头形如：公司 | 招聘状态&&投递链接 | 更新日期 | 地点 | 备注"""
    jobs = []
    for line in md.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        if cells[0] in ("公司", "Company", "") or set(cells[0]) <= set("-: "):
            continue

        company = cells[0]
        status_cell = cells[1] if len(cells) > 1 else ""
        update_date = cells[2] if len(cells) > 2 else ""
        location = cells[3] if len(cells) > 3 else ""
        note = cells[4] if len(cells) > 4 else ""

        link_m = re.search(r"\[([^\]]*)\]\((https?://[^)]+)\)", status_cell)
        title = link_m.group(1).strip() if link_m else re.sub(r"[\[\]]", "", status_cell)
        link = link_m.group(2).strip() if link_m else ""

        d = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", update_date)
        deadline_norm = f"{d.group(1)}-{int(d.group(2)):02d}-{int(d.group(3)):02d}" if d else ""

        haystack = " ".join([company, title, note, location])
        if KEYWORDS and not any(k.lower() in haystack.lower() for k in KEYWORDS):
            continue

        jobs.append({
            "company": company,
            "title": title or "校招/实习",
            "city": location or "—",
            "degree": "",
            "deadline": deadline_norm,
            "link": link,
            "source": source_name,
            "tags": [t for t in ["实习" if "实习" in haystack else "校招",
                                  "AI" if ("AI" in haystack or "ai" in haystack) else "",
                                  "产品" if "产品" in haystack else ""] if t],
            "note": note,
        })
    return jobs


def job_key(job: dict) -> str:
    return (str(job.get("company", "")).strip() + "|" + str(job.get("link", "")).strip()).lower()


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def main() -> int:
    sources = load_json(SOURCES_FILE, {}).get("fetch_sources") or DEFAULT_SOURCES
    existing = load_json(JOBS_FILE, {}).get("jobs", [])
    log(f"已有岗位 {len(existing)} 条")
    merged = {job_key(j): j for j in existing}

    new_count = 0
    for src in sources:
        url = src.get("url", "")
        name = src.get("name", url)
        if not url:
            continue
        log(f"抓取源: {name}")
        md = fetch_url(url)
        if not md:
            continue
        jobs = parse_markdown_table(md, name)
        log(f"  → 解析得到 {len(jobs)} 条(关键词过滤后)")
        for j in jobs:
            k = job_key(j)
            if k not in merged:
                j["first_seen"] = TODAY
                new_count += 1
            else:
                j["first_seen"] = merged[k].get("first_seen", TODAY)
            j["updated"] = TODAY
            merged[k] = j

    all_jobs = list(merged.values())
    for j in all_jobs:
        fs = j.get("first_seen", TODAY)
        try:
            days = (datetime.strptime(TODAY, "%Y-%m-%d") - datetime.strptime(fs, "%Y-%m-%d")).days
            j["is_new"] = days <= 3
        except Exception:
            j["is_new"] = False

    def sort_key(j):
        d = j.get("deadline", "")
        return d if re.match(r"\d{4}-\d{2}-\d{2}", d or "") else "0000"
    all_jobs.sort(key=sort_key, reverse=True)

    output = {
        "updated_at": datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M"),
        "total": len(all_jobs),
        "new_today": new_count,
        "jobs": all_jobs,
    }
    JOBS_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"完成：共 {len(all_jobs)} 条，本次新增 {new_count} 条 → {JOBS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

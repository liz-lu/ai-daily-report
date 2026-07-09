#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
27届 AI 产品岗秋招追踪器 —— 数据抓取与结构化脚本

流程：
  1. 读取 data/sources.json 中的公开数据源（RSS / 聚合页 / 秋招汇总）
  2. 抓取原始文本
  3. 调用 DeepSeek API，从原始文本中提取「27届/2027届 AI产品/产品」岗位，结构化为 JSON
  4. 与已有 data/jobs.json 合并去重（按 公司+岗位 唯一），标记新增、按截止日期排序
  5. 写回 data/jobs.json，供 index.html 前端渲染

设计原则：
  - 无第三方 pip 依赖，仅用 Python 标准库（与 GitHub Actions 环境兼容）
  - 优雅降级：未配置 DEEPSEEK_API_KEY 时，跳过 AI 处理、保留已有数据，脚本正常退出（不让 Actions 失败）
  - 抓取失败不阻断：单个源失败仅记录，不影响其它源

环境变量：
  DEEPSEEK_API_KEY   DeepSeek API 密钥（存 GitHub Secrets，勿写进代码）
  DEEPSEEK_MODEL     模型名，默认 deepseek-chat
  DEEPSEEK_BASE_URL  接口地址，默认 https://api.deepseek.com
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
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
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 AI-PM-Tracker/1.0"
)

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip()
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().rstrip("/")


def log(msg: str) -> None:
    print(f"[{datetime.now(TZ_CN).strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch_url(url: str, timeout: int = 20) -> str:
    """抓取单个 URL 的文本内容，失败返回空串。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            for enc in ("utf-8", "gbk", "gb2312"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="ignore")
    except Exception as e:
        log(f"  [warn] 抓取失败 {url}: {e}")
        return ""


def strip_html(text: str) -> str:
    """粗略去掉 HTML 标签与多余空白，得到可喂给 LLM 的纯文本。"""
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-zA-Z#0-9]+;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def call_deepseek(raw_text: str) -> list:
    """调用 DeepSeek，把原始文本抽成结构化岗位列表。"""
    if not DEEPSEEK_API_KEY:
        log("未配置 DEEPSEEK_API_KEY，跳过 AI 结构化。")
        return []

    raw_text = raw_text[:24000]

    system_prompt = (
        "你是校招信息结构化助手。用户会给你从招聘网页抓取的原始文本，"
        "你需要从中提取【2027届/27届】的【AI产品经理 / AI产品 / 产品经理（AI方向）】相关岗位。"
        "严格只输出一个 JSON 数组，不要任何解释文字、不要 markdown 代码块。"
        "每个岗位对象字段为："
        "company(公司), title(岗位名), city(城市), degree(学历要求), "
        "deadline(投递截止日期,格式YYYY-MM-DD,未知填空串), link(投递/详情链接,未知填空串), "
        "source(信息来源), tags(标签数组)。"
        "只保留确实与 AI/产品 相关、且面向 2027届（或2026年底-2027年毕业）的岗位；"
        "无法确认是 27届的、或明显是 26届已截止的，请丢弃。若没有符合的，输出 []。"
    )

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": raw_text},
        ],
        "temperature": 0.2,
        "max_tokens": 4000,
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{DEEPSEEK_BASE_URL}/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        content = result["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content).strip()
        jobs = json.loads(content)
        if isinstance(jobs, list):
            return jobs
        return []
    except Exception as e:
        log(f"  [warn] DeepSeek 调用/解析失败: {e}")
        return []


def job_key(job: dict) -> str:
    return (str(job.get("company", "")).strip() + "|" + str(job.get("title", "")).strip()).lower()


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def main() -> int:
    sources = load_json(SOURCES_FILE, {})
    existing_jobs = load_json(JOBS_FILE, {}).get("jobs", [])
    log(f"已有岗位 {len(existing_jobs)} 条")

    merged = {job_key(j): j for j in existing_jobs}

    new_count = 0
    for src in sources.get("fetch_sources", []):
        url = src.get("url", "")
        name = src.get("name", url)
        if not url:
            continue
        log(f"抓取源: {name}")
        html = fetch_url(url)
        if not html:
            continue
        text = strip_html(html)
        if len(text) < 50:
            continue
        jobs = call_deepseek(text)
        log(f"  -> 结构化得到 {len(jobs)} 条")
        for j in jobs:
            j["source"] = j.get("source") or name
            j["updated"] = TODAY
            k = job_key(j)
            if k not in merged:
                j["first_seen"] = TODAY
                j["is_new"] = True
                new_count += 1
            else:
                j["first_seen"] = merged[k].get("first_seen", TODAY)
                j["is_new"] = False
            merged[k] = j
        time.sleep(1)

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
        if d and re.match(r"\d{4}-\d{2}-\d{2}", d):
            return (0, d)
        return (1, "9999")

    all_jobs.sort(key=sort_key)

    output = {
        "updated_at": datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M"),
        "total": len(all_jobs),
        "new_today": new_count,
        "jobs": all_jobs,
    }
    JOBS_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"完成：共 {len(all_jobs)} 条岗位，本次新增 {new_count} 条 -> {JOBS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

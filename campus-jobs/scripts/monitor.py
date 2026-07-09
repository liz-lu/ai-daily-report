#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI产品岗求职作战台 —— 招聘官网更新监测脚本（自用，非商业，不公开）

做法（合规、稳定、不碰反爬接口）：
  1. 读取 data/companies.json 里各公司招聘官网入口
  2. 每天访问每个页面，计算正文内容指纹(hash)——已过滤动态噪音防误报
  3. 与上次指纹对比：变化 → 标记"今日有更新"，提示你优先去看
  4. 记录每家的最近检查时间、连续无更新天数、历史更新次数
  5. 输出 data/monitor.json 供前端渲染

技术加固：
  - 每站独立 try/except，任一失败不中断整体
  - 3次重试 + 退避 + 随机UA + 超时
  - 指纹过滤动态噪音（csrf/token/session/uuid/长随机串/多位数字），防假阳性
  - 网站临时抓不到(unreachable)时保留旧指纹，避免恢复后误报
  - 异常兜底：若一次几乎所有站点同时"更新"，判为解析异常并告警(不污染数据)
  - 零第三方依赖、零API key
"""

from __future__ import annotations

import json
import re
import sys
import time
import hashlib
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ_CN = timezone(timedelta(hours=8))
NOW = datetime.now(TZ_CN)
TODAY = NOW.strftime("%Y-%m-%d")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
COMPANIES_FILE = DATA_DIR / "companies.json"
MONITOR_FILE = DATA_DIR / "monitor.json"

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]


def log(m: str) -> None:
    print(f"[{NOW.strftime('%H:%M:%S')}] {m}", flush=True)


def fetch(url: str, retries: int = 3, timeout: int = 20) -> str:
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA_POOL[i % len(UA_POOL)],
                "Accept": "text/html,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="ignore")
        except Exception as e:
            log(f"    第{i+1}次失败: {type(e).__name__}")
            time.sleep(1.5 * (i + 1))
    return ""


def content_fingerprint(html_text: str) -> str:
    """提取正文可见文本算指纹，并过滤每次都变的动态噪音，避免假阳性误报。"""
    t = re.sub(r"<script[\s\S]*?</script>", " ", html_text, flags=re.I)
    t = re.sub(r"<style[\s\S]*?</style>", " ", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    # 过滤动态噪音：
    t = re.sub(r"(?i)(csrf|token|session|sid|uuid|nonce|_ga|timestamp|ts)[=_:\-][a-z0-9\-]+", " ", t)  # 键值型token
    t = re.sub(r"[a-f0-9]{8,}", " ", t)          # 长十六进制(hash/id)
    t = re.sub(r"[a-zA-Z0-9_\-]{16,}", " ", t)   # 超长随机串(base64/token)
    t = re.sub(r"\d{4,}", " ", t)                # 4位以上数字(访问量/时间戳/ID)
    t = re.sub(r"\s+", "", t)
    return hashlib.md5(t.encode("utf-8")).hexdigest()


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def main() -> int:
    companies = load_json(COMPANIES_FILE, {}).get("companies", [])
    prev = {c["name"]: c for c in load_json(MONITOR_FILE, {}).get("companies", [])}
    log(f"监测 {len(companies)} 家招聘官网")

    results, updated_today = [], 0
    for c in companies:
        name, url = c["name"], c["url"]
        rec = prev.get(name, {})
        entry = {
            "name": name, "category": c.get("category", ""), "url": url,
            "note": c.get("note", ""),
            "first_seen": rec.get("first_seen", TODAY),
            "last_change": rec.get("last_change", ""),
            "update_count": rec.get("update_count", 0),
            "fingerprint": rec.get("fingerprint", ""),
            "status": "ok",
        }
        try:
            html_text = fetch(url)
            if not html_text:
                # 临时抓不到：保留旧指纹，标 unreachable，不判为更新
                entry["status"] = "unreachable"
                entry["updated_today"] = False
            else:
                fp = content_fingerprint(html_text)
                changed = bool(entry["fingerprint"]) and fp != entry["fingerprint"]
                entry["updated_today"] = changed
                entry["fingerprint"] = fp   # 更新指纹
        except Exception as e:
            entry["status"] = f"error:{type(e).__name__}"
            entry["updated_today"] = False
        entry["last_check"] = TODAY
        results.append(entry)
        time.sleep(0.8)

    # 兜底：若"更新"比例异常高(>70%且总数>=5)，大概率是解析/网络异常批量误判，
    # 则本轮不采信这些"更新"，仅记录告警，避免污染数据、避免狼来了。
    changed_list = [e for e in results if e.get("updated_today")]
    anomaly = len(results) >= 5 and len(changed_list) > 0.7 * len(results)
    if anomaly:
        for e in results:
            e["updated_today"] = False
        log(f"[warn] 异常：{len(changed_list)}/{len(results)} 家同时变化，判为批量误判，本轮不采信更新")

    # 确认为更新的：更新 last_change / count
    for e in results:
        if e.get("updated_today"):
            e["last_change"] = TODAY
            e["update_count"] = e.get("update_count", 0) + 1
            updated_today += 1
        # 连续无更新天数
        if e["last_change"]:
            try:
                e["days_since_change"] = (datetime.strptime(TODAY, "%Y-%m-%d")
                                          - datetime.strptime(e["last_change"], "%Y-%m-%d")).days
            except Exception:
                e["days_since_change"] = None
        else:
            e["days_since_change"] = None
        log(f"  {e['name']}: {'today-updated' if e.get('updated_today') else e['status']}")

    results.sort(key=lambda e: (e.get("updated_today", False), e.get("last_change", "")), reverse=True)

    out = {
        "updated_at": NOW.strftime("%Y-%m-%d %H:%M"),
        "total": len(results),
        "updated_today": updated_today,
        "anomaly": anomaly,
        "companies": results,
    }
    MONITOR_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"完成：{len(results)} 家，今日 {updated_today} 家有更新{' (异常已拦截)' if anomaly else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

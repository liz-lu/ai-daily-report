#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成仓库根目录的公开首页 index.html。

背景：AI 简报数据存放在 daily-briefs/ 子目录，主脚本 ai-daily-brief.py 会在
daily-briefs/index.html 生成完整格式的时间线首页（相对链接指向同目录的当日文件）。
但对外公开的链接是仓库根目录 https://<user>.github.io/<repo>/，其 index.html
需要把简报链接加上 daily-briefs/ 前缀才能正确跳转。

本脚本复用主脚本的渲染函数，扫描 daily-briefs/ 全部历史，生成一份
根目录 index.html（完整格式 + daily-briefs/ 前缀链接），保证：
  - 根目录公开链接始终展示全部历史
  - 每天 GitHub Actions 跑完主脚本后再跑本脚本，根目录首页自动更新
  - 不改变原有页面格式

用法：python scripts/sync_root_index.py
"""
from __future__ import annotations
import importlib.util
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIEFS_DIR = REPO_ROOT / "daily-briefs"


def _load_brief_module():
    path = REPO_ROOT / "scripts" / "ai-daily-brief.py"
    spec = importlib.util.spec_from_file_location("ai_daily_brief", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    if not BRIEFS_DIR.is_dir():
        print(f"[sync_root_index] 未找到目录: {BRIEFS_DIR}，跳过")
        return 0
    htmls = sorted(BRIEFS_DIR.glob("AI简报-*.html"), key=lambda p: p.name, reverse=True)
    if not htmls:
        print("[sync_root_index] daily-briefs 下没有简报，跳过")
        return 0

    brief = _load_brief_module()
    latest_name = htmls[0].name
    content = brief.render_index_timeline_html(BRIEFS_DIR, latest_name)

    # 给指向简报文件的相对链接加上 daily-briefs/ 前缀
    root_index = re.sub(
        r'(href|src)="(AI简报-\d{4}-\d{2}-\d{2}\.(?:html|txt|json))',
        r'\1="daily-briefs/\2',
        content,
    )
    (REPO_ROOT / "index.html").write_text(root_index, encoding="utf-8")

    days = sorted(set(re.findall(r"\d{4}-\d{2}-\d{2}", root_index)))
    print(f"[sync_root_index] 已生成根目录 index.html，含 {len(days)} 期（{days[0]} → {days[-1]}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

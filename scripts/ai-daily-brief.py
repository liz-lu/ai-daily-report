#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日抓取若干 AI 相关 RSS，生成简报文本与网页版本，并尝试弹出 macOS 通知。
依赖：Python 3 标准库（无 pip 包）。

输出目录：~/Documents/AI每日简报/
  - AI简报-YYYY-MM-DD.txt   纯文本备份
  - AI简报-YYYY-MM-DD.html  排版后的网页版（推荐阅读）
  - AI简报-YYYY-MM-DD.json  结构化归档（便于检索/复盘/二次分析）
  - index.html               按日期浏览历史简报
  - latest.html              自动跳转到最新一期
"""

from __future__ import annotations

import html
import json
import os
import re
import base64
import shutil
import subprocess
import sys
import tempfile
import textwrap
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from calendar import monthrange
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError

# 东八区日期（用于文件名与简报抬头）
TZ_CN = timezone(timedelta(hours=8))

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 AI-Daily-Brief/2.0"
)

# (栏目名, RSS URL, 每条最多取几条, 是否按关键词过滤为 AI 相关)
FEEDS: list[tuple[str, str, int, bool]] = [
    ("量子位", "https://www.qbitai.com/feed", 12, True),
    ("雷锋网", "https://www.leiphone.com/feed", 10, True),
    ("36氪", "https://36kr.com/feed", 10, True),
    ("IT之家", "https://www.ithome.com/rss/", 12, True),
    ("Solidot", "https://www.solidot.org/index.rss", 10, True),
]

_CN_AI_KEYWORDS: tuple[str, ...] = (
    "人工智能", "大模型", "生成式", "机器学习", "深度学习", "神经网络", "多模态",
    "世界模型", "具身", "智能体", "AI", "AIGC", "算力", "英伟达", "NVIDIA", "AMD",
    "OpenAI", "ChatGPT", "GPT", "LLM", "Agent", "Anthropic", "Claude", "Gemini",
    "谷歌", "Google", "微软", "Microsoft", "Meta", "语音", "视觉", "自动驾驶",
    "机器人", "智驾", "芯片", "半导体", "CUDA", "Diffusion", "Sora", "MCP", "RAG",
    "Transformer", "Token", "小程序", "模型", "算法", "训练", "推理", "开源模型",
    "阿里", "腾讯", "字节", "百度", "华为", "荣耀", "蚂蚁", "智谱", "月之暗面",
)

_THEME_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("大模型 / 应用 / 智能体", (
        "大模型", "LLM", "GPT", "ChatGPT", "OpenAI", "Claude", "Gemini", "Anthropic",
        "智能体", "Agent", "AIGC", "生成式", "多模态", "RAG", "MCP", "千问", "豆包",
        "灵光圈", "百灵", "语音识别", "翻译器",
    )),
    ("具身 / 机器人 / 智驾", (
        "具身", "机器人", "人形", "智驾", "自动驾驶", "VLA", "世界模型", "速腾", "激光雷达",
    )),
    ("芯片 / 算力 / 推理", (
        "芯片", "半导体", "算力", "推理", "GPU", "NVIDIA", "英伟达", "AMD", "CUDA", "天数智芯",
    )),
    ("云 / 大厂 / 生态", (
        "阿里", "腾讯", "字节", "百度", "华为", "微软", "谷歌", "Google", "蚂蚁", "火山引擎",
        "亚马逊", "苹果", "库克", "Meta", "LG", "Discord",
    )),
    ("数据 / 安全 / 合规 / 社会议题", (
        "安全", "隐私", "刑事", "监管", "枪击", "风险", "版权", "AI 生成", "上传音乐",
    )),
    ("产业 / 投融资 / 活动", (
        "融资", "投资", "峰会", "大会", "沙盒", "IPO", "独角兽", "裁员", "收购", "合作",
    )),
)

_THEME_ACCENTS: dict[str, tuple[str, str]] = {
    "大模型 / 应用 / 智能体": ("#7a9b86", "模型与应用正快速向真实工作流收敛"),
    "具身 / 机器人 / 智驾": ("#8aa4b1", "物理世界里的 AI 仍在拼落地速度"),
    "芯片 / 算力 / 推理": ("#9f8a68", "基础设施的效率叙事持续升温"),
    "云 / 大厂 / 生态": ("#8f9bb8", "平台与生态的整合能力决定扩散半径"),
    "数据 / 安全 / 合规 / 社会议题": ("#b67b72", "效率之外，治理与边界问题变得更紧迫"),
    "产业 / 投融资 / 活动": ("#bea06d", "资本、合作与大会正在放大行业方向感"),
    "其他 AI 科技动态": ("#97a19a", "零散动态中仍有值得继续跟踪的信号"),
}


StoryRow = tuple[str, str, str, str | None, str | None]
ThemeRow = tuple[str, str, str, str | None]


def _strip_ns(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _fetch(url: str, timeout: int = 35) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_err: Exception | None = None
    for _ in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (URLError, HTTPError, TimeoutError, OSError) as e:
            last_err = e
    assert last_err is not None
    raise last_err


def _load_optional_env_files(paths: list[Path]) -> None:
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _clean_title(s: str) -> str:
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _strip_html_to_text(raw: str, max_len: int = 480) -> str:
    s = html.unescape(raw or "")
    s = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", s, flags=re.I)
    s = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[: max_len - 1].rsplit(" ", 1)[0] + "…"
    return s


def _escape_html(text: str | None) -> str:
    return html.escape(text or "", quote=True)


def _rss_item_excerpt(node: ET.Element, title: str) -> str | None:
    excerpt: str | None = None
    desc_el = node.find("description")
    if desc_el is not None and (desc_el.text or "").strip():
        t = _strip_html_to_text(desc_el.text, max_len=520)
        if t and len(t) > min(len(title) + 12, 40):
            excerpt = t
    if excerpt is None:
        for child in node:
            if _strip_ns(child.tag) == "encoded" and (child.text or "").strip():
                excerpt = _strip_html_to_text(child.text, max_len=520)
                break
    return excerpt


def _parse_rss_items(root: ET.Element) -> Iterable[tuple[str, str, str | None, str | None]]:
    channel = root.find("channel")
    if channel is None:
        for ch in root:
            if _strip_ns(ch.tag) == "channel":
                channel = ch
                break
    if channel is None:
        return

    for node in channel:
        if _strip_ns(node.tag) != "item":
            continue
        title_el = node.find("title")
        link_el = node.find("link")
        pub_el = node.find("pubDate")
        title = (title_el.text or "").strip() if title_el is not None else ""
        link = (link_el.text or "").strip() if link_el is not None else ""
        pub = (pub_el.text or "").strip() if pub_el is not None else None
        excerpt = _rss_item_excerpt(node, title)
        if title or link:
            yield title, link, pub, excerpt


def _parse_atom_entries(root: ET.Element) -> Iterable[tuple[str, str, str | None, str | None]]:
    for entry in root.iter():
        if _strip_ns(entry.tag) != "entry":
            continue
        title = ""
        link = ""
        updated = None
        cand_excerpts: list[str] = []
        for child in entry:
            tag = _strip_ns(child.tag)
            if tag == "title" and child.text:
                title = child.text.strip()
            elif tag == "link" and child.get("href"):
                link = child.get("href", "").strip()
            elif tag in ("updated", "published") and child.text:
                updated = child.text.strip()
            elif tag in ("summary", "content") and (child.text or "").strip():
                cand_excerpts.append(_strip_html_to_text(child.text, max_len=520))
        excerpt: str | None = None
        if cand_excerpts:
            excerpt = max(cand_excerpts, key=len)
            if len(excerpt) <= min(len(title) + 12, 40):
                excerpt = None
        if title or link:
            yield title, link, updated, excerpt


def _story_themes(title: str, excerpt: str | None) -> list[str]:
    blob = title
    if excerpt:
        blob = f"{title} {excerpt}"
    hit: list[str] = []
    for name, kws in _THEME_RULES:
        for kw in kws:
            if kw.lower() in blob.lower() or kw in blob:
                hit.append(name)
                break
    if not hit:
        hit.append("其他 AI 科技动态")
    return hit


def _is_cn_ai_title(title: str) -> bool:
    t = title.lower()
    for keyword in _CN_AI_KEYWORDS:
        if keyword.lower() in t or keyword in title:
            return True
    return False


def collect_stories() -> list[StoryRow]:
    out: list[StoryRow] = []
    for section, url, limit, use_filter in FEEDS:
        try:
            raw = _fetch(url)
        except (URLError, HTTPError, TimeoutError, OSError) as e:
            out.append((section, f"[抓取失败] {url}", str(e), None, None))
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            out.append((section, f"[解析失败] {url}", str(e), None, None))
            continue

        root_tag = _strip_ns(root.tag)
        items: list[tuple[str, str, str | None, str | None]] = []
        if root_tag == "rss":
            items = list(_parse_rss_items(root))
        elif root_tag == "feed":
            items = list(_parse_atom_entries(root))
        else:
            out.append((section, f"[未知 Feed 格式] {url}", root_tag, None, None))
            continue

        n = 0
        for title, link, pub, excerpt in items:
            title = _clean_title(title)
            if not title and not link:
                continue
            if use_filter and not _is_cn_ai_title(title):
                continue
            out.append((section, title or "(无标题)", link, pub, excerpt))
            n += 1
            if n >= limit:
                break
    return out


def _compute_brief_data(
    stories: list[StoryRow],
) -> tuple[
    list[StoryRow],
    list[StoryRow],
    dict[str, list[ThemeRow]],
    list[str],
    list[str],
]:
    ok: list[StoryRow] = []
    errors: list[StoryRow] = []
    for row in stories:
        if row[1].startswith("["):
            errors.append(row)
        else:
            ok.append(row)

    theme_to_rows: dict[str, list[ThemeRow]] = defaultdict(list)
    for section, title, link, _pub, excerpt in ok:
        for theme in _story_themes(title, excerpt):
            theme_to_rows[theme].append((section, title, link, excerpt))

    themes_sorted = sorted(theme_to_rows.keys(), key=lambda name: len(theme_to_rows[name]), reverse=True)
    sections_seen = sorted({section for section, _, _, _, _ in ok})
    return ok, errors, theme_to_rows, themes_sorted, sections_seen


def _theme_cards(theme_to_rows: dict[str, list[ThemeRow]], themes_sorted: list[str]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for theme in themes_sorted:
        rows = theme_to_rows[theme]
        sources = sorted({section for section, _, _, _ in rows})
        accent, desc = _THEME_ACCENTS.get(theme, _THEME_ACCENTS["其他 AI 科技动态"])
        cards.append({
            "theme": theme,
            "count": len(rows),
            "sources": sources,
            "accent": accent,
            "description": desc,
            "anchor": _slugify(theme),
        })
    return cards


def _slugify(text: str) -> str:
    slug = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", text).strip("-").lower()
    return slug or "section"


def _truncate(text: str, limit: int = 140) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _normalize_llm_endpoint(base_url: str | None) -> str:
    if not base_url:
        return "https://api.openai.com/v1/chat/completions"
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _extract_json_block(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    match = re.search(r"\{[\s\S]*\}", s)
    return match.group(0) if match else s


def generate_llm_summary(
    ok: list[StoryRow],
    errors: list[StoryRow],
    theme_to_rows: dict[str, list[ThemeRow]],
    themes_sorted: list[str],
) -> dict[str, Any] | None:
    api_key = os.environ.get("AI_DAILY_BRIEF_API_KEY") or os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("AI_DAILY_BRIEF_MODEL") or os.environ.get("OPENAI_MODEL")
    if not api_key or not model or not ok:
        return None

    top_themes: list[dict[str, Any]] = []
    for theme in themes_sorted[:5]:
        rows = theme_to_rows[theme][:4]
        top_themes.append({
            "theme": theme,
            "stories": [
                {
                    "source": section,
                    "title": title,
                    "excerpt": _truncate(excerpt or "", 180),
                }
                for section, title, _link, excerpt in rows
            ],
        })

    payload = {
        "date": datetime.now(TZ_CN).strftime("%Y-%m-%d"),
        "story_count": len(ok),
        "error_count": len(errors),
        "themes": top_themes,
    }
    prompt = (
        "你是中文科技媒体总编，请根据给定的 AI 新闻聚合数据，输出一份简报页可直接使用的 JSON。"
        "要求：1) 文风克制、专业、像高质量晨报；2) 不夸张，不编造事实；3) 只基于输入内容归纳；"
        "4) 输出 JSON，不要包裹 markdown。"
        'JSON schema: {"heroSummary": str, "keyThemes": [{"title": str, "summary": str}], '
        '"impactNotes": [str], "watchlist": [str]}'
    )

    body = {
        "model": model,
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }
    req = urllib.request.Request(
        _normalize_llm_endpoint(os.environ.get("AI_DAILY_BRIEF_BASE_URL") or os.environ.get("OPENAI_BASE_URL")),
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        message = raw["choices"][0]["message"]["content"]
        parsed = json.loads(_extract_json_block(message))
    except Exception:
        return None

    if not isinstance(parsed, dict):
        return None
    parsed.setdefault("heroSummary", "")
    parsed.setdefault("keyThemes", [])
    parsed.setdefault("impactNotes", [])
    parsed.setdefault("watchlist", [])
    return parsed


def render_brief(stories: list[StoryRow]) -> str:
    now = datetime.now(TZ_CN)
    lines: list[str] = []
    lines.append(f"中文 AI 资讯简报 · {now.strftime('%Y-%m-%d %H:%M')}（北京时间）")
    lines.append("=" * 60)
    lines.append(
        "说明：数据来自各站 RSS；在标题与链接外，尽量附带摘要字段的纯文本摘录，"
        "并按主题自动归类。归类规则为关键词启发式，可能与人工编辑分类不同，请以原文为准。"
    )
    lines.append("")

    ok, errors, theme_to_rows, themes_sorted, sections_seen = _compute_brief_data(stories)
    lines.append("## 一、本期概览与主题地图")
    lines.append("-" * 40)
    lines.append(
        f"- 有效条目 **{len(ok)}** 条，来自栏目：{', '.join(sections_seen)}。"
        f"另有抓取/解析异常 **{len(errors)}** 处（见文末）。"
    )
    lines.append(
        "- 阅读建议：先扫主题地图把握「今天在吵什么」，再下钻感兴趣的主题段落；"
        "需要核对事实或细节时，用「分源原文索引」直达媒体原始页面。"
    )
    lines.append("")
    lines.append("| 主题 | 相关条数（可跨主题重复计数） | 主要来源 |")
    lines.append("| --- | ---: | --- |")
    for theme in themes_sorted:
        rows = theme_to_rows[theme]
        srcs = sorted({section for section, _, _, _ in rows})
        lines.append(f"| {theme} | {len(rows)} | {'、'.join(srcs[:4])}{'…' if len(srcs) > 4 else ''} |")
    lines.append("")

    lines.append("## 二、按主题深读（摘录 + 链接）")
    lines.append("-" * 40)
    for theme in themes_sorted:
        lines.append("")
        lines.append(f"### {theme}")
        seen_links: set[str] = set()
        for section, title, link, excerpt in theme_to_rows[theme]:
            if not link or link in seen_links:
                continue
            seen_links.add(link)
            lines.append(f"- **【{section}】** {title}")
            if excerpt:
                lines.append(f"  摘录：{excerpt}")
            if link.startswith("http"):
                lines.append(f"  {link}")
    lines.append("")

    lines.append("## 三、分源原文索引（时间线）")
    lines.append("-" * 40)
    current_section = None
    for section, title, link, pub, excerpt in stories:
        if section != current_section:
            current_section = section
            lines.append("")
            lines.append(f"### {section}")
            lines.append("-" * 28)
        if title.startswith("[") and link and not link.startswith("http"):
            lines.append(f"- {title} — {link}")
            continue
        line = f"- {title}"
        if pub:
            line += f"  ({pub})"
        lines.append(line)
        if excerpt:
            lines.append(f"  摘录：{excerpt}")
        if link.startswith("http"):
            lines.append(f"  {link}")

    if errors:
        lines.append("")
        lines.append("## 附：抓取异常")
        lines.append("-" * 40)
        for section, title, link, _pub, _excerpt in errors:
            lines.append(f"- [{section}] {title} — {link}")

    lines.append("")
    lines.append("=" * 60)
    lines.append("本文件由本地脚本自动生成，可配合 launchd 每日运行。")
    return "\n".join(lines)


def render_brief_html(
    stories: list[StoryRow],
    llm_summary: dict[str, Any] | None,
    day: str,
    generated_at: datetime,
) -> str:
    ok, errors, theme_to_rows, themes_sorted, sections_seen = _compute_brief_data(stories)
    theme_cards = _theme_cards(theme_to_rows, themes_sorted)
    hero_summary = ""
    key_themes: list[dict[str, str]] = []
    impact_notes: list[str] = []
    watchlist: list[str] = []
    if llm_summary:
        hero_summary = str(llm_summary.get("heroSummary") or "").strip()
        key_themes = [item for item in llm_summary.get("keyThemes", []) if isinstance(item, dict)]
        impact_notes = [str(item).strip() for item in llm_summary.get("impactNotes", []) if str(item).strip()]
        watchlist = [str(item).strip() for item in llm_summary.get("watchlist", []) if str(item).strip()]

    nav_links = "".join(
        f'<a class="toc-link" href="#{card["anchor"]}">{_escape_html(card["theme"])}</a>'
        for card in theme_cards
    )
    summary_cards = "".join(
        (
            f'<article class="theme-pill" style="--accent:{card["accent"]};">'
            f'<div class="theme-pill__top"><span>{_escape_html(card["theme"])}</span>'
            f"<strong>{card['count']}</strong></div>"
            f'<p>{_escape_html(card["description"])}</p>'
            f'<small>{_escape_html("、".join(card["sources"]))}</small>'
            "</article>"
        )
        for card in theme_cards
    )

    if hero_summary:
        model_summary_html = f'<p class="hero-copy">{_escape_html(hero_summary)}</p>'
    else:
        model_summary_html = (
            '<p class="hero-copy hero-copy--muted">'
            "本期模型总结暂不可用，页面仍保留完整的原始聚合与主题整理。"
            "</p>"
        )

    key_theme_html = "".join(
        (
            '<article class="insight-card">'
            f'<h3>{_escape_html(item.get("title", ""))}</h3>'
            f'<p>{_escape_html(item.get("summary", ""))}</p>'
            "</article>"
        )
        for item in key_themes[:5]
        if item.get("title") and item.get("summary")
    )
    impact_html = "".join(f"<li>{_escape_html(item)}</li>" for item in impact_notes[:4])
    watch_html = "".join(f"<li>{_escape_html(item)}</li>" for item in watchlist[:4])

    theme_sections: list[str] = []
    for card in theme_cards:
        rows = theme_to_rows[card["theme"]]
        seen_links: set[str] = set()
        items_html: list[str] = []
        for section, title, link, excerpt in rows:
            if not link or link in seen_links:
                continue
            seen_links.add(link)
            items_html.append(
                "<article class=\"story-card\">"
                f"<div class=\"story-card__source\">{_escape_html(section)}</div>"
                f"<h3>{_escape_html(title)}</h3>"
                + (f"<p>{_escape_html(excerpt)}</p>" if excerpt else "")
                + f'<a href="{_escape_html(link)}" target="_blank" rel="noreferrer">查看原文</a>'
                + "</article>"
            )
        theme_sections.append(
            f'<section class="section-card" id="{card["anchor"]}">'
            f'<div class="section-head"><h2>{_escape_html(card["theme"])}</h2>'
            f"<span>{card['count']} 条</span></div>"
            f'<p class="section-note">{_escape_html(card["description"])}</p>'
            f'<div class="stories-grid">{"".join(items_html)}</div>'
            "</section>"
        )

    source_sections: list[str] = []
    current_section = None
    current_items: list[str] = []

    def flush_source_section(section_name: str | None, items: list[str]) -> None:
        if not section_name:
            return
        source_sections.append(
            f'<section class="section-card section-card--source" id="source-{_slugify(section_name)}">'
            f'<div class="section-head"><h2>{_escape_html(section_name)}</h2></div>'
            f'<div class="source-list">{"".join(items)}</div>'
            "</section>"
        )

    for section, title, link, pub, excerpt in stories:
        if section != current_section:
            flush_source_section(current_section, current_items)
            current_section = section
            current_items = []

        if title.startswith("[") and link and not link.startswith("http"):
            current_items.append(
                '<article class="source-item">'
                f"<h3>{_escape_html(title)}</h3>"
                f"<p>{_escape_html(link)}</p>"
                "</article>"
            )
            continue

        current_items.append(
            '<article class="source-item">'
            f"<div class=\"source-meta\">{_escape_html(pub or '无时间信息')}</div>"
            f"<h3>{_escape_html(title)}</h3>"
            + (f"<p>{_escape_html(excerpt)}</p>" if excerpt else "")
            + (
                f'<a href="{_escape_html(link)}" target="_blank" rel="noreferrer">原文链接</a>'
                if link.startswith("http")
                else ""
            )
            + "</article>"
        )
    flush_source_section(current_section, current_items)

    error_html = "".join(
        (
            '<li class="error-item">'
            f"<strong>{_escape_html(section)}</strong>"
            f"<span>{_escape_html(title)}</span>"
            f"<em>{_escape_html(link)}</em>"
            "</li>"
        )
        for section, title, link, _pub, _excerpt in errors
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>中文 AI 资讯简报 · {day}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Noto+Sans+SC:wght@400;500;700&display=swap" rel="stylesheet" />
  <style>
    :root {{
      --bg: #f5f4f1;
      --bg-soft: #eeece7;
      --paper: #fffdfa;
      --paper-2: #f8f5ef;
      --text: #28312d;
      --muted: #6f7771;
      --line: rgba(56, 68, 62, 0.10);
      --accent: #7a9b86;
      --accent-2: #c7b293;
      --shadow: 0 16px 45px rgba(60, 72, 66, 0.08);
      --radius: 22px;
      --radius-sm: 14px;
      --font: "DM Sans", "Noto Sans SC", system-ui, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      font-family: var(--font);
      background:
        radial-gradient(circle at top left, rgba(122, 155, 134, 0.12), transparent 32%),
        radial-gradient(circle at top right, rgba(199, 178, 147, 0.14), transparent 28%),
        var(--bg);
      color: var(--text);
      line-height: 1.65;
    }}
    a {{ color: #6d8878; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .app-shell {{
      width: min(1360px, calc(100vw - 32px));
      margin: 0 auto;
      display: grid;
      grid-template-columns: 220px minmax(0, 1fr);
      gap: 24px;
      padding: 24px 0 40px;
    }}
    .toc {{
      position: sticky;
      top: 18px;
      height: calc(100vh - 36px);
      overflow: auto;
      background: rgba(255, 253, 250, 0.76);
      backdrop-filter: blur(10px);
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 18px 14px 20px;
      box-shadow: var(--shadow);
    }}
    .toc h1 {{
      font-size: 0.95rem;
      margin: 0 0 10px;
      letter-spacing: 0.06em;
    }}
    .toc p {{
      margin: 0 0 14px;
      font-size: 0.8rem;
      color: var(--muted);
    }}
    .toc-link {{
      display: block;
      margin: 6px 0;
      padding: 10px 12px;
      border-radius: 12px;
      background: transparent;
      color: #49524d;
      border: 1px solid transparent;
      font-size: 0.9rem;
    }}
    .toc-link:hover {{
      text-decoration: none;
      background: rgba(122, 155, 134, 0.08);
      border-color: rgba(122, 155, 134, 0.16);
    }}
    .main {{
      min-width: 0;
    }}
    .hero {{
      background: linear-gradient(145deg, rgba(255, 253, 250, 0.96), rgba(248, 245, 239, 0.96));
      border: 1px solid var(--line);
      border-radius: 28px;
      padding: 34px 34px 28px;
      box-shadow: var(--shadow);
      overflow: hidden;
      position: relative;
    }}
    .hero::after {{
      content: "";
      position: absolute;
      right: -80px;
      top: -80px;
      width: 240px;
      height: 240px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(122, 155, 134, 0.16), transparent 68%);
      pointer-events: none;
    }}
    .eyebrow {{
      font-size: 0.8rem;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 12px;
    }}
    .hero h2 {{
      margin: 0;
      font-size: clamp(2rem, 4vw, 3.4rem);
      line-height: 1.08;
      letter-spacing: -0.04em;
      max-width: 10em;
    }}
    .hero-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px 14px;
      margin-top: 18px;
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .hero-copy {{
      max-width: 820px;
      margin-top: 20px;
      font-size: 1.02rem;
      color: #39423d;
    }}
    .hero-copy--muted {{
      color: var(--muted);
    }}
    .hero-stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-top: 28px;
    }}
    .stat-card {{
      background: rgba(255, 255, 255, 0.72);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px 16px 14px;
    }}
    .stat-card strong {{
      display: block;
      font-size: 1.45rem;
      margin-bottom: 4px;
    }}
    .stat-card span {{
      color: var(--muted);
      font-size: 0.88rem;
    }}
    .section-card {{
      margin-top: 22px;
      background: rgba(255, 253, 250, 0.94);
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 24px;
      box-shadow: var(--shadow);
      scroll-margin-top: 24px;
    }}
    .section-head {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
    }}
    .section-head h2 {{
      margin: 0;
      font-size: 1.2rem;
      letter-spacing: -0.02em;
    }}
    .section-head span {{
      color: var(--muted);
      font-size: 0.9rem;
    }}
    .section-note {{
      margin: 0 0 18px;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .theme-grid,
    .insight-grid,
    .stories-grid {{
      display: grid;
      gap: 14px;
    }}
    .theme-grid {{
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }}
    .insight-grid {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .stories-grid {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .theme-pill,
    .insight-card,
    .story-card,
    .source-item {{
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px;
    }}
    .theme-pill {{
      border-top: 3px solid var(--accent);
    }}
    .theme-pill__top {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
      font-weight: 600;
    }}
    .theme-pill p,
    .insight-card p,
    .story-card p,
    .source-item p {{
      margin: 0;
      color: #44504a;
      font-size: 0.95rem;
    }}
    .theme-pill small {{
      display: block;
      margin-top: 12px;
      color: var(--muted);
      font-size: 0.82rem;
    }}
    .insight-columns {{
      display: grid;
      grid-template-columns: 1.2fr 0.8fr;
      gap: 18px;
      align-items: start;
    }}
    .insight-side {{
      display: grid;
      gap: 14px;
    }}
    .insight-side ul {{
      margin: 10px 0 0 18px;
      padding: 0;
      color: #425049;
    }}
    .insight-side li + li {{
      margin-top: 8px;
    }}
    .story-card__source,
    .source-meta {{
      color: var(--muted);
      font-size: 0.82rem;
      letter-spacing: 0.04em;
      margin-bottom: 8px;
    }}
    .story-card h3,
    .source-item h3,
    .insight-card h3 {{
      margin: 0 0 10px;
      font-size: 1rem;
      line-height: 1.4;
    }}
    .story-card a,
    .source-item a {{
      display: inline-block;
      margin-top: 12px;
      font-size: 0.9rem;
      font-weight: 600;
    }}
    .source-list {{
      display: grid;
      gap: 12px;
    }}
    .error-list {{
      list-style: none;
      margin: 0;
      padding: 0;
      display: grid;
      gap: 12px;
    }}
    .error-item {{
      display: grid;
      gap: 4px;
      padding: 14px 16px;
      border-radius: 16px;
      background: #fff;
      border: 1px solid rgba(182, 123, 114, 0.18);
    }}
    .error-item strong {{
      color: #8f5a53;
    }}
    .footer-note {{
      color: var(--muted);
      text-align: center;
      font-size: 0.86rem;
      padding: 18px 0 8px;
    }}
    @media (max-width: 1100px) {{
      .app-shell {{
        grid-template-columns: 1fr;
      }}
      .toc {{
        position: relative;
        top: 0;
        height: auto;
      }}
    }}
    @media (max-width: 860px) {{
      .hero,
      .section-card {{
        padding: 18px;
      }}
      .hero-stats,
      .theme-grid,
      .insight-grid,
      .stories-grid,
      .insight-columns {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <div class="app-shell">
    <aside class="toc">
      <h1>AI Brief</h1>
      <p>每日聚合 + 主题整理 + 模型导语</p>
      <a class="toc-link" href="#hero">封面摘要</a>
      <a class="toc-link" href="#overview">主题地图</a>
      <a class="toc-link" href="#insights">模型总结</a>
      {nav_links}
      <a class="toc-link" href="#timeline">分源时间线</a>
      {'<a class="toc-link" href="#errors">抓取异常</a>' if errors else ''}
    </aside>
    <main class="main">
      <section class="hero" id="hero">
        <div class="eyebrow">Chinese AI Daily Brief</div>
        <h2>中文 AI 资讯简报</h2>
        <div class="hero-meta">
          <span>{_escape_html(day)}</span>
          <span>{_escape_html(generated_at.strftime('%H:%M'))}（北京时间）</span>
          <span>{_escape_html('、'.join(sections_seen))}</span>
        </div>
        {model_summary_html}
        <div class="hero-stats">
          <div class="stat-card"><strong>{len(ok)}</strong><span>有效条目</span></div>
          <div class="stat-card"><strong>{len(sections_seen)}</strong><span>来源栏目</span></div>
          <div class="stat-card"><strong>{len(theme_cards)}</strong><span>主题分组</span></div>
          <div class="stat-card"><strong>{len(errors)}</strong><span>抓取异常</span></div>
        </div>
      </section>

      <section class="section-card" id="overview">
        <div class="section-head"><h2>本期概览与主题地图</h2><span>帮助先看懂今天在发生什么</span></div>
        <p class="section-note">页面优先把信息压成可扫读的卡片，再保留完整时间线与原始链接，避免文本连在一起造成阅读疲劳。</p>
        <div class="theme-grid">{summary_cards}</div>
      </section>

      <section class="section-card" id="insights">
        <div class="section-head"><h2>模型总结</h2><span>作为导读，不替代原始新闻</span></div>
        <div class="insight-columns">
          <div>
            <div class="insight-grid">{key_theme_html or '<article class="insight-card"><h3>模型总结暂不可用</h3><p>当 API 未配置或调用失败时，这一块会自动降级，但页面主体仍会正常更新。</p></article>'}</div>
          </div>
          <div class="insight-side">
            <article class="insight-card">
              <h3>影响观察</h3>
              <ul>{impact_html or '<li>暂无额外点评。</li>'}</ul>
            </article>
            <article class="insight-card">
              <h3>持续关注</h3>
              <ul>{watch_html or '<li>暂无 watchlist。</li>'}</ul>
            </article>
          </div>
        </div>
      </section>

      {"".join(theme_sections)}

      <section class="section-card" id="timeline">
        <div class="section-head"><h2>分源原文索引</h2><span>保留完整时间线，便于回查</span></div>
        <p class="section-note">这里按媒体来源展开，方便从编辑部视角或来源可靠性视角回看当天信息流。</p>
        {"".join(source_sections)}
      </section>

      {f'<section class="section-card" id="errors"><div class="section-head"><h2>抓取异常</h2><span>不影响当期页面生成</span></div><ul class="error-list">{error_html}</ul></section>' if errors else ''}
      <div class="footer-note">本页由本地脚本自动生成，可配合 launchd 每日运行；纯文本备份与归档页同目录可用。</div>
    </main>
  </div>
</body>
</html>
"""


def build_archive_payload(
    stories: list[StoryRow],
    llm_summary: dict[str, Any] | None,
    day: str,
    generated_at: datetime,
) -> dict[str, Any]:
    ok, errors, theme_to_rows, themes_sorted, sections_seen = _compute_brief_data(stories)
    return {
        "date": day,
        "generated_at": generated_at.isoformat(),
        "timezone": "Asia/Shanghai",
        "stats": {
            "story_count": len(ok),
            "error_count": len(errors),
            "source_count": len(sections_seen),
            "theme_count": len(themes_sorted),
        },
        "sources": sections_seen,
        "themes": [
            {
                "name": theme,
                "count": len(theme_to_rows[theme]),
                "sources": sorted({section for section, _, _, _ in theme_to_rows[theme]}),
                "stories": [
                    {
                        "source": section,
                        "title": title,
                        "link": link,
                        "excerpt": excerpt,
                    }
                    for section, title, link, excerpt in theme_to_rows[theme]
                ],
            }
            for theme in themes_sorted
        ],
        "stories": [
            {
                "section": section,
                "title": title,
                "link": link,
                "published_at": pub,
                "excerpt": excerpt,
                "themes": [] if title.startswith("[") else _story_themes(title, excerpt),
            }
            for section, title, link, pub, excerpt in stories
        ],
        "errors": [
            {
                "section": section,
                "title": title,
                "detail": link,
            }
            for section, title, link, _pub, _excerpt in errors
        ],
        "llm_summary": llm_summary,
    }


def _render_calendar_html(days: list[str], latest_day: str) -> str:
    if not days:
        return "<p>暂无可展示的日期归档。</p>"

    parsed_days = sorted(datetime.strptime(day, "%Y-%m-%d") for day in days)
    available = {dt.strftime("%Y-%m-%d"): f"day-{dt.strftime('%Y-%m-%d')}" for dt in parsed_days}
    start = parsed_days[0]
    end = parsed_days[-1]

    month_blocks: list[str] = []
    year = start.year
    month = start.month
    while (year, month) <= (end.year, end.month):
        first_weekday, total_days = monthrange(year, month)
        cells: list[str] = []
        for _ in range(first_weekday):
            cells.append('<div class="calendar-cell calendar-cell--empty"></div>')
        for day_num in range(1, total_days + 1):
            day_key = f"{year:04d}-{month:02d}-{day_num:02d}"
            if day_key in available:
                latest_cls = " calendar-day--latest" if day_key == latest_day else ""
                cells.append(
                    f'<a class="calendar-cell calendar-day{latest_cls}" href="#" data-target="{_escape_html(available[day_key])}"><span>{day_num}</span></a>'
                )
            else:
                cells.append(f'<div class="calendar-cell calendar-cell--muted"><span>{day_num}</span></div>')

        month_blocks.append(
            '<section class="calendar-month">'
            f'<div class="calendar-month__title">{year} 年 {month:02d} 月</div>'
            '<div class="calendar-weekdays"><span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span>六</span><span>日</span></div>'
            f'<div class="calendar-grid">{"".join(cells)}</div>'
            "</section>"
        )
        month += 1
        if month == 13:
            month = 1
            year += 1

    return "".join(month_blocks)


def _load_archive_payload(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw)
    except Exception:
        return None


def render_index_html(out_dir: Path, latest_name: str) -> str:
    entries = sorted(out_dir.glob("AI简报-*.html"), key=lambda path: path.name, reverse=True)
    days = [path.stem.replace("AI简报-", "") for path in entries]
    latest_day = latest_name.removesuffix(".html").replace("AI简报-", "")
    calendar_html = _render_calendar_html(days, latest_day)
    nav_links = []
    day_sections = []
    total_story_count = 0
    latest_story_count = 0
    latest_theme_count = 0
    latest_source_count = 0
    for path in entries:
        day = path.stem.replace("AI简报-", "")
        is_latest = path.name == latest_name
        txt_name = f"AI简报-{day}.txt"
        json_name = f"AI简报-{day}.json"
        txt_exists = (out_dir / txt_name).exists()
        json_exists = (out_dir / json_name).exists()
        payload = _load_archive_payload(out_dir / json_name) if json_exists else None
        section_id = f"day-{day}"

        stats = payload.get("stats", {}) if payload else {}
        story_count = int(stats.get("story_count", 0) or 0)
        source_count = int(stats.get("source_count", 0) or 0)
        theme_count = int(stats.get("theme_count", 0) or 0)
        error_count = int(stats.get("error_count", 0) or 0)
        total_story_count += story_count
        if is_latest:
            latest_story_count = story_count
            latest_theme_count = theme_count
            latest_source_count = source_count

        llm_summary = payload.get("llm_summary") if payload else None
        hero_summary = ""
        key_themes: list[dict[str, Any]] = []
        impact_notes: list[str] = []
        watchlist: list[str] = []
        if isinstance(llm_summary, dict):
            hero_summary = str(llm_summary.get("heroSummary") or "").strip()
            key_themes = [item for item in llm_summary.get("keyThemes", []) if isinstance(item, dict)]
            impact_notes = [str(item).strip() for item in llm_summary.get("impactNotes", []) if str(item).strip()]
            watchlist = [str(item).strip() for item in llm_summary.get("watchlist", []) if str(item).strip()]

        theme_items = payload.get("themes", []) if payload else []
        story_items = payload.get("stories", []) if payload else []
        source_items = payload.get("sources", []) if payload else []

        theme_cards = []
        for theme in theme_items[:4]:
            theme_name = str(theme.get("name") or "其他 AI 科技动态")
            accent, desc = _THEME_ACCENTS.get(theme_name, _THEME_ACCENTS["其他 AI 科技动态"])
            stories_html = []
            for story in theme.get("stories", [])[:3]:
                title = _escape_html(str(story.get("title") or ""))
                source = _escape_html(str(story.get("source") or ""))
                link = _escape_html(str(story.get("link") or "#"))
                excerpt = _truncate(str(story.get("excerpt") or "").strip(), 96)
                excerpt_html = f'<p>{_escape_html(excerpt)}</p>' if excerpt else ""
                stories_html.append(
                    '<li>'
                    f'<a href="{link}" target="_blank" rel="noopener noreferrer">{title}</a>'
                    f'<span>{source}</span>'
                    f'{excerpt_html}'
                    '</li>'
                )
            theme_cards.append(
                '<article class="theme-card">'
                f'<span class="theme-accent" style="background:{_escape_html(accent)}"></span>'
                f'<div class="theme-card__head"><h3>{_escape_html(theme_name)}</h3><span>{int(theme.get("count", 0) or 0)} 条</span></div>'
                f'<p class="theme-card__desc">{_escape_html(desc)}</p>'
                f'<ul class="mini-story-list">{"".join(stories_html) or "<li><span>暂无细分条目</span></li>"}</ul>'
                '</article>'
            )

        quick_story_rows = []
        for story in story_items[:10]:
            title = _escape_html(str(story.get("title") or ""))
            source = _escape_html(str(story.get("section") or ""))
            link = _escape_html(str(story.get("link") or "#"))
            published_at = _escape_html(str(story.get("published_at") or ""))
            excerpt = _truncate(str(story.get("excerpt") or "").strip(), 140)
            meta = " · ".join([part for part in [source, published_at] if part])
            quick_story_rows.append(
                '<article class="story-row">'
                f'<a class="story-row__title" href="{link}" target="_blank" rel="noopener noreferrer">{title}</a>'
                + (f'<div class="story-row__meta">{meta}</div>' if meta else "")
                + (f"<p>{_escape_html(excerpt)}</p>" if excerpt else "")
                + '</article>'
            )

        impact_html = "".join(f"<li>{_escape_html(item)}</li>" for item in impact_notes[:4]) or "<li>暂无补充观察。</li>"
        watch_html = "".join(f"<li>{_escape_html(item)}</li>" for item in watchlist[:4]) or "<li>暂无持续关注项。</li>"
        key_theme_html = "".join(
            '<li>'
            f"<strong>{_escape_html(str(item.get('title') or '重点主题'))}</strong>"
            f"<span>{_escape_html(str(item.get('summary') or ''))}</span>"
            '</li>'
            for item in key_themes[:4]
        )
        if not key_theme_html:
            key_theme_html = "<li><strong>模型总结暂不可用</strong><span>页面仍会保留当天主题和原始资讯，便于回查。</span></li>"

        source_html = "".join(
            f'<span class="source-pill">{_escape_html(str(source))}</span>'
            for source in source_items[:8]
        ) or '<span class="source-pill">暂无来源信息</span>'
        theme_grid_html = "".join(theme_cards) or '<p class="empty-copy">暂无主题聚合内容。</p>'
        story_list_html = "".join(quick_story_rows) or '<p class="empty-copy">暂无资讯条目。</p>'

        nav_links.append(
            f'<a class="date-chip{" date-chip--latest" if is_latest else ""}{" is-active" if is_latest else ""}" href="#" data-target="{_escape_html(section_id)}">{_escape_html(day)}{" · 最新" if is_latest else ""}</a>'
        )
        day_sections.append(
            f'<article class="day-section{" is-active" if is_latest else ""}" '
            f'id="{_escape_html(section_id)}"'
            f' data-day="{_escape_html(day)}"'
            f' data-stories="{story_count}"'
            f' data-themes="{theme_count}"'
            f' data-sources="{source_count}"'
            f' data-label="{_escape_html("最新一期" if is_latest else "历史归档")}">'
            '<div class="day-section__top">'
            '<div>'
            f'<div class="day-kicker">{"最新一期" if is_latest else "历史归档"}</div>'
            f'<h2>{_escape_html(day)} AI 每日资讯</h2>'
            f'<p>{_escape_html(hero_summary or "这一天的简报已归档在此，可直接从顶部日期切换到别的日期并继续浏览。")}</p>'
            '</div>'
            '<div class="day-actions">'
            f'<a class="action-primary" href="{_escape_html(path.name)}" target="_blank" rel="noopener noreferrer">单独打开网页</a>'
            + (
                f'<a class="action-secondary" href="{_escape_html(txt_name)}" target="_blank" rel="noopener noreferrer">TXT</a>'
                if txt_exists
                else ""
            )
            + (
                f'<a class="action-secondary" href="{_escape_html(json_name)}" target="_blank" rel="noopener noreferrer">JSON</a>'
                if json_exists
                else ""
            )
            + '</div>'
            + '</div>'
            '<div class="stat-row">'
            f'<span class="stat-pill">资讯 {story_count}</span>'
            f'<span class="stat-pill">主题 {theme_count}</span>'
            f'<span class="stat-pill">来源 {source_count}</span>'
            f'<span class="stat-pill">异常 {error_count}</span>'
            '</div>'
            '<div class="content-layout">'
            '<section class="content-panel">'
            '<div class="panel-head"><h3>模型抓手</h3><span>快速看懂这一天</span></div>'
            f'<ul class="summary-list">{key_theme_html}</ul>'
            '<div class="panel-head panel-head--spaced"><h3>重点主题</h3><span>点击上方日期，下面内容会直接切换</span></div>'
            f'<div class="theme-grid">{theme_grid_html}</div>'
            '</section>'
            '<aside class="content-side">'
            '<section class="side-card">'
            '<h3>影响观察</h3>'
            f'<ul>{impact_html}</ul>'
            '</section>'
            '<section class="side-card">'
            '<h3>持续关注</h3>'
            f'<ul>{watch_html}</ul>'
            '</section>'
            '<section class="side-card">'
            '<h3>来源分布</h3>'
            f'<div class="source-row">{source_html}</div>'
            '</section>'
            '</aside>'
            '</div>'
            '<section class="content-panel content-panel--stories">'
            '<div class="panel-head"><h3>当日资讯列表</h3><span>保留原始链接，方便继续深挖</span></div>'
            f'<div class="story-list">{story_list_html}</div>'
            '</section>'
            '</article>'
        )

    nav_html = "".join(nav_links) if nav_links else '<span class="date-chip">暂无归档</span>'
    sections_html = "".join(day_sections) if day_sections else "<p>暂无归档。</p>"
    overview_html = (
        f'<article class="overview-card"><span>归档天数</span><strong>{len(entries)}</strong><p>每天都会保留在同一页里切换查看。</p></article>'
        f'<article class="overview-card"><span>累计资讯</span><strong>{total_story_count}</strong><p>保留网页、TXT、JSON 三份归档。</p></article>'
        f'<article class="overview-card"><span>最新一期</span><strong>{_escape_html(latest_day)}</strong><p>{latest_story_count} 条资讯 · {latest_theme_count} 个主题 · {latest_source_count} 个来源</p></article>'
    ) if entries else ""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AI 每日简报归档</title>
  <style>
    body {{
      margin: 0;
      font-family: "DM Sans", "Noto Sans SC", system-ui, sans-serif;
      background: #f5f4f1;
      color: #28312d;
    }}
    a {{
      color: inherit;
    }}
    .sticky-date-nav {{
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      z-index: 30;
      background: rgba(245,244,241,.92);
      backdrop-filter: blur(14px);
      border-bottom: 1px solid rgba(56,68,62,.08);
    }}
    .sticky-date-nav__inner {{
      width: min(1180px, calc(100vw - 24px));
      margin: 0 auto;
      padding: 12px 0;
    }}
    .sticky-date-nav__label {{
      margin: 0 0 8px;
      font-size: .84rem;
      color: #66706a;
      letter-spacing: .02em;
    }}
    .selected-strip {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      margin-top: 10px;
      padding: 12px 14px;
      border-radius: 16px;
      background: rgba(255,255,255,.72);
      border: 1px solid rgba(56,68,62,.08);
      box-shadow: inset 0 1px 0 rgba(255,255,255,.4);
    }}
    .selected-strip__date {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-weight: 800;
      color: #314039;
    }}
    .selected-strip__meta {{
      color: #61706a;
      font-size: .92rem;
      text-align: right;
    }}
    .date-chip-row {{
      display: flex;
      gap: 10px;
      overflow-x: auto;
      padding-bottom: 4px;
      scrollbar-width: none;
    }}
    .date-chip-row::-webkit-scrollbar {{
      display: none;
    }}
    .date-chip {{
      flex: 0 0 auto;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 10px 14px;
      border-radius: 999px;
      background: rgba(255,255,255,.88);
      border: 1px solid rgba(56,68,62,.10);
      text-decoration: none;
      color: #5d6b64;
      font-weight: 700;
      font-size: .92rem;
      white-space: nowrap;
      box-shadow: 0 8px 20px rgba(60,72,66,.05);
    }}
    .date-chip:hover,
    .date-chip.is-active {{
      background: #6f8a7a;
      border-color: #6f8a7a;
      color: #fff;
    }}
    .date-chip--latest {{
      border-color: rgba(111,138,122,.28);
    }}
    .wrap {{
      width: min(1180px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 104px 0 56px;
    }}
    .hero {{
      background: linear-gradient(145deg, rgba(255,253,250,.96), rgba(248,245,239,.96));
      border: 1px solid rgba(56,68,62,.10);
      border-radius: 28px;
      padding: 30px;
      margin-bottom: 22px;
      box-shadow: 0 16px 45px rgba(60,72,66,.08);
    }}
    .hero h1 {{
      margin: 0 0 10px;
      font-size: clamp(1.8rem, 4vw, 3rem);
      letter-spacing: -.04em;
    }}
    .hero p {{
      margin: 0;
      color: #6f7771;
      max-width: 680px;
    }}
    .overview-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      margin-top: 18px;
    }}
    .overview-card {{
      background: rgba(255,255,255,.72);
      border: 1px solid rgba(56,68,62,.08);
      border-radius: 18px;
      padding: 16px 18px;
    }}
    .overview-card span {{
      display: block;
      font-size: .84rem;
      color: #6c7570;
      margin-bottom: 8px;
    }}
    .overview-card strong {{
      display: block;
      font-size: clamp(1.4rem, 3vw, 2rem);
      letter-spacing: -.03em;
      margin-bottom: 6px;
    }}
    .overview-card p {{
      margin: 0;
      color: #6b746e;
      font-size: .92rem;
      max-width: none;
    }}
    .calendar-panel {{
      background: #fffdfa;
      border: 1px solid rgba(56,68,62,.10);
      border-radius: 22px;
      padding: 20px;
      margin-bottom: 18px;
      box-shadow: 0 10px 30px rgba(60,72,66,.06);
    }}
    .calendar-panel h2 {{
      margin: 0 0 8px;
      font-size: 1.15rem;
    }}
    .calendar-panel p {{
      margin: 0 0 16px;
      color: #6f7771;
    }}
    .calendar-stack {{
      display: grid;
      gap: 18px;
    }}
    .calendar-month {{
      background: rgba(245,244,241,.72);
      border: 1px solid rgba(56,68,62,.08);
      border-radius: 18px;
      padding: 14px;
    }}
    .calendar-month__title {{
      font-weight: 700;
      margin-bottom: 12px;
    }}
    .calendar-weekdays,
    .calendar-grid {{
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
      gap: 8px;
    }}
    .calendar-weekdays {{
      margin-bottom: 8px;
      color: #7a827d;
      font-size: .82rem;
      text-align: center;
    }}
    .calendar-cell {{
      min-height: 42px;
      border-radius: 12px;
      display: flex;
      align-items: center;
      justify-content: center;
      text-decoration: none;
      border: 1px solid transparent;
      background: #fff;
      color: #33403a;
      font-weight: 600;
    }}
    .calendar-cell--empty {{
      background: transparent;
      border-color: transparent;
      min-height: 42px;
    }}
    .calendar-cell--muted {{
      background: rgba(255,255,255,.45);
      color: #a1a7a3;
      border-color: rgba(56,68,62,.05);
    }}
    .calendar-day {{
      background: rgba(111,138,122,.10);
      border-color: rgba(111,138,122,.16);
      color: #4f675a;
    }}
    .calendar-day:hover {{
      text-decoration: none;
      background: rgba(111,138,122,.18);
    }}
    .calendar-day.is-active {{
      background: #6f8a7a;
      color: #fff;
      border-color: #6f8a7a;
    }}
    .calendar-day--latest {{
      background: #6f8a7a;
      color: #fff;
      border-color: #6f8a7a;
    }}
    .daily-stack {{
      display: grid;
      gap: 20px;
    }}
    .day-section {{
      display: none;
      background: #fffdfa;
      border: 1px solid rgba(56,68,62,.10);
      border-radius: 26px;
      padding: 24px;
      box-shadow: 0 14px 36px rgba(60,72,66,.06);
    }}
    .day-section.is-active {{
      display: block;
      animation: fadeIn .22s ease;
    }}
    .day-section__top {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 16px;
    }}
    .day-kicker {{
      color: #6f8a7a;
      font-size: .85rem;
      font-weight: 700;
      margin-bottom: 8px;
    }}
    .day-section h2 {{
      margin: 0 0 10px;
      font-size: clamp(1.4rem, 3vw, 2rem);
      letter-spacing: -.03em;
    }}
    .day-section__top p {{
      margin: 0;
      color: #69736d;
      max-width: 760px;
    }}
    .day-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      justify-content: flex-end;
    }}
    .action-primary,
    .action-secondary {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 42px;
      padding: 0 14px;
      border-radius: 12px;
      text-decoration: none;
      font-weight: 600;
    }}
    .action-primary {{
      background: #6f8a7a;
      color: #fff;
    }}
    .action-secondary {{
      background: rgba(111,138,122,.10);
      border: 1px solid rgba(111,138,122,.16);
      color: #5a7365;
    }}
    .stat-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 18px;
    }}
    .stat-pill,
    .source-pill {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 34px;
      padding: 0 12px;
      border-radius: 12px;
      background: rgba(111,138,122,.10);
      color: #577061;
      font-weight: 600;
      font-size: .9rem;
      border: 1px solid rgba(111,138,122,.12);
    }}
    .content-layout {{
      display: grid;
      grid-template-columns: minmax(0, 1.7fr) minmax(280px, .9fr);
      gap: 18px;
      margin-bottom: 18px;
    }}
    .content-panel,
    .side-card {{
      background: rgba(245,244,241,.7);
      border: 1px solid rgba(56,68,62,.08);
      border-radius: 20px;
      padding: 18px;
    }}
    .content-panel--stories {{
      background: rgba(255,255,255,.72);
    }}
    .panel-head {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }}
    .panel-head--spaced {{
      margin-top: 20px;
    }}
    .panel-head h3,
    .side-card h3 {{
      margin: 0;
      font-size: 1.02rem;
    }}
    .panel-head span,
    .summary-list span,
    .side-card li,
    .story-row__meta,
    .mini-story-list span,
    .theme-card__desc {{
      color: #6b746e;
    }}
    .summary-list,
    .side-card ul,
    .mini-story-list {{
      margin: 0;
      padding-left: 18px;
    }}
    .summary-list {{
      display: grid;
      gap: 10px;
    }}
    .summary-list li strong,
    .mini-story-list a {{
      display: block;
      margin-bottom: 4px;
    }}
    .theme-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
    }}
    .theme-card {{
      position: relative;
      background: #fff;
      border: 1px solid rgba(56,68,62,.08);
      border-radius: 18px;
      padding: 16px;
      box-shadow: 0 8px 24px rgba(60,72,66,.04);
    }}
    .theme-accent {{
      display: block;
      width: 42px;
      height: 4px;
      border-radius: 999px;
      margin-bottom: 12px;
    }}
    .theme-card__head {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: baseline;
      margin-bottom: 8px;
    }}
    .theme-card__head h3 {{
      margin: 0;
      font-size: 1rem;
    }}
    .theme-card__head span {{
      color: #607168;
      font-size: .88rem;
      white-space: nowrap;
    }}
    .theme-card__desc {{
      margin: 0 0 12px;
      font-size: .9rem;
    }}
    .mini-story-list p,
    .story-row p {{
      margin: 6px 0 0;
      color: #56635d;
    }}
    .content-side {{
      display: grid;
      gap: 14px;
      align-content: start;
    }}
    .source-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .story-list {{
      display: grid;
      gap: 12px;
    }}
    .story-row {{
      background: #fff;
      border: 1px solid rgba(56,68,62,.08);
      border-radius: 16px;
      padding: 14px 16px;
    }}
    .story-row__title {{
      font-weight: 700;
      text-decoration: none;
    }}
    .story-row__title:hover,
    .mini-story-list a:hover {{
      text-decoration: underline;
    }}
    .story-row__meta {{
      margin-top: 6px;
      font-size: .88rem;
    }}
    .empty-copy {{
      margin: 0;
      color: #6b746e;
    }}
    @keyframes fadeIn {{
      from {{
        opacity: 0;
        transform: translateY(8px);
      }}
      to {{
        opacity: 1;
        transform: translateY(0);
      }}
    }}
    @media (max-width: 760px) {{
      .sticky-date-nav__inner {{
        width: calc(100vw - 16px);
      }}
      .wrap {{
        width: calc(100vw - 20px);
        padding: 96px 0 28px;
      }}
      .hero,
      .calendar-panel,
      .day-section,
      .content-panel,
      .side-card {{
        border-radius: 18px;
        padding: 16px;
      }}
      .selected-strip,
      .overview-card {{
        border-radius: 14px;
      }}
      .selected-strip {{
        align-items: flex-start;
        flex-direction: column;
      }}
      .selected-strip__meta {{
        text-align: left;
      }}
      .overview-grid {{
        grid-template-columns: 1fr;
      }}
      .date-chip {{
        font-size: .88rem;
        padding: 9px 12px;
      }}
      .calendar-weekdays,
      .calendar-grid {{
        gap: 6px;
      }}
      .calendar-cell {{
        min-height: 36px;
        border-radius: 10px;
        font-size: .92rem;
      }}
      .day-section__top,
      .content-layout {{
        grid-template-columns: 1fr;
      }}
      .day-actions {{
        width: 100%;
        justify-content: stretch;
      }}
      .action-primary,
      .action-secondary {{
        flex: 1 1 auto;
        text-align: center;
        padding: 10px 12px;
      }}
    }}
  </style>
</head>
<body>
  <div class="sticky-date-nav">
    <div class="sticky-date-nav__inner">
      <p class="sticky-date-nav__label">日期导航会固定在页面顶部，点击后下方内容直接切换，不再整页跳转</p>
      <div class="date-chip-row">{nav_html}</div>
      <div class="selected-strip">
        <div class="selected-strip__date" id="selected-date">正在查看：{_escape_html(latest_day)}</div>
        <div class="selected-strip__meta" id="selected-meta">{latest_story_count} 条资讯 · {latest_theme_count} 个主题 · {latest_source_count} 个来源</div>
      </div>
    </div>
  </div>
  <div class="wrap">
    <section class="hero">
      <h1>AI 每日简报归档</h1>
      <p>这里会按天留存网页、纯文本和结构化 JSON 三份归档。顶部日期始终固定，点击后下方内容会在同页直接切换；`latest.html` 仍会始终指向最新一期。</p>
      <div class="overview-grid">{overview_html}</div>
    </section>
    <section class="calendar-panel">
      <h2>日期日历</h2>
      <p>你可以点日历，也可以直接点页面最上方的日期条；两者都会在同一页切换到对应日期内容。</p>
      <div class="calendar-stack">{calendar_html}</div>
    </section>
    <section class="daily-stack">{sections_html}</section>
  </div>
  <script>
    (() => {{
      const chips = Array.from(document.querySelectorAll('.date-chip[data-target]'));
      const calendarDays = Array.from(document.querySelectorAll('.calendar-day[data-target]'));
      const sections = Array.from(document.querySelectorAll('.day-section[id]'));
      const selectedDate = document.getElementById('selected-date');
      const selectedMeta = document.getElementById('selected-meta');
      const controls = [...chips, ...calendarDays];
      if (!controls.length || !sections.length) return;

      const setActive = (id) => {{
        controls.forEach((control) => control.classList.toggle('is-active', control.dataset.target === id));
        sections.forEach((section) => {{
          const isActive = section.id === id;
          section.classList.toggle('is-active', isActive);
          if (isActive) {{
            if (selectedDate) {{
              const label = section.dataset.label || '';
              selectedDate.textContent = `正在查看：${{section.dataset.day || ''}}${{label ? ' · ' + label : ''}}`;
            }}
            if (selectedMeta) {{
              selectedMeta.textContent = `${{section.dataset.stories || 0}} 条资讯 · ${{section.dataset.themes || 0}} 个主题 · ${{section.dataset.sources || 0}} 个来源`;
            }}
          }}
        }});
        if (history.replaceState) {{
          history.replaceState(null, '', '#' + id);
        }}
        const activeChip = chips.find((chip) => chip.dataset.target === id);
        if (activeChip && activeChip.scrollIntoView) {{
          activeChip.scrollIntoView({{ inline: 'center', block: 'nearest', behavior: 'smooth' }});
        }}
      }};

      controls.forEach((control) => {{
        control.addEventListener('click', (event) => {{
          event.preventDefault();
          setActive(control.dataset.target);
        }});
      }});

      const hashId = location.hash ? location.hash.slice(1) : '';
      const initialId = sections.some((section) => section.id === hashId) ? hashId : (sections.find((section) => section.classList.contains('is-active')) || sections[0]).id;
      setActive(initialId);
    }})();
  </script>
</body>
</html>
"""


def render_index_timeline_html(out_dir: Path, latest_name: str) -> str:
    entries = sorted(out_dir.glob("AI简报-*.html"), key=lambda path: path.name, reverse=True)
    latest_day = latest_name.removesuffix(".html").replace("AI简报-", "")
    timeline_rows: list[str] = []
    total_story_count = 0
    latest_story_count = 0
    latest_source_count = 0

    for idx, path in enumerate(entries):
        day = path.stem.replace("AI简报-", "")
        payload = _load_archive_payload(out_dir / f"AI简报-{day}.json")
        stats = payload.get("stats", {}) if payload else {}
        story_count = int(stats.get("story_count", 0) or 0)
        source_count = int(stats.get("source_count", 0) or 0)
        theme_count = int(stats.get("theme_count", 0) or 0)
        error_count = int(stats.get("error_count", 0) or 0)
        total_story_count += story_count
        if path.name == latest_name:
            latest_story_count = story_count
            latest_source_count = source_count

        llm_summary = payload.get("llm_summary") if payload else None
        key_themes = llm_summary.get("keyThemes", []) if isinstance(llm_summary, dict) else []
        theme_items = payload.get("themes", []) if payload else []
        story_items = payload.get("stories", []) if payload else []
        source_items = payload.get("sources", []) if payload else []
        hero_summary = str((llm_summary or {}).get("heroSummary") or "").strip() if isinstance(llm_summary, dict) else ""

        display_date = datetime.strptime(day, "%Y-%m-%d").strftime("%b %d")
        first_theme = key_themes[0] if key_themes and isinstance(key_themes[0], dict) else {}
        headline = str(first_theme.get("title") or "").strip()
        if not headline and theme_items:
            headline = str(theme_items[0].get("name") or "").strip()
        if not headline and story_items:
            headline = str(story_items[0].get("title") or "").strip()
        if not headline:
            headline = "not much happened today"

        tag_links: list[tuple[str, str]] = []
        for theme in theme_items[:7]:
            name = str(theme.get("name") or "").strip()
            if name:
                tag_links.append((name, f"{path.name}#{_slugify(name)}"))
        for source in source_items[:5]:
            name = str(source or "").strip()
            if name:
                tag_links.append((name, f"{path.name}#source-{_slugify(name)}"))
        tag_names = [name for name, _href in tag_links]
        tags_html = "".join(
            f'<a href="{_escape_html(href)}">{_escape_html(tag)}</a>'
            for tag, href in tag_links[:12]
        )

        story_links = []
        for story in story_items[:6]:
            title = str(story.get("title") or "").strip()
            link = str(story.get("link") or "#").strip()
            source = str(story.get("section") or "").strip()
            if not title:
                continue
            story_links.append(
                '<li>'
                f'<a href="{_escape_html(link)}" target="_blank" rel="noopener noreferrer">{_escape_html(title)}</a>'
                + (f"<small>{_escape_html(source)}</small>" if source else "")
                + '</li>'
            )

        summary = hero_summary
        if not summary and first_theme:
            summary = str(first_theme.get("summary") or "").strip()
        if not summary:
            summary = "这一天的简报已归档，可展开查看主题、来源和重点原文。"

        timeline_rows.append(
            '<details class="timeline-item"'
            f' data-title="{_escape_html((headline + " " + " ".join(tag_names)).lower())}"'
            f'{" open" if idx == 0 else ""}>'
            '<summary>'
            '<span class="timeline-dot" aria-hidden="true"></span>'
            f'<time datetime="{_escape_html(day)}">{_escape_html(display_date)}</time>'
            f'<strong>{_escape_html(headline)}</strong>'
            f'<span class="row-meta">{story_count} 条 · {theme_count} 主题 · {source_count} 来源</span>'
            '<span class="chevron" aria-hidden="true">›</span>'
            '</summary>'
            '<div class="timeline-detail">'
            f'<p>{_escape_html(summary)}</p>'
            f'<div class="tag-row">{tags_html or "<span>暂无标签</span>"}</div>'
            f'<ul class="issue-links">{"".join(story_links) or "<li><span>暂无资讯条目</span></li>"}</ul>'
            '<div class="issue-actions">'
            f'<a href="{_escape_html(path.name)}" target="_blank" rel="noopener noreferrer">打开当日网页</a>'
            f'<a href="AI简报-{_escape_html(day)}.txt" target="_blank" rel="noopener noreferrer">TXT</a>'
            f'<a href="AI简报-{_escape_html(day)}.json" target="_blank" rel="noopener noreferrer">JSON</a>'
            + (f"<span>异常 {error_count}</span>" if error_count else "")
            + '</div>'
            + '</div>'
            + '</details>'
        )

    rows_html = "".join(timeline_rows) or '<p class="empty-copy">暂无归档。</p>'
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>人工智能新闻</title>
  <style>
    :root {{
      --bg: #f5f5f4;
      --text: #171717;
      --muted: #737373;
      --line: #d9d9d6;
      --card: #f7f7f6;
      --card-hover: #ffffff;
      --shadow: 0 1px 2px rgba(0,0,0,.03);
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", sans-serif;
      font-size: 14px;
      line-height: 1.5;
    }}
    a {{ color: inherit; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .site-shell {{
      width: min(1180px, calc(100vw - 48px));
      margin: 0 auto;
      padding: 22px 0 72px;
    }}
    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      min-height: 30px;
    }}
    .brand {{
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 0 7px;
      background: #050505;
      color: #fff;
      font-weight: 700;
      letter-spacing: .03em;
      font-size: 13px;
    }}
    .top-links {{
      display: flex;
      align-items: center;
      gap: 8px;
      color: #111;
      font-size: 13px;
    }}
    .top-links a,
    .search-pill {{
      color: #111;
      opacity: .86;
    }}
    .search-pill {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 0 9px;
      border: 1px solid var(--line);
      border-radius: 4px;
      background: rgba(255,255,255,.46);
      font-size: 12px;
    }}
    .hero {{
      min-height: 188px;
      display: grid;
      place-items: center;
      text-align: center;
      color: rgba(0,0,0,.16);
      font-size: clamp(15px, 2vw, 22px);
      font-weight: 650;
      letter-spacing: .02em;
      user-select: none;
    }}
    .timeline-head {{
      display: grid;
      grid-template-columns: 1fr auto 1fr;
      align-items: center;
      gap: 24px;
      margin-bottom: 28px;
    }}
    .timeline-head h1 {{
      margin: 0;
      font-size: 15px;
      line-height: 1;
      font-weight: 750;
    }}
    .filter-box {{
      display: flex;
      align-items: center;
      gap: 8px;
      color: #555;
      font-size: 13px;
    }}
    .filter-box input {{
      width: min(220px, 32vw);
      height: 28px;
      border: 1px solid var(--line);
      border-radius: 4px;
      background: rgba(255,255,255,.58);
      padding: 0 9px;
      outline: none;
      color: #222;
    }}
    .filter-box input:focus {{
      border-color: #9f9f9b;
      background: #fff;
    }}
    .all-link {{
      justify-self: end;
      color: #222;
      text-decoration: underline;
      text-underline-offset: 2px;
      font-size: 13px;
    }}
    .timeline {{
      position: relative;
      padding-left: 32px;
    }}
    .timeline::before {{
      content: "";
      position: absolute;
      left: 12px;
      top: 0;
      bottom: 0;
      width: 1px;
      background: var(--line);
    }}
    .timeline-item {{
      position: relative;
      margin: 0 0 14px;
    }}
    .timeline-item[hidden] {{ display: none; }}
    .timeline-item summary {{
      position: relative;
      display: grid;
      grid-template-columns: 92px minmax(180px, 1fr) auto 20px;
      align-items: center;
      gap: 16px;
      min-height: 40px;
      padding: 0 10px;
      list-style: none;
      cursor: pointer;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--card);
      box-shadow: var(--shadow);
    }}
    .timeline-item summary::-webkit-details-marker {{ display: none; }}
    .timeline-item summary:hover {{
      background: var(--card-hover);
    }}
    .timeline-dot {{
      position: absolute;
      left: -24px;
      top: 50%;
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: #777;
      transform: translateY(-50%);
      box-shadow: 0 0 0 4px var(--bg);
    }}
    time {{
      color: #777;
      font-size: 13px;
      white-space: nowrap;
    }}
    summary strong {{
      font-size: 14px;
      font-weight: 760;
      color: #262626;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .row-meta {{
      color: #777;
      font-size: 12px;
      white-space: nowrap;
    }}
    .chevron {{
      color: #3f3f3f;
      font-size: 26px;
      line-height: 1;
      transform: translateY(-1px);
      transition: transform .15s ease;
    }}
    .timeline-item[open] .chevron {{
      transform: rotate(90deg) translateX(-1px);
    }}
    .timeline-detail {{
      margin: 8px 0 18px;
      padding: 16px 18px 18px;
      border: 1px solid var(--line);
      border-top: 0;
      border-radius: 0 0 8px 8px;
      background: rgba(255,255,255,.46);
      color: #303030;
    }}
    .timeline-detail p {{
      margin: 0 0 12px;
      max-width: 920px;
    }}
    .tag-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
      margin-bottom: 14px;
      color: #686868;
      font-size: 12px;
    }}
    .tag-row a,
    .tag-row span {{
      padding: 2px 7px;
      border: 1px solid #ddddda;
      border-radius: 999px;
      background: rgba(255,255,255,.55);
    }}
    .tag-row a:hover {{
      background: #fff;
      border-color: #bdbdb8;
      text-decoration: none;
    }}
    .issue-links {{
      margin: 0;
      padding-left: 18px;
      display: grid;
      gap: 7px;
    }}
    .issue-links small {{
      margin-left: 8px;
      color: var(--muted);
    }}
    .issue-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 16px;
      color: #444;
      font-size: 13px;
    }}
    .issue-actions a {{
      text-decoration: underline;
      text-underline-offset: 2px;
    }}
    .empty-copy,
    .invalid-copy {{
      color: var(--muted);
    }}
    .invalid-copy {{
      display: none;
      margin-left: 8px;
      font-size: 12px;
    }}
    .footer {{
      margin: 44px 0 0 32px;
      color: var(--muted);
      font-size: 13px;
    }}
    @media (max-width: 760px) {{
      .site-shell {{
        width: min(100vw - 24px, 1180px);
        padding-top: 14px;
      }}
      .topbar,
      .timeline-head {{
        grid-template-columns: 1fr;
      }}
      .topbar {{
        align-items: flex-start;
      }}
      .top-links {{
        flex-wrap: wrap;
      }}
      .hero {{
        min-height: 110px;
      }}
      .timeline-head {{
        display: grid;
        gap: 12px;
      }}
      .filter-box {{
        align-items: flex-start;
        flex-direction: column;
      }}
      .filter-box input {{
        width: 100%;
      }}
      .all-link {{
        justify-self: start;
      }}
      .timeline {{
        padding-left: 24px;
      }}
      .timeline::before {{
        left: 9px;
      }}
      .timeline-item summary {{
        grid-template-columns: 64px minmax(0, 1fr) 18px;
        gap: 10px;
        min-height: 46px;
      }}
      .timeline-dot {{
        left: -19px;
      }}
      .row-meta {{
        display: none;
      }}
      summary strong {{
        white-space: normal;
      }}
    }}
  </style>
</head>
<body>
  <div class="site-shell">
    <header class="topbar">
      <a class="brand" href="index.html">人工智能新闻</a>
      <nav class="top-links" aria-label="站点导航">
        <a href="latest.html">最新</a><span>/</span>
        <a href="index.html">归档</a><span>/</span>
        <a href="#timeline">标签</a><span>/</span>
        <span class="search-pill">搜索(Cmd+K)</span>
      </nav>
    </header>

    <section class="hero" aria-label="站点介绍">
      <div>中文 AI 每日简报 · {len(entries)} days · {total_story_count} stories</div>
    </section>

    <section class="timeline-head">
      <h1>Last 30 days in AI</h1>
      <label class="filter-box">
        <span>Filter titles:</span>
        <input id="title-filter" type="text" value="^((?!not much).)*$" aria-describedby="filter-error" />
        <span class="invalid-copy" id="filter-error">Invalid regex</span>
      </label>
      <a class="all-link" href="latest.html">See all issues</a>
    </section>

    <main class="timeline" id="timeline">
      {rows_html}
    </main>

    <footer class="footer">
      最新一期：{_escape_html(latest_day)} · {latest_story_count} 条资讯 · {latest_source_count} 个来源 · 本页由本地脚本自动生成
    </footer>
  </div>

  <script>
    (() => {{
      const input = document.getElementById('title-filter');
      const error = document.getElementById('filter-error');
      const items = Array.from(document.querySelectorAll('.timeline-item[data-title]'));
      if (!input || !items.length) return;

      const applyFilter = () => {{
        let regex = null;
        const value = input.value.trim();
        if (value) {{
          try {{
            regex = new RegExp(value, 'i');
            if (error) error.style.display = 'none';
          }} catch (_err) {{
            if (error) error.style.display = 'inline';
            return;
          }}
        }} else if (error) {{
          error.style.display = 'none';
        }}
        items.forEach((item) => {{
          item.hidden = Boolean(regex && !regex.test(item.dataset.title || ''));
        }});
      }};

      input.addEventListener('input', applyFilter);
      applyFilter();
    }})();
  </script>
</body>
</html>
"""


def render_latest_html(target_name: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="refresh" content="0; url={_escape_html(target_name)}" />
  <title>跳转到最新一期简报</title>
</head>
<body>
  <p>正在跳转到最新一期简报：<a href="{_escape_html(target_name)}">{_escape_html(target_name)}</a></p>
</body>
</html>
"""


def _github_api_request(url: str, token: str, method: str = "GET", data: dict[str, Any] | None = None) -> dict[str, Any] | None:
    body = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except HTTPError as e:
        if e.code == 404:
            return None
        raise


def publish_static_site(out_dir: Path, owner: str, repo: str, token: str, subdir: str = "") -> str | None:
    files = []
    for pattern in ("*.html", "*.txt", "*.json"):
        files.extend(sorted(out_dir.glob(pattern)))
    if not files:
        return None

    clean_subdir = subdir.strip().strip("/")
    if repo == f"{owner}.github.io":
        pages_url = f"https://{owner}.github.io/"
    else:
        pages_url = f"https://{owner}.github.io/{repo}/"
    if clean_subdir:
        pages_url += f"{clean_subdir}/"

    repo_info = _github_api_request(f"https://api.github.com/repos/{owner}/{repo}", token)
    branch = (repo_info or {}).get("default_branch", "main")
    for src in files:
        remote_path = f"{clean_subdir}/{src.name}" if clean_subdir else src.name
        encoded_path = urllib.parse.quote(remote_path, safe="/")
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{encoded_path}"
        existing = _github_api_request(url, token)
        payload = {
            "message": f"Update AI daily brief site: {src.name}",
            "content": base64.b64encode(src.read_bytes()).decode("ascii"),
            "branch": branch,
        }
        if existing and isinstance(existing, dict) and existing.get("sha"):
            payload["sha"] = existing["sha"]
        _github_api_request(url, token, method="PUT", data=payload)
    return pages_url


def mac_notify(title: str, body: str, sound: str | None = "Glass") -> None:
    if os.environ.get("AI_DAILY_BRIEF_NO_NOTIFY"):
        return
    body = body.replace('"', '\\"')
    title = title.replace('"', '\\"')
    script = f'display notification "{body}" with title "{title}"'
    if sound:
        script += f' sound name "{sound}"'
    try:
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def main() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    home = Path.home()
    out_dir_env = os.environ.get("AI_DAILY_BRIEF_OUT_DIR")
    if out_dir_env:
        out_dir = Path(out_dir_env).expanduser().resolve()
    else:
        out_dir = home / "Documents" / "AI每日简报"
    out_dir.mkdir(parents=True, exist_ok=True)
    _load_optional_env_files([
        repo_root / "scripts" / "ai-daily-brief.env",
        out_dir / ".env",
    ])

    now = datetime.now(TZ_CN)
    day = now.strftime("%Y-%m-%d")
    txt_path = out_dir / f"AI简报-{day}.txt"
    html_path = out_dir / f"AI简报-{day}.html"
    json_path = out_dir / f"AI简报-{day}.json"
    index_path = out_dir / "index.html"
    latest_path = out_dir / "latest.html"

    stories = collect_stories()
    ok, errors, theme_to_rows, themes_sorted, _sections_seen = _compute_brief_data(stories)
    llm_summary = generate_llm_summary(ok, errors, theme_to_rows, themes_sorted)

    text = render_brief(stories)
    html_doc = render_brief_html(stories, llm_summary, day, now)
    archive_payload = build_archive_payload(stories, llm_summary, day, now)

    txt_path.write_text(text, encoding="utf-8")
    html_path.write_text(html_doc, encoding="utf-8")
    json_path.write_text(json.dumps(archive_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    index_path.write_text(render_index_html(out_dir, html_path.name), encoding="utf-8")
    latest_path.write_text(render_latest_html(html_path.name), encoding="utf-8")

    published_url = None
    publish_error = None
    publish_owner = os.environ.get("AI_DAILY_BRIEF_PUBLISH_OWNER")
    publish_repo = os.environ.get("AI_DAILY_BRIEF_PUBLISH_REPO")
    publish_token = os.environ.get("AI_DAILY_BRIEF_PUBLISH_TOKEN")
    publish_subdir = os.environ.get("AI_DAILY_BRIEF_PUBLISH_SUBDIR", "")
    if publish_owner and publish_repo and publish_token:
        try:
            published_url = publish_static_site(out_dir, publish_owner, publish_repo, publish_token, publish_subdir)
        except Exception as exc:
            published_url = None
            publish_error = str(exc)
            print(f"[publish] failed: {publish_error}", file=sys.stderr)

    preview = textwrap.shorten(text.replace("\n", " "), width=180, placeholder="…")
    body = f"网页版：{html_path.name}。{preview}"
    if published_url:
        body = f"已同步：{published_url}"
    elif publish_error:
        body = f"已更新：{html_path.name}（公网同步失败）"
    mac_notify("中文 AI 每日简报已更新", body)
    print(published_url or html_path)
    return html_path


if __name__ == "__main__":
    main()

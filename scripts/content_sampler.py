#!/usr/bin/env python3
"""Content sampler + teardown pipeline for AI account topic discovery.

This is not a competitor metrics crawler. It treats every input as a content
object, then analyzes hook, structure, proof, commercial entrance, and how it
can become the user's own AI-business-system-director topic.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import push_to_feishu as feishu
from feishu_table_registry import resolve_table_id, table_name


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
REPORT_DIR = OUT / "daily_reports"
CONTENT_SOURCES = ROOT / "config" / "content_sources.yaml"
MANUAL_ITEMS = ROOT / "data" / "manual" / "content_items.example.jsonl"
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


BUSINESS_KEYWORDS = {
    "内容团队选题到Brief流程": ["内容", "选题", "脚本", "Brief", "写作", "公众号", "小红书", "素材"],
    "AI导演工作流与视频交付": ["视频", "镜头", "分镜", "导演", "成片", "画面", "剪辑", "prompt"],
    "非技术Agent处理重复业务任务": ["Agent", "智能体", "Codex", "Claude Code", "MCP", "自动化", "任务"],
    "汽车与内容营销流程": ["汽车", "品牌", "营销", "带货", "素材", "审核", "信任", "增长"],
    "项目复盘与能力产品化": ["复盘", "创业", "产品", "服务", "咨询", "案例", "Build"],
}

COLUMN_BY_SCENE = {
    "内容团队选题到Brief流程": "真实工作流改造",
    "AI导演工作流与视频交付": "AI导演工作流",
    "非技术Agent处理重复业务任务": "真实工作流改造",
    "汽车与内容营销流程": "汽车与内容营销",
    "项目复盘与能力产品化": "AI项目复盘",
}

COLUMN_ALIASES = {
    "AI汽车与品牌增长": "汽车与内容营销",
    "非技术Agent实战": "真实工作流改造",
}

SOURCE_TYPE_ALIASES = {
    "competitor_article": "公众号文章",
    "competitor_video": "对标视频",
}

TOP10_COLUMN_LIMITS = {
    "AI业务定调": (1, 2),
    "真实工作流改造": (2, 3),
    "汽车与内容营销": (2, 3),
    "AI导演工作流": (2, 3),
    "AI项目复盘": (0, 1),
}
CONTENT_INBOX_FIELDS = [
    "标题",
    "来源类型",
    "来源名称",
    "平台",
    "链接",
    "发布时间",
    "采集时间",
    "采集状态",
    "失败原因",
    "摘要/片段",
    "作者/账号",
    "内容指纹",
    "正文/全文",
    "正文长度",
    "是否全文解析",
    "原始payload路径",
    "解析说明",
    "运行日期",
    "运行批次",
    "是否本次新增",
    "最近参与运行批次",
    "最近采样日期",
    "是否重复",
    "处理状态",
]


def normalize_column(column: str) -> str:
    return COLUMN_ALIASES.get(column, column)


def normalize_source_type(source_type: str) -> str:
    return SOURCE_TYPE_ALIASES.get(source_type, source_type)


@dataclass
class ContentItem:
    source_type: str
    platform: str
    account_name: str
    title: str
    url: str
    content_shape: str
    cover_text: str
    body_snippet: str
    published_at: str
    comment_questions: str
    ocr_text: str
    fetch_method: str
    fetch_status: str
    failure_reason: str
    fingerprint: str
    column: str = ""
    learn_focus: str = ""
    do_not_copy: str = ""
    convert_direction: str = ""
    raw_text_length: int = 0
    body_truncated: str = ""
    reused_url: str = "否"


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_script = False
        self.parts: list[str] = []
        self.headings: list[str] = []
        self.links: list[str] = []
        self.images: list[str] = []
        self.current_tag = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.current_tag = tag
        if tag in {"script", "style", "noscript"}:
            self.in_script = True
        attr = dict(attrs)
        if tag == "img":
            alt = attr.get("alt") or attr.get("data-src") or attr.get("src") or ""
            if alt:
                self.images.append(alt[:160])
        if tag == "a":
            href = attr.get("href") or ""
            text = attr.get("title") or href
            if href:
                self.links.append(text[:160])

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self.in_script = False
        self.current_tag = ""

    def handle_data(self, data: str) -> None:
        if self.in_script:
            return
        text = normalize_space(data)
        if not text:
            return
        self.parts.append(text)
        if self.current_tag in {"h1", "h2", "h3"}:
            self.headings.append(text)


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("name") or item.get("value") or ""))
            else:
                parts.append(cell_text(item))
        return normalize_space(" ".join(part for part in parts if part))
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or value.get("value") or "")
    return str(value)


def fingerprint(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_slug() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def default_run_id() -> str:
    return os.getenv("RUN_ID") or os.getenv("AI_ACCOUNT_RADAR_RUN_ID") or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_text(url: str) -> tuple[str, str]:
    req = Request(url, headers={"User-Agent": DEFAULT_UA})
    try:
        with urlopen(req, timeout=20) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace"), "ok"
    except HTTPError as exc:
        return "", f"http_{exc.code}"
    except (URLError, TimeoutError) as exc:
        return "", f"failed:{exc.__class__.__name__}"


def fetch_json(url: str) -> tuple[dict[str, Any] | None, str, str]:
    text, status = fetch_text(url)
    if status != "ok":
        return None, status, ""
    try:
        return json.loads(text), "ok", ""
    except json.JSONDecodeError as exc:
        preview = normalize_space(text)[:500]
        return None, f"failed:JSONDecodeError line={exc.lineno} col={exc.colno} msg={exc.msg}", preview


def aihot_daily_rows(data: dict[str, Any], source: dict[str, Any]) -> list[ContentItem]:
    rows: list[ContentItem] = []
    generated_at = data.get("generatedAt", "")
    daily_date = data.get("date", "")
    for section in data.get("sections", []) or []:
        label = section.get("label", "") or "AIHOT日报"
        for item in section.get("items", []) or []:
            title = item.get("title", "")
            url = item.get("sourceUrl", "") or item.get("url", "")
            summary = item.get("summary", "") or ""
            source_name = item.get("sourceName", "") or source["account_name"]
            body = f"{summary} {label}".strip()
            rows.append(ContentItem(
                source_type="AIHOT热点",
                platform="AIHOT",
                account_name=source_name,
                title=title,
                url=url,
                content_shape=f"日报条目/{label}",
                cover_text="",
                body_snippet=body,
                published_at=generated_at or daily_date,
                comment_questions="",
                ocr_text="",
                fetch_method="aihot_daily_api",
                fetch_status="ok",
                failure_reason="",
                fingerprint=fingerprint(url, title, source_name, daily_date),
                column=normalize_column(source.get("column", "")),
                learn_focus=source.get("learn_focus", ""),
                do_not_copy=source.get("do_not_copy", ""),
                convert_direction=source.get("convert_direction", ""),
                raw_text_length=len(body),
                body_truncated="否",
            ))
    return rows


def meta_content(page: str, names: list[str]) -> str:
    for name in names:
        patterns = [
            rf'<meta[^>]+property=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+name=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(name)}["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, page, flags=re.I)
            if match:
                return normalize_space(match.group(1))
    return ""


def title_from_html(page: str) -> str:
    return meta_content(page, ["og:title", "twitter:title", "title"]) or normalize_space(re.sub(r"<.*?>", "", re.search(r"<title[^>]*>(.*?)</title>", page, flags=re.I | re.S).group(1))) if re.search(r"<title[^>]*>(.*?)</title>", page, flags=re.I | re.S) else ""


def extract_article(url: str, fallback: dict[str, Any]) -> ContentItem:
    page, status = fetch_text(url)
    failure = "" if status == "ok" else status
    title = fallback.get("内容标题") or fallback.get("title") or ""
    body = fallback.get("正文/字幕/简介片段") or fallback.get("body_snippet") or ""
    cover = fallback.get("封面文字") or fallback.get("cover_text") or ""
    ocr = fallback.get("截图/OCR文本") or fallback.get("ocr_text") or ""
    if status == "ok":
        extractor = TextExtractor()
        extractor.feed(page)
        page_title = title_from_html(page)
        title = title or page_title
        description = meta_content(page, ["og:description", "description"])
        joined = " ".join(extractor.parts)
        body = normalize_space(" ".join([description, joined]))[:5000] or body
        cover = cover or " / ".join(extractor.headings[:5])
        ocr = ocr or " / ".join(extractor.images[:8])
    fp = fingerprint(url, title, fallback.get("账号名/公众号名", ""))
    return ContentItem(
        source_type="公众号文章",
        platform=fallback.get("平台", "微信公众号/公开网页"),
        account_name=fallback.get("账号名/公众号名", fallback.get("account_name", "")),
        title=title or "未命名公众号文章",
        url=url,
        content_shape="长文",
        cover_text=cover,
        body_snippet=body,
        published_at=fallback.get("发布时间", ""),
        comment_questions=fallback.get("评论区问题", ""),
        ocr_text=ocr,
        fetch_method="public_article_url",
        fetch_status=status,
        failure_reason=failure,
        fingerprint=fp,
    )


def extract_video_shallow(url: str, fallback: dict[str, Any]) -> ContentItem:
    page, status = fetch_text(url) if url else ("", "manual_only")
    title = fallback.get("内容标题") or fallback.get("title") or ""
    body = fallback.get("正文/字幕/简介片段") or fallback.get("body_snippet") or ""
    cover = fallback.get("封面文字") or fallback.get("cover_text") or ""
    comments = fallback.get("评论区问题") or fallback.get("comment_questions") or ""
    ocr = fallback.get("截图/OCR文本") or fallback.get("ocr_text") or ""
    if status == "ok":
        extractor = TextExtractor()
        extractor.feed(page)
        title = title or title_from_html(page)
        desc = meta_content(page, ["og:description", "description"])
        visible = " ".join(extractor.parts[:80])
        body = body or normalize_space(" ".join([desc, visible]))[:2500]
        cover = cover or meta_content(page, ["og:title"]) or title
        ocr = ocr or " / ".join(extractor.images[:8])
    failure = "" if status == "ok" or status == "manual_only" else status
    if status != "ok" and not body and not cover:
        failure = failure or "页面不可公开解析，需手动粘贴标题、封面、简介、字幕或截图OCR。"
    fp = fingerprint(url, title, fallback.get("账号名/公众号名", ""))
    return ContentItem(
        source_type="对标视频",
        platform=fallback.get("平台", fallback.get("platform", "短视频平台")),
        account_name=fallback.get("账号名/公众号名", fallback.get("account_name", "")),
        title=title or "未命名对标视频",
        url=url,
        content_shape=fallback.get("内容形态", "短视频"),
        cover_text=cover,
        body_snippet=body,
        published_at=fallback.get("发布时间", ""),
        comment_questions=comments,
        ocr_text=ocr,
        fetch_method="video_shallow_or_manual",
        fetch_status=status if body or cover else "failed",
        failure_reason=failure,
        fingerprint=fp,
    )


def aihot_items(source: dict[str, Any], fetch: bool) -> tuple[list[ContentItem], list[str]]:
    logs: list[str] = []
    if not fetch:
        return [], ["AIHOT: skipped"]
    if not source.get("url"):
        return [], [f"{source.get('account_name', 'AIHOT')}: skipped_missing_url"]
    data, status, preview = fetch_json(source["url"])
    if preview:
        logs.append(f"{source['account_name']}: {status} | url={source['url']} | preview={preview[:300]}")
    else:
        logs.append(f"{source['account_name']}: {status} | url={source['url']}")
    if not data:
        return [], logs
    if isinstance(data.get("sections"), list):
        rows = aihot_daily_rows(data, source)
        logs.append(f"{source['account_name']}: parsed_daily_sections={len(rows)}")
        return rows, logs
    rows: list[ContentItem] = []
    for item in data.get("items", []):
        title = item.get("title", "")
        url = item.get("url", "")
        summary = item.get("summary", "") or ""
        category = item.get("category", "") or ""
        source_name = item.get("source", "") or source["account_name"]
        rows.append(ContentItem(
            source_type="AIHOT热点",
            platform="AIHOT",
            account_name=source_name,
            title=title,
            url=url,
            content_shape="热点条目",
            cover_text="",
            body_snippet=f"{summary} {category}".strip(),
            published_at=item.get("publishedAt", ""),
            comment_questions="",
            ocr_text="",
            fetch_method="aihot_api",
            fetch_status="ok",
            failure_reason="",
            fingerprint=fingerprint(url, title, source_name),
            column=normalize_column(source.get("column", "")),
            learn_focus=source.get("learn_focus", ""),
            do_not_copy=source.get("do_not_copy", ""),
            convert_direction=source.get("convert_direction", ""),
            raw_text_length=len(f"{summary} {category}".strip()),
            body_truncated="否",
        ))
    return rows, logs


def load_manual_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            print(f"[warn] manual content line {line_no} skipped: {exc}", file=sys.stderr)
    return rows


def load_payload_text(payload_path: str) -> str:
    if not payload_path:
        return ""
    path = Path(payload_path)
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")[:20000]
    except OSError:
        return ""


def load_reused_content_ledger(manual_rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    if not any(row.get("是否来自已解析URL复用") == "是" for row in manual_rows):
        return {}
    if not all(os.getenv(name) for name in ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_BASE_APP_TOKEN"]):
        return {}
    try:
        app_token = str(os.getenv("FEISHU_BASE_APP_TOKEN"))
        token = feishu.tenant_token()
        table_id = resolve_table_id(list_tables(token, app_token), "content_inbox")
        if not table_id:
            return {}
        records = all_records(token, app_token, table_id)
    except Exception as exc:
        print(f"[warn] reused URL ledger lookup skipped: {exc}", file=sys.stderr)
        return {}
    ledger: dict[str, dict[str, str]] = {}
    for record in records:
        fields = record.get("fields", {})
        normalized = {name: cell_text(value) for name, value in fields.items()}
        for key_name in ("内容指纹", "链接"):
            key = normalized.get(key_name, "")
            if key:
                ledger[key] = normalized
    return ledger


def enrich_reused_item_from_ledger(item: ContentItem, ledger: dict[str, dict[str, str]]) -> None:
    fields = ledger.get(item.fingerprint) or ledger.get(item.url) or {}
    if not fields:
        return
    full_body = fields.get("正文/全文", "")
    payload_path = fields.get("原始payload路径", "")
    if len(full_body) < 500:
        payload_text = load_payload_text(payload_path)
        if payload_text:
            full_body = payload_text
    if full_body:
        item.body_snippet = full_body
    raw_len = fields.get("正文长度", "")
    if raw_len.isdigit():
        item.raw_text_length = max(item.raw_text_length, int(raw_len))
    elif full_body:
        item.raw_text_length = max(item.raw_text_length, len(full_body))
    if payload_path:
        item.ocr_text = payload_path
    if fields.get("是否全文解析") == "是" and not item.body_truncated:
        item.body_truncated = "否"


def collect_items(fetch_aihot: bool, manual_path: Path) -> tuple[list[ContentItem], list[str]]:
    config = load_json(CONTENT_SOURCES)
    sources = config["sources"]
    items: list[ContentItem] = []
    logs: list[str] = []
    source_by_name = {source["account_name"]: source for source in sources}

    for source in sources:
        if not source.get("default_enabled", True):
            continue
        if normalize_source_type(source["source_type"]) == "AIHOT热点":
            rows, source_logs = aihot_items(source, fetch_aihot)
            items.extend(rows)
            logs.extend(source_logs)

    manual_rows = load_manual_items(manual_path)
    reused_ledger = load_reused_content_ledger(manual_rows)
    for raw in manual_rows:
        source_type = normalize_source_type(raw.get("来源类型", raw.get("source_type", "手动补充")))
        url = raw.get("内容链接", raw.get("url", ""))
        account = raw.get("账号名/公众号名", raw.get("account_name", ""))
        source_meta = source_by_name.get(account, {})
        if source_meta.get("default_enabled") is False:
            source_meta = {}
        fetch_method = raw.get("抓取方式", raw.get("fetch_method", ""))
        is_resolved_url_item = fetch_method in {
            "wechat_public_html_js_content",
            "douyin_public_router_data",
            "rss_atom_xml",
            "jina_reader",
        }
        if is_resolved_url_item:
            fp = raw.get("内容指纹") or fingerprint(url, raw.get("内容标题", ""), account)
            item = ContentItem(
                source_type=source_type,
                platform=raw.get("平台", ""),
                account_name=account,
                title=raw.get("内容标题", "未命名内容"),
                url=url,
                content_shape=raw.get("内容形态", ""),
                cover_text=raw.get("封面文字", ""),
                body_snippet=raw.get("正文/字幕/简介片段", ""),
                published_at=raw.get("发布时间", ""),
                comment_questions=raw.get("评论区问题", ""),
                ocr_text=raw.get("截图/OCR文本", ""),
                fetch_method=fetch_method,
                fetch_status=raw.get("抓取状态", "success"),
                failure_reason=raw.get("失败原因", ""),
                fingerprint=fp,
                raw_text_length=int(raw.get("正文原始长度") or len(raw.get("正文/字幕/简介片段", ""))),
                body_truncated=raw.get("正文是否截断", ""),
                reused_url=raw.get("是否来自已解析URL复用", "否"),
            )
            if item.reused_url == "是":
                enrich_reused_item_from_ledger(item, reused_ledger)
        elif source_type == "公众号文章" and url:
            item = extract_article(url, raw)
        elif source_type == "对标视频":
            item = extract_video_shallow(url, raw)
        else:
            fp = fingerprint(url, raw.get("内容标题", ""), account)
            item = ContentItem(
                source_type=source_type,
                platform=raw.get("平台", ""),
                account_name=account,
                title=raw.get("内容标题", "未命名内容"),
                url=url,
                content_shape=raw.get("内容形态", ""),
                cover_text=raw.get("封面文字", ""),
                body_snippet=raw.get("正文/字幕/简介片段", ""),
                published_at=raw.get("发布时间", ""),
                comment_questions=raw.get("评论区问题", ""),
                ocr_text=raw.get("截图/OCR文本", ""),
                fetch_method=raw.get("抓取方式", "manual_jsonl"),
                fetch_status=raw.get("抓取状态", "ok"),
                failure_reason=raw.get("失败原因", ""),
                fingerprint=fp,
                reused_url=raw.get("是否来自已解析URL复用", "否"),
            )
        item.source_type = normalize_source_type(item.source_type)
        item.column = normalize_column(item.column or source_meta.get("column", ""))
        item.learn_focus = item.learn_focus or source_meta.get("learn_focus", "")
        item.do_not_copy = item.do_not_copy or source_meta.get("do_not_copy", "")
        item.convert_direction = item.convert_direction or source_meta.get("convert_direction", "")
        items.append(item)

    seen: set[str] = set()
    deduped = []
    for item in items:
        if item.fingerprint in seen:
            continue
        seen.add(item.fingerprint)
        deduped.append(item)
    return deduped, logs


def item_text(item: ContentItem) -> str:
    # Use the content itself for classification. Source-level learn_focus is
    # useful context for explanations, but it is too broad for scoring.
    return " ".join([item.title, item.cover_text, item.body_snippet, item.comment_questions, item.ocr_text])


def choose_scene(text: str) -> str:
    lower = text.lower()
    visual_content_terms = ["公众号首图", "小红书卡片", "教程步骤卡", "视觉模板", "图文卡", "视觉物料", "宣传图"]
    if "claude code" in lower and any(k in text for k in ["原则", "团队", "工作方式", "验收", "harness"]):
        return "非技术Agent处理重复业务任务"
    if any(k in text for k in ["内容创作方法论", "内容创作", "内部分享"]):
        return "内容团队选题到Brief流程"
    if any(k in lower for k in ["miso", "speech", "voice", "asr", "grok imagine", "ppisp", "photometric"]) or any(k in text for k in ["语音模型", "口播", "视频生成", "3D重建", "光度变化"]):
        return "AI导演工作流与视频交付"
    if any(k in text for k in visual_content_terms):
        return "内容团队选题到Brief流程"
    if any(k.lower() in lower for k in ["runway", "kling", "luma", "seedance", "视频", "分镜", "镜头", "成片", "短剧", "宣传图", "视觉物料"]):
        return "AI导演工作流与视频交付" if any(k.lower() in lower for k in ["runway", "kling", "luma", "seedance", "视频", "分镜", "镜头", "成片", "短剧"]) else "内容团队选题到Brief流程"
    if any(k.lower() in lower for k in ["llamaindex", "openrouter", "codex", "claude code", "agent", "guardrails", "mcp", "自动化", "api", "智能体", "文档自动化"]):
        return "非技术Agent处理重复业务任务"
    if any(k.lower() in lower for k in ["shein", "ai假人", "虚假广告", "带货", "品牌", "营销", "信任", "合规", "审核", "骗子", "汽车"]):
        return "汽车与内容营销流程"
    if any(k.lower() in lower for k in ["build in public", "产品化", "服务入口", "模板包", "咨询", "项目搭建"]):
        return "项目复盘与能力产品化"
    scores = {
        scene: sum(1 for word in words if word.lower() in text.lower())
        for scene, words in BUSINESS_KEYWORDS.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "内容团队选题到Brief流程"


def detect_hook(item: ContentItem) -> str:
    text = f"{item.title} {item.cover_text} {item.body_snippet[:160]}"
    if any(k in text for k in ["别再", "不要", "不是", "为什么"]):
        return "反常识/纠错型：先指出常见误区，让用户停下来看。"
    if any(k in text for k in ["3个", "三个", "清单", "模板", "SOP"]):
        return "可领取资产型：用清单、模板或步骤承诺具体收获。"
    if any(k in text for k in ["前后", "对比", "差距", "修改"]):
        return "结果对比型：用前后差异证明方法有效。"
    return "判断先行型：先给结论，再解释为什么。"


def infer_structure(item: ContentItem) -> str:
    text = item_text(item)
    if item.source_type == "公众号文章":
        return "痛点开场 -> 分层解释 -> 案例/方法 -> 可执行清单 -> CTA/服务入口"
    if "分镜" in text or "镜头" in text or "视频" in text:
        return "问题钩子 -> Brief/分镜 -> 镜头或画面对比 -> 修改逻辑 -> 验收/CTA"
    if "Agent" in text or "智能体" in text or "自动化" in text:
        return "任务痛点 -> 输入输出拆解 -> 工具/Agent步骤 -> 验收标准 -> 模板CTA"
    return "冲突句 -> 核心判断 -> 示例拆解 -> 可执行动作 -> CTA"


def proof_method(item: ContentItem) -> str:
    text = item_text(item)
    proofs = []
    if any(k in text for k in ["案例", "客户", "项目", "实战", "复盘"]):
        proofs.append("用真实案例/项目复盘证明懂行")
    if any(k in text for k in ["对比", "前后", "修改", "成片"]):
        proofs.append("用前后对比或成片结果证明方法")
    if any(k in text for k in ["模板", "清单", "SOP", "流程"]):
        proofs.append("用模板、清单或流程证明可交付")
    return "；".join(proofs) or "主要靠观点判断，需要补你的真实业务现场来证明"


def commercial_entrance(item: ContentItem) -> str:
    text = item_text(item)
    if any(k in text for k in ["领取", "私信", "回复", "资料包", "模板"]):
        return "有资料包/模板/私信入口"
    if any(k in text for k in ["咨询", "诊断", "课程", "服务", "合作"]):
        return "有咨询/服务/合作入口"
    return "未看到明确商业入口，可转成轻资料包或诊断CTA"


def asset_for_scene(scene: str) -> str:
    return {
        "内容团队选题到Brief流程": "内容选题评分表和Brief模板",
        "AI导演工作流与视频交付": "AI视频Brief与分镜验收清单",
        "非技术Agent处理重复业务任务": "非技术Agent任务拆解模板",
        "汽车与内容营销流程": "品牌AI素材审核清单 / 汽车内容营销SOP",
        "项目复盘与能力产品化": "AI项目复盘与服务化检查表",
    }.get(scene, "业务流程改造清单")


def old_pain(scene: str) -> str:
    return {
        "内容团队选题到Brief流程": "追热点和看对标很多，但选题缺判断，Brief缺业务场景，发布后难复盘。",
        "AI导演工作流与视频交付": "只会写prompt和展示画面，缺Brief、分镜、修改逻辑和验收标准。",
        "非技术Agent处理重复业务任务": "把Agent当聊天机器人，任务边界、输入输出和验收标准不清。",
        "汽车与内容营销流程": "AI素材、卖点表达和带货内容进入投放前，缺少品牌一致性、真实性、合规性和风险审核。",
        "项目复盘与能力产品化": "只记录动作和情绪，没有把能力沉淀成模板、案例和服务入口。",
    }[scene]


def ai_entry(scene: str) -> str:
    return {
        "内容团队选题到Brief流程": "用AI做资料筛选、内容拆解、选题评分和Brief提纲，但最终判断由人完成。",
        "AI导演工作流与视频交付": "AI生成素材，人负责Brief、分镜、节奏、修改和验收。",
        "非技术Agent处理重复业务任务": "把重复任务拆成输入、步骤、输出、验收和异常处理，再交给Agent执行可重复部分。",
        "汽车与内容营销流程": "用AI辅助识别素材风险、提炼产品卖点、检查品牌调性和生成投放前审核清单，但最终由人确认。",
        "项目复盘与能力产品化": "用AI整理项目过程、失败点、可复用模板和服务入口。",
    }[scene]


def show_result(scene: str) -> str:
    return {
        "内容团队选题到Brief流程": "旧流程/新流程对比表 + 可领取Brief模板",
        "AI导演工作流与视频交付": "一页Brief + 三个镜头节点 + 修改前后对比",
        "非技术Agent处理重复业务任务": "任务拆解表 + Agent输入输出样例 + 验收清单",
        "汽车与内容营销流程": "AI素材审核流程图 + 汽车/品牌内容投放前风险清单",
        "项目复盘与能力产品化": "项目复盘卡 + 能力产品化清单",
    }[scene]


def business_profile(scene: str, angle_type: str) -> dict[str, str]:
    if angle_type == "品牌风控/信任危机":
        return {
            "业务场景": "AI营销素材审核与品牌风控",
            "旧流程痛点": "AI生成素材、虚拟人设、带货内容进入投放前，缺少真实性、合规性、品牌一致性和风险审核。",
            "AI介入点": "用AI辅助识别虚假人设、素材风险、夸张承诺、AI生成标识缺失和品牌调性偏差，但最终由人确认。",
            "可展示结果": "AI素材审核流程图 + 投放前风险清单",
            "可沉淀资产": "品牌AI素材审核清单 / AI营销风控SOP",
        }
    if angle_type == "产品生死线":
        return {
            "业务场景": "AI产品壁垒与中间层工具判断",
            "旧流程痛点": "看到模型、框架或平台更新时，只复述参数和发布信息，没判断哪些产品壁垒会被削弱、哪些工具需要重做。",
            "AI介入点": "用AI辅助拆解能力变化、受影响产品层、替代路径和非技术用户行动建议，但关键判断由人确认。",
            "可展示结果": "AI产品壁垒影响判断表",
            "可沉淀资产": "AI工具壁垒判断清单",
        }
    return {
        "业务场景": scene,
        "旧流程痛点": old_pain(scene),
        "AI介入点": ai_entry(scene),
        "可展示结果": show_result(scene),
        "可沉淀资产": asset_for_scene(scene),
    }


def short_title(title: str) -> str:
    clean = re.sub(r"[：:丨|｜].*$", "", title).strip()
    return clean[:22] + ("..." if len(clean) > 22 else "")


def title_token(title: str, limit: int = 18) -> str:
    clean = re.sub(r"\s+", " ", title or "").strip()
    clean = re.sub(r"[#＃].*$", "", clean).strip()
    clean = re.sub(r"[：:丨|｜].*$", "", clean).strip()
    return clean[:limit] + ("..." if len(clean) > limit else "")


def title_template_key(title: str) -> str:
    return title_structure_template(title)


def title_structure_template(title: str) -> str:
    if "背后，哪类AI工具会先失去壁垒" in title:
        return "product_wall_generic"
    if "更新后，业务人该先判断它会改掉哪段工作流" in title:
        return "generic_model_update_workflow"
    if re.search(r".+后，.+先看.+", title):
        return "after_audience_look"
    if re.search(r".+后，.+先补.+", title):
        return "after_audience_patch"
    if re.search(r".+后，.+最该.+", title):
        return "after_audience_should"
    if re.search(r".+不是.+，而是.+", title):
        return "not_a_but_b"
    if title.startswith("非技术人看") and "先看" in title:
        return "nontech_look_first"
    if re.search(r".+发布后，.+先验证.+", title):
        return "release_validate_first"
    if re.search(r".+背后，.+该重做.+", title):
        return "behind_redo"
    if re.search(r".+后，.+该怎么判断.+", title):
        return "after_how_judge"
    if "最先变的是哪一步" in title:
        return "workflow_step_generic"
    if "最该重排的是哪一步" in title:
        return "content_reorder_generic"
    if "不是prompt" in title:
        return "ai_video_not_prompt_generic"
    return "specific"


FORBIDDEN_VISIBLE_TERMS = [
    "自查表", "少做一小时", "这类更新", "可执行动作", "业务动作", "业务验收清单",
    "别只看发布信息", "先看任务怎么验收", "该先判断", "最该重排",
    "适合拆成一次真实任务边界测试", "适合拆成一次AI视频交付测试",
    "不该只看工具名", "只有在能说清具体产品层",
]


def forbidden_title_hits(text: str) -> list[str]:
    return [term for term in FORBIDDEN_VISIBLE_TERMS if term and term in (text or "")]


def visible_title_is_usable(title: str, item: ContentItem) -> bool:
    if not title or forbidden_title_hits(title):
        return False
    anchor = extract_event_anchor(item)
    source = specific_event_title(item)
    if anchor and anchor[:4] not in title and source[:4] not in title:
        return False
    return True


def specific_event_title(item: ContentItem) -> str:
    return title_token(item.title, 24)


def extract_event_anchor(item: ContentItem) -> str:
    title_text = " ".join([item.title, item.cover_text])
    title_lower = title_text.lower()
    text = item_text(item)
    lower = text.lower()
    title_rules = [
        ("Sensor Tower / ChatGPT月活", lambda s: "sensor tower" in s or ("chatgpt" in s and ("月活" in s or "10亿" in s or "10 亿" in s))),
        ("Anthropic AI恶意账户分析", lambda s: "anthropic" in s and ("恶意账户" in s or "malicious" in s or "abuse" in s)),
        ("OpenClaw 2026.6.1", lambda s: "openclaw" in s),
        ("Cloudflare AI Gateway", lambda s: "cloudflare ai gateway" in s),
        ("Cloudflare Radar", lambda s: "cloudflare radar" in s),
        ("Ideogram v4.0", lambda s: "ideogram" in s),
        ("Miso One", lambda s: "miso" in s),
        ("Grok Imagine", lambda s: "grok imagine" in s),
        ("NVIDIA PPISP", lambda s: "ppisp" in s or "photometric" in s),
        ("MiniMax M3", lambda s: "minimax" in s or "mini max" in s),
        ("Meet OpenJarvis", lambda s: "openjarvis" in s),
        ("Claude Code", lambda s: "claude code" in s),
        ("Google Colab CLI", lambda s: "google colab" in s and "cli" in s),
        ("Suno Voices", lambda s: "suno" in s and "voices" in s),
        ("Arena Agent排行榜", lambda s: "arena" in s and ("agent" in s or "智能体" in s)),
        ("Gemini Live 图像编辑", lambda s: "gemini live" in s and ("图像" in s or "image" in s)),
        ("MiniCPM财务分析工具", lambda s: "minicpm" in s and ("财务" in s or "accounting" in s)),
        ("Claude 自助数据分析", lambda s: "claude" in s and ("自助数据分析" in s or "数据分析" in s)),
        ("Karpathy llm-wiki", lambda s: "karpathy" in s or "llm-wiki" in s),
    ]
    for label, predicate in title_rules:
        if predicate(title_lower):
            return label
    body_rules = [
        ("Sensor Tower / ChatGPT月活", lambda s: "sensor tower" in s or ("chatgpt" in s and ("月活" in s or "10亿" in s or "10 亿" in s))),
        ("Anthropic AI恶意账户分析", lambda s: "anthropic" in s and ("恶意账户" in s or "malicious" in s or "abuse" in s)),
        ("OpenClaw 2026.6.1", lambda s: "openclaw" in s),
        ("Cloudflare AI Gateway", lambda s: "cloudflare ai gateway" in s),
        ("Cloudflare Radar", lambda s: "cloudflare radar" in s),
        ("Ideogram v4.0", lambda s: "ideogram" in s),
        ("Miso One", lambda s: "miso" in s),
        ("Grok Imagine", lambda s: "grok imagine" in s),
        ("NVIDIA PPISP", lambda s: "ppisp" in s or "photometric" in s),
        ("Meet OpenJarvis", lambda s: "openjarvis" in s),
        ("Claude Code", lambda s: "claude code" in s),
        ("Google Colab CLI", lambda s: "google colab" in s and "cli" in s),
        ("Suno Voices", lambda s: "suno" in s and "voices" in s),
        ("Arena Agent排行榜", lambda s: "arena" in s and ("agent" in s or "智能体" in s)),
        ("Gemini Live 图像编辑", lambda s: "gemini live" in s and ("图像" in s or "image" in s)),
        ("MiniCPM财务分析工具", lambda s: "minicpm" in s and ("财务" in s or "accounting" in s)),
        ("Claude 自助数据分析", lambda s: "claude" in s and ("自助数据分析" in s or "数据分析" in s)),
        ("MiniMax M3", lambda s: "minimax" in s or "mini max" in s),
        ("Karpathy llm-wiki", lambda s: "karpathy" in s or "llm-wiki" in s),
    ]
    for label, predicate in body_rules:
        if predicate(lower):
            return label
    return specific_event_title(item)


def infer_business_change(item: ContentItem, scene: str) -> str:
    text = item_text(item)
    lower = text.lower()
    if "cloudflare radar" in lower or "机器人流量" in text:
        return "AI流量真伪判断"
    if "cloudflare ai gateway" in lower:
        return "模型路由、成本和权限验收"
    if "ideogram" in lower:
        return "品牌图文一致性验收"
    if "miso" in lower or "语音模型" in text:
        return "AI视频口播生产和配音修改"
    if "grok imagine" in lower:
        return "AI视频镜头验证"
    if "ppisp" in lower or "3D重建" in text:
        return "镜头一致性和3D素材验收"
    if "openclaw" in lower:
        return "开源语音助手的任务边界和本地部署判断"
    if "anthropic" in lower and "恶意账户" in text:
        return "AI工具滥用风险和内容安全复核"
    if "minimax" in lower or "长上下文" in text:
        return "长资料处理到任务验收"
    if "openjarvis" in lower:
        return "本地Agent任务边界和验收"
    if "claude" in lower and "数据分析" in text:
        return "非技术团队指标口径和数据分析验收"
    if "sensor tower" in lower or "月活" in text:
        return "AI入口成为默认工作界面"
    return scene


def compose_topic_title(event_anchor: str, business_change: str, audience: str, constraint: str = "") -> str:
    if constraint == "question":
        return f"{event_anchor}：{audience}现在最该问清的不是热度，而是{business_change}"
    if constraint == "asset":
        return f"{event_anchor}提醒{audience}，要把{business_change}沉淀成一张检查表"
    return f"{event_anchor}正在改的不是工具名，而是{audience}的{business_change}"


def is_agent_task_content(text: str) -> bool:
    lower = text.lower()
    head = lower[:220]
    disallow_tokens = [
        "融资", "人物访谈", "访谈", "观点", "黑客马拉松", "论文", "研究动态",
        "普通模型发布", "月活", "组织管理", "partner network", "openclaw",
        "黄仁勋", "纳德拉",
    ]
    if any(token in text for token in disallow_tokens):
        return False
    strong_tokens = [
        "claude code", "codex", "mcp", "llamaindex", "openrouter", "openjarvis",
        "guardrails", "tool calling", "function calling", "工具调用", "自动化任务",
        "工作流执行", "企业流程自动化", "任务边界",
    ]
    if any(token in lower or token in text for token in strong_tokens):
        return True
    if "agent" in head or "智能体" in head:
        task_terms = ["任务", "流程", "自动化", "执行", "验收", "工具", "应用", "桌面应用", "business", "seo"]
        return any(term in lower or term in text for term in task_terms)
    return False


def hotspot_angle(item: ContentItem, scene: str) -> dict[str, str]:
    text = item_text(item)
    title = short_title(item.title)
    event = extract_event_anchor(item)
    lower = text.lower()
    if "minicpm" in lower and any(k in text for k in ["财务", "AccountingLLM", "会计", "分析工具"]):
        return {
            "角度类型": "Agent落地",
            "我的蹭热点角度": "MiniCPM财务分析工具更适合拆成企业数据任务怎么交接：输入资料、口径确认、结果复核和异常处理，而不是硬套品牌风控。",
            "影响对象": "企业财务分析、非技术数据任务、资料整理、口径复核和结果交接。",
            "标题": "MiniCPM财务分析工具这条，适合看企业数据任务怎么交接",
            "标题规则": "minicpm_accounting_agent_specific",
        }
    if "arena" in lower and ("agent" in lower or "智能体" in text or "排行榜" in text):
        return {
            "角度类型": "Agent落地",
            "我的蹭热点角度": "Arena Agent排行榜值得看的是评测场景是否接近真实工作任务：谁定义任务、谁检查结果、失败样例有没有暴露。",
            "影响对象": "Agent产品、企业自动化任务、非技术团队验收和工具选择。",
            "标题": "Arena Agent排行榜值得看的是它怎么定义真实任务",
            "标题规则": "arena_agent_benchmark_specific",
        }
    if "google colab" in lower and "cli" in lower:
        return {
            "角度类型": "非技术人机会",
            "我的蹭热点角度": "Google Colab CLI不该被锚定成Claude Code，它更像把Notebook里的实验、脚本和复现步骤带进命令行工作方式。",
            "影响对象": "数据分析、Notebook实验、脚本复现、轻量开发和非工程团队协作。",
            "标题": "Google Colab CLI发布后，Notebook实验会更像一条可复现任务",
            "标题规则": "google_colab_cli_specific",
        }
    if "suno" in lower and "voices" in lower:
        return {
            "角度类型": "AI导演流程",
            "我的蹭热点角度": "Suno Voices的重点不是泛化成AI视频模型，而是人声、口播和声音资产能不能进入内容制作的角色设定与修改流程。",
            "影响对象": "口播内容、声音资产、短视频配音、角色设定和修改交付。",
            "标题": "Suno Voices更适合拆人声资产，而不是泛讲AI视频模型",
            "标题规则": "suno_voices_specific",
        }
    if "gemini live" in lower and any(k in text for k in ["图像", "图片", "image", "编辑"]):
        return {
            "角度类型": "内容团队变化",
            "我的蹭热点角度": "Gemini Live实时编辑图像更适合拆内容团队现场改图：用户说需求、AI出修改、人确认品牌和事实风险。",
            "影响对象": "图文内容、现场改图、品牌视觉、素材修改和内容审核。",
            "标题": "Gemini Live实时改图后，内容团队要重新分工的是现场修改",
            "标题规则": "gemini_live_image_edit_specific",
        }
    if any(k in text for k in ["公众号首图", "小红书", "教程步骤卡", "视觉物料", "图文卡", "视觉模板", "宣传图"]):
        if "luma" in lower:
            hot_title = "Luma自动生成宣传图后，内容团队还要保留哪三个人工判断"
        elif "公众号首图" in text or "小红书" in text or "图文卡" in text:
            hot_title = "公众号首图能自动生成后，运营最该补的是品牌审核清单"
        else:
            hot_title = "宣传图自动生成后，内容团队还需要保留哪三个人工判断"
        return {
            "角度类型": "内容团队变化",
            "我的蹭热点角度": "视觉物料自动生成后，内容团队真正要保留的是选题判断、审美把关、品牌一致性和复盘标准。",
            "影响对象": "内容团队、品牌运营、图文生产流程、设计协作和素材复用。",
            "标题": hot_title,
            "标题规则": "visual_content_specific",
        }
    if any(k.lower() in lower for k in ["shein", "ai假人", "虚假广告", "带货", "品牌", "营销", "信任", "合规", "审核", "骗子"]):
        return {
            "角度类型": "品牌风控/信任危机",
            "我的蹭热点角度": "这类热点不该只讲AI翻车，而要讲品牌内容团队如何补素材审核、AI生成标识、虚假人设识别和投放前风控。",
            "影响对象": "品牌内容团队、投放团队、素材审核流程、AI营销合规与消费者信任。",
            "标题": "AI假人带货翻车后，品牌内容团队最该补的是素材审核流程",
            "标题规则": "brand_risk_specific",
        }
    if "anthropic" in lower and any(k in text for k in ["恶意账户", "攻击者", "滥用", "abuse", "malicious"]):
        return {
            "角度类型": "品牌风控/信任危机",
            "我的蹭热点角度": "Anthropic披露AI恶意账户时，不该只当安全新闻，而要看内容团队、AI工具团队和服务商如何补滥用识别、异常行为复核和客户风险提示。",
            "影响对象": "AI工具服务商、内容团队、品牌风控、客户交付和异常使用复核。",
            "标题": "Anthropic恶意账户分析后，AI工具团队最该补的是滥用风险复核",
            "标题规则": "ai_abuse_risk_review",
        }
    if any(k in lower for k in ["openjarvis", "local-first"]) or "设备端个人AI智能体" in text:
        return {
            "角度类型": "Agent落地",
            "我的蹭热点角度": "本地优先Agent的重点不是框架名，而是任务边界、工具调用、记忆和学习怎么被业务人验收。",
            "影响对象": "个人Agent、设备端自动化、非技术任务流、隐私和验收标准。",
            "标题": compose_topic_title(event, infer_business_change(item, scene), "非技术团队", "asset"),
            "标题规则": "local_first_agent_validation",
        }
    if any(k in lower for k in ["ppisp", "photometric"]) or any(k in text for k in ["3D重建", "光度变化"]):
        return {
            "角度类型": "AI导演流程",
            "我的蹭热点角度": "NVIDIA PPISP不该硬讲成论文指标，而要看它能否改善素材一致性、镜头衔接和成片确认。",
            "影响对象": "AI视频团队、3D素材工作流、镜头一致性、成片验收和视觉资产复用。",
            "标题": "NVIDIA PPISP把3D重建问题拉回成片验收，AI视频团队别只看论文指标",
            "标题规则": "video_3d_reconstruction_validation",
        }
    if any(k.lower() in lower for k in ["runway", "kling", "luma", "seedance", "sora", "视频", "图像", "画面", "音效", "剪辑"]):
        if "runway" in lower:
            hot_title = "Runway API开放后，AI视频服务最先被重做的是哪一层"
        elif "luma" in lower:
            hot_title = "Luma自动宣传图这类能力，真正考验的是谁来决定能不能发"
        else:
            hot_title = ""
        return {
            "角度类型": "AI导演流程" if any(k.lower() in lower for k in ["runway", "kling", "seedance", "sora", "视频", "分镜", "镜头", "成片"]) else "内容团队变化",
            "我的蹭热点角度": "AI视频模型更新后，导演工作流里最先变的不是prompt，而是Brief、分镜、素材修改和验收标准。",
            "影响对象": "AI视频服务、品牌内容团队、短视频制作流程、分镜和成片验收。",
            "标题": hot_title,
            "标题规则": "ai_video_workflow_specific",
        }
    if any(k in lower for k in ["miso", "speech", "voice", "asr", "vapi"]) or any(k in text for k in ["语音模型", "语音克隆", "低延迟", "口播"]):
        return {
            "角度类型": "AI导演流程",
            "我的蹭热点角度": "语音模型更新会影响AI视频服务里的口播、配音、修改和验收，不该只看参数。",
            "影响对象": "AI视频服务、口播生产、配音修改、短剧工作流和内容验收。",
            "标题": "Miso One开源语音模型后，AI视频服务的口播修改会变成新交付项" if "miso" in lower else f"{event}正在把AI口播从配音工具变成视频交付环节",
            "标题规则": "speech_model_video_voiceover",
        }
    if is_agent_task_content(text):
        if "llamaindex" in lower:
            hot_title = "Agent模板真正要看的，是它能不能说清输入和失败边界"
        elif "openrouter" in lower or "补丁" in text:
            hot_title = "模型能生成补丁后，Codex类工具怎么进入非技术工作流"
        elif "claude" in lower and any(k in text for k in ["数据分析", "自助", "分析"]):
            hot_title = "Claude自助数据分析真正改变的，是业务团队怎么定义指标口径"
        else:
            hot_title = ""
        return {
            "角度类型": "Agent落地",
            "我的蹭热点角度": f"{title}要先补一个真实任务样例：输入什么、交付什么结果、失败时谁处理。",
            "影响对象": "Agent服务、企业流程自动化、非技术业务人的重复任务、工具链整合。",
            "标题": hot_title,
            "标题规则": "agent_validation_specific",
        }
    if "文档自动化" in text or "documentation" in lower or "mcg toolkit" in lower:
        return {
            "角度类型": "Agent落地",
            "我的蹭热点角度": "文档自动化不只是省写文档，而是让Agent项目的输入、输出、验收和异常记录开始可交接。",
            "影响对象": "Agent项目交付、模型文档、验收记录、非技术团队协作。",
            "标题": "AI模型文档自动化后，Agent项目最该补的是验收文档",
            "标题规则": "agent_documentation_specific",
        }
    if "推理速度" in text or "reasoning speed" in lower or "inference" in lower:
        return {
            "角度类型": "工作流重排",
            "我的蹭热点角度": "模型变快的价值不只是跑分，而是哪些内容生产、资料筛选和复盘任务可以从人工盯着做改成后台自动跑。",
            "影响对象": "内容团队重复任务、资料筛选、日报生成、轻量Agent工作流。",
            "标题": f"{event}后，内容团队哪些后台任务可以真正自动跑",
            "标题规则": "inference_speed_workflow",
        }
    if any(k in lower for k in ["cloudflare radar", "bot traffic"]) or any(k in text for k in ["机器人流量", "AI流量", "Radar"]):
        return {
            "角度类型": "内容团队变化",
            "我的蹭热点角度": "Cloudflare把AI流量做成雷达后，内容团队不能只看热度，要学会判断工具是真增长、爬虫噪音还是虚火。",
            "影响对象": "内容团队、AI工具运营、增长复盘、流量判断和选题验证。",
            "标题": f"{event}后，内容团队该怎么判断AI工具是真增长还是虚火",
            "标题规则": "traffic_radar_growth_check",
        }
    if "deepseek" in lower and any(k in text for k in ["融资", "参投", "估值", "腾讯", "宁德时代"]):
        return {
            "角度类型": "商业化机会",
            "我的蹭热点角度": "DeepSeek融资传闻不适合只讲资本热闹，更适合拆模型公司、生态工具和AI创业项目各自该站在哪一层。",
            "影响对象": "AI创业项目、模型生态、中间层工具、企业服务和内容团队的工具选择。",
            "标题": f"{event}背后，AI创业项目该重新判断自己站在哪一层",
            "标题规则": "model_company_ecosystem_positioning",
        }
    if any(k in text for k in ["斯坦福", "法学院", "法学教授"]):
        return {
            "角度类型": "工作流重排",
            "我的蹭热点角度": "AI在专家任务里超过教授，不该直接讲替代专家，而要讲非技术团队如何把专业任务拆成输入、判断、复核和验收。",
            "影响对象": "专业服务团队、非技术业务团队、知识工作流、复核机制和AI任务验收。",
            "标题": f"{event}后，非技术团队更该学的是专家任务怎么验收",
            "标题规则": "expert_task_validation",
        }
    if ("chatgpt" in lower and any(k in text for k in ["月活", "10 亿", "10亿"])) or "sensor tower" in lower:
        return {
            "角度类型": "内容团队变化",
            "我的蹭热点角度": "ChatGPT月活破10亿的重点不是用户数本身，而是AI入口已经变成内容团队、品牌团队和普通用户的默认工作界面。",
            "影响对象": "内容团队、品牌运营、用户触点、AI入口设计和工具选择。",
            "标题": "Sensor Tower说ChatGPT月活破10亿后，内容团队该把AI入口当基础设施",
            "标题规则": "chatgpt_default_ai_interface",
        }
    if any(k in text for k in ["战略合作", "阿里云", "宏利香港", "保险"]):
        return {
            "角度类型": "工作流重排",
            "我的蹭热点角度": "传统企业接入云厂商AI，不该只看签约新闻，而要看具体业务流程、数据边界、验收标准和一线团队能不能真的用起来。",
            "影响对象": "传统企业AI转型、业务流程改造、企业知识库、客服/营销/运营团队和项目验收。",
            "标题": f"{event}后，传统企业AI项目最该先验收哪条业务流程",
            "标题规则": "enterprise_ai_partnership_workflow",
        }
    if "cloudflare ai gateway" in lower or ("cloudflare" in lower and "gateway" in lower):
        return {
            "角度类型": "工作流重排",
            "我的蹭热点角度": "模型接入AI Gateway的重点不是多一个入口，而是企业和团队开始需要统一管理模型路由、成本、权限和调用验收。",
            "影响对象": "企业AI调用、Agent工具链、模型路由、成本控制和交付验收。",
            "标题": f"{event}后，企业AI调用最该补的是路由和成本验收",
            "标题规则": "ai_gateway_ops_validation",
        }
    if any(k in lower for k in ["ideogram", "midjourney", "image model"]) or any(k in text for k in ["生图", "图像模型", "2K", "JSON 提示"]):
        return {
            "角度类型": "内容团队变化",
            "我的蹭热点角度": "图像模型更新不只是画质更好，而是品牌图文生产开始卷一致性、批量修改和验收标准。",
            "影响对象": "品牌设计、内容运营、图文模板、视觉验收和素材复用。",
            "标题": f"{event}不是又一个生图更新，而是品牌图文开始卷一致性验收",
            "标题规则": "image_model_brand_consistency",
        }
    if any(k in lower for k in ["grok imagine", "imagine"]) or any(k in text for k in ["Imagine", "视频生成"]):
        return {
            "角度类型": "AI导演流程",
            "我的蹭热点角度": "视频模型发布后，最该看的不是热闹，而是哪些镜头、节奏和修改环节可以被纳入导演验收。",
            "影响对象": "AI视频团队、导演工作流、镜头验证、素材修改和成片验收。",
            "标题": "Grok Imagine 1.5预览版适合拿来做三组镜头测试，而不是直接喊替代剪辑",
            "标题规则": "video_model_shot_validation",
        }
    if "karpathy" in lower or "llm-wiki" in lower:
        return {
            "角度类型": "内容团队变化",
            "我的蹭热点角度": "llm-wiki火起来，不该只看项目星标，而要看内容团队如何把AI知识整理成选题资产、资料入口和判断标准。",
            "影响对象": "内容团队、AI知识库、选题资料池、内部学习和知识资产沉淀。",
            "标题": f"{event}火了后，内容团队该怎么搭自己的AI知识库入口",
            "标题规则": "ai_knowledge_base_content_asset",
        }
    if "openclaw" in lower:
        return {
            "角度类型": "暂存观察",
            "我的蹭热点角度": "OpenClaw更新可以关注，但当前信息更像版本发布；如果没有明确业务任务、工作流边界和可演示场景，先不要硬讲成Agent验收方法论。",
            "影响对象": "暂存：需要补充它能稳定接管的具体业务任务、输入输出和演示链路。",
            "标题": "OpenClaw 2026.6.1可以观察，先别硬套非技术Agent验收",
            "标题规则": "agent_release_observation",
        }
    if any(k in lower for k in ["minimax", "1m token", "long context"]) or any(k in text for k in ["100万", "1M token", "长上下文", "解码加速"]):
        return {
            "角度类型": "Agent落地",
            "我的蹭热点角度": "长上下文和解码加速的价值，不是炫参数，而是让资料整理、验收和多步Agent任务更可能一次跑完。",
            "影响对象": "Agent工作流、长文档处理、项目资料整理、内容复盘和非技术任务验收。",
            "标题": f"{event}后，先看长资料任务能不能交接给Agent",
            "标题规则": "long_context_agent_workflow",
        }
    if any(k in text for k in ["黄仁勋", "纳德拉", "人物观点", "人物访谈", "共议"]):
        return {
            "角度类型": "暂存观察",
            "我的蹭热点角度": "人物观点类热点可以帮助判断趋势，但如果没有落到产品能力、业务场景或项目经验，不适合直接占用今日Top10。",
            "影响对象": "暂存：需要补充具体产品变化、团队动作或业务流程影响。",
            "标题": f"{event}可以观察，先别把人物观点硬改成工作流选题",
            "标题规则": "person_viewpoint_observation",
        }
    if any(k in text for k in ["洪水", "水文", "灾害", "气候"]) and not any(k in text for k in ["内容", "营销", "Agent", "视频", "品牌"]):
        return {
            "角度类型": "暂存观察",
            "我的蹭热点角度": "这个热点有技术和公共议题价值，但暂时缺少内容团队、品牌增长或AI业务系统的直接行动角度，适合暂存观察。",
            "影响对象": "暂存：需要进一步判断它能否转成数据产品、公益技术或行业解决方案复盘。",
            "标题": f"{event}可以观察，先别硬讲成内容团队工作流",
            "标题规则": "off_position_science_observation",
        }
    if any(k.lower() in lower for k in ["模型", "model", "api", "框架", "framework", "平台", "推理", "训练", "开源", "发布", "更新"]):
        if any(k in text for k in ["训练框架", "自研训练", "JAX", "GPU"]):
            hot_title = "大厂自研训练框架变多后，中间层AI工具还能靠什么活"
            angle = "产品生死线"
        elif "gemini" in lower and any(k in text for k in ["幕后", "架构师", "探索"]):
            hot_title = "AI公司讲幕后故事时，内容团队该学的是信任感而不是术语"
            angle = "内容团队变化"
        elif "banana" in lower or "图像" in text or "多模态" in text:
            hot_title = "图像模型继续升级后，品牌图最难的是保持一致"
            angle = "内容团队变化"
        else:
            hot_title = ""
            angle = "产品生死线"
        return {
            "角度类型": angle,
            "我的蹭热点角度": f"{event}目前更像资讯观察项；如果后续能补到明确产品层、用户任务或项目影响，再考虑转成选题。",
            "影响对象": "AI工具产品、包装型SaaS、插件生态、内容/运营团队的工具选择。",
            "标题": hot_title,
            "标题规则": "generic_model_update_observe",
        }
    if any(k in text for k in ["公众号", "小红书", "图文", "卡片", "文章", "内容", "素材", "设计"]):
        return {
            "角度类型": "内容团队变化",
            "我的蹭热点角度": f"{title} 影响的是内容团队从选题、图文素材到复盘的哪一步被合并或重排。",
            "影响对象": "内容团队、品牌运营、图文生产、素材复用和投放复盘。",
            "标题": f"{title}要先回到具体内容场景再决定做不做",
            "标题规则": "content_team_reorder",
        }
    if scene == "汽车与内容营销流程":
        return {
            "角度类型": "商业化机会",
            "我的蹭热点角度": f"这个热点要看它如何改变汽车与品牌内容团队的素材生产、审核、卖点表达和信任建立。",
            "影响对象": "汽车内容团队、品牌营销、素材审核、投放前风控和产品卖点表达。",
            "标题": f"{title}背后，品牌内容团队该重做哪条审核线",
            "标题规则": "brand_content_audit",
        }
    return {
        "角度类型": "暂存观察",
        "我的蹭热点角度": "目前只能看出资讯价值，业务影响和行动建议还不够明确，适合暂存观察。",
        "影响对象": "待补：需要进一步判断影响哪类产品、流程、团队或商业机会。",
        "标题": f"{title} 可以蹭，但要先找到业务影响而不是复述资讯",
        "标题规则": "needs_observation",
    }


def regular_topic_title(item: ContentItem, scene: str) -> str:
    core = short_title(item.title)
    if item.source_type == "AIHOT热点":
        return hotspot_angle(item, scene).get("标题") or specific_event_title(item)
    text = item_text(item)
    lower = text.lower()
    if item.source_type == "公众号文章":
        if "claude code" in lower and any(k in text for k in ["原则", "团队", "工作方式", "harness", "验收"]):
            return "Claude Code团队的5条原则，最值得学的是项目交付习惯"
        if any(k in text for k in ["方法论", "内容创作", "内部分享", "团队"]):
            return f"{core}，内容团队最该学的是判断流程而不是观点金句"
        return f"{core}，怎么拆成我的业务现场选题和Brief"
    if scene == "AI导演工作流与视频交付":
        if item.fetch_method == "douyin_public_router_data":
            return f"{core}，先别急着复刻成片，要拆它的标题、工具和交付承诺"
        return f"{core}，AI视频真正该拆的是Brief、分镜和验收"
    if scene == "非技术Agent处理重复业务任务":
        return f"{core}，先看它能不能说清任务边界"
    if scene == "汽车与内容营销流程":
        return f"{core}，品牌团队该补哪条素材审核线"
    if scene == "项目复盘与能力产品化":
        return f"{core}，怎么把项目过程沉淀成模板和服务入口"
    return f"{core}，先看它和我的内容现场有没有关系"


INTERNAL_TITLE_TERMS = ["内容团队", "业务团队", "非技术团队", "验收", "流程", "工作流", "基础设施"]


def infer_content_type(topic: dict[str, Any], item: ContentItem) -> str:
    text = " ".join([
        topic.get("来源内容", ""),
        topic.get("业务场景", ""),
        topic.get("热点切入方式", ""),
        topic.get("可沉淀资产", ""),
        topic.get("我的蹭热点角度", ""),
    ])
    if topic.get("推荐动作") == "暂存观察":
        return "暂存观察"
    if item.source_type != "AIHOT热点":
        return "对标学习"
    if any(k in text for k in ["风险", "审核", "风控", "合规", "翻车", "虚假", "恶意"]):
        return "踩坑提醒"
    if topic.get("热点切入方式") in {"AI导演流程", "Agent落地", "工作流重排"}:
        return "工作流拆解"
    if topic.get("推荐动作") == "进入Brief" or any(k in text for k in ["清单", "SOP", "模板", "流程图", "资料包"]):
        return "资产沉淀"
    return "热点短评"


def title_style(content_type: str, topic: dict[str, Any]) -> str:
    if content_type == "踩坑提醒":
        return "风险提醒型"
    if content_type == "对标学习":
        return "案例拆解型"
    if content_type == "资产沉淀":
        return "清单资产型"
    if content_type == "工作流拆解":
        return "流程拆解型"
    if topic.get("推荐动作") == "立即蹭热点":
        return "热点短评型"
    return "观点判断型"


def platform_suggestion(content_type: str, topic: dict[str, Any]) -> str:
    if topic.get("推荐动作") == "立即蹭热点":
        return "小红书短帖 / 抖音短评 / 视频号短评"
    if topic.get("推荐动作") == "进入Brief":
        return "公众号 / 小红书图文 / 抖音"
    if content_type == "对标学习":
        return "小红书图文 / 公众号短文"
    if content_type == "工作流拆解":
        return "抖音 / 视频号 / 小红书图文"
    if content_type == "踩坑提醒":
        return "小红书短帖 / 公众号短评"
    if content_type == "暂存观察":
        return "内部观察 / 暂不发布"
    return "小红书 / 抖音 / 视频号"


def publishable_title_from_topic(topic: dict[str, Any], item: ContentItem, content_type: str) -> str:
    event = topic.get("事件锚点") or extract_event_anchor(item)
    event_short = title_token(event, 28)
    core = short_title(item.title)
    lower = " ".join([item.title, item.body_snippet, topic.get("我的蹭热点角度", "")]).lower()
    text = " ".join([item.title, item.body_snippet, topic.get("业务场景", ""), topic.get("可沉淀资产", "")])

    if item.source_type == "公众号文章":
        if "卡兹克" in item.account_name or "数字生命卡兹克" in item.account_name:
            source_title = item.title or ""
            if any(k in source_title for k in ["内部分享", "内容创作方法论", "内容创作", "三年来总结"]):
                return "卡兹克这场内部分享，值得学的是他怎么筛选选题"
            if "Claude Code" in source_title or (
                "Claude Code" in text and any(k in source_title for k in ["原则", "团队", "工作方式"])
            ):
                return "卡兹克拆 Claude Code 这篇，最值得抄的是它的项目复盘方式"
            return "卡兹克这篇内部分享，真正值得学的是他怎么筛选AI信息"
        return f"{core}最值得拆的，是它怎么让读者相信作者懂行"
    if item.source_type == "对标视频":
        if item.fetch_method == "douyin_public_router_data":
            if any(k in text for k in ["小云雀", "短剧Agent", "AI短剧"]):
                return "小云雀短剧Agent这条视频，最值得看的是它怎么承诺成片效果"
            if any(k in text for k in ["一镜到底", "爆款视频", "视频密码"]):
                return "这条AI爆款视频教程，先看它怎么把工具包装成交付承诺"
            return f"{core}最值得看的，不是工具名，而是它怎么承诺结果"
        return f"{core}这条视频，真正能学的是钩子、结构和转化入口"

    if "cloudflare radar" in lower or ("cloudflare" in lower and "radar" in lower):
        return "AI工具到底是真火还是虚火？Cloudflare这组流量数据给了一个判断方法"
    if "cloudflare ai gateway" in lower or ("cloudflare" in lower and "gateway" in lower):
        return "Cloudflare AI Gateway接入更多模型后，最先失控的可能是成本和权限"
    if "ideogram" in lower or "生图" in text or "图像模型" in text:
        return "生图工具继续升级后，品牌图最难的反而是保持一致"
    if "grok imagine" in lower or "imagine" in lower:
        return "Grok Imagine 1.5我最想测的不是画质，而是这三类镜头能不能稳定"
    if "claude" in lower and ("数据分析" in text or "data" in lower):
        return "Claude能自己做数据分析后，最危险的不是不会用AI，而是指标口径没人管"
    if "miso" in lower or ("grok" in lower and any(k in text for k in ["语音", "低延迟", "口播"])):
        return "AI口播开始卷低延迟后，视频服务最该多卖一个“修改交付”"
    if any(k in text for k in ["AI假人", "Shein", "虚假广告", "带货"]):
        return "AI假人带货翻车后，品牌内容团队最该补的是素材审核流程"
    if "llm-wiki" in lower or "karpathy" in lower:
        return "Karpathy把AI知识做成llm-wiki后，内容团队也该有自己的资料入口"
    if "minimax" in lower or "长上下文" in text or "100万" in text:
        return "长上下文继续升级后，AI助理最该先解决资料整理这件事"
    if "nemotron" in lower and any(k in text for k in ["种子", "问答", "合成"]):
        return "Nemotron开始合成任务数据后，普通团队也该重看自己的训练材料"
    if "nemotron" in lower and ("ultra" in lower or "nvidia" in lower):
        return "NVIDIA继续推Nemotron后，企业AI最该看的不是模型名，是谁来评测结果"
    if "openshell" in lower:
        return "OpenShell继续更新后，中间层AI工具不能只靠包装界面了"
    if "openjarvis" in lower:
        return "OpenJarvis可以先观察：它离真正接管本地任务还差哪一步"
    if any(k in text for k in ["智能体工程", "Agent工程", "实战窍门"]):
        return "Agent工程经验开始成体系后，最值钱的是那张踩坑清单"

    if content_type == "踩坑提醒" and any(k in text for k in ["风险", "恶意", "虚假", "翻车", "滥用"]):
        return f"{event_short}这条，适合讲AI内容上线前的风险复核"
    return ""


def title_alternatives(topic: dict[str, Any], item: ContentItem, publishable: str) -> str:
    event = topic.get("事件锚点") or extract_event_anchor(item)
    content_type = topic.get("内容类型", "")
    alternatives = [publishable]
    if content_type == "对标学习":
        alternatives.append(f"{short_title(item.title)}这条内容，真正值得学的是专业感怎么建立")
        alternatives.append("同样讲AI，为什么这类内容更容易让人相信你做过项目")
    elif content_type == "踩坑提醒":
        alternatives.append(f"{event}不是八卦，是AI内容上线前的风险提醒")
        alternatives.append("AI素材别急着投放，先过一遍这几个风险点")
    elif content_type == "工作流拆解":
        alternatives.append(f"{event}这条，先拿一个真实任务测清楚")
        alternatives.append(f"{event}能不能做内容，关键看它有没有具体使用场景")
    elif content_type == "资产沉淀":
        alternatives.append(f"{event}要不要做，先看它能沉淀哪类账号资产")
        alternatives.append(f"{event}如果只剩新闻价值，就先不要急着发")
    elif content_type == "暂存观察":
        alternatives.append(f"{event}先别急着发，等一个更清楚的落地案例")
        alternatives.append(f"{event}热度够了，但现在还差一个好角度")
    else:
        alternatives.append(f"{event}之后，哪类人的日常工作会先变轻")
        alternatives.append(f"{event}这件事，我会从一个具体场景讲起")
    return "\n".join(dict.fromkeys(alternatives[:3]))


def is_over_internalized_title(title: str) -> str:
    hits = sum(1 for term in INTERNAL_TITLE_TERMS if term in title)
    if hits >= 3:
        return "是"
    if any(phrase in title for phrase in ["该先判断", "先看输入输出", "怎么转成", "最该补的是"]):
        return "是"
    return "否"


def publish_rewrite_reason(topic: dict[str, Any], item: ContentItem) -> str:
    internal = topic.get("内部切入角度") or topic.get("我的选题标题", "")
    publishable = topic.get("可发布标题", "")
    if internal == publishable:
        return "未改写：内部角度已经接近可发布表达。"
    event = topic.get("事件锚点") or extract_event_anchor(item)
    return f"把内部判断句改成面向用户的标题；保留事件锚点：{event}。"


def ensure_publish_metadata(topic: dict[str, Any], item: ContentItem) -> dict[str, Any]:
    content_type = infer_content_type(topic, item)
    publishable = publishable_title_from_topic(topic, item, content_type)
    topic["内部切入角度"] = topic.get("内部切入角度") or topic.get("我的选题标题", "")
    topic["内容类型"] = content_type
    topic["平台建议"] = platform_suggestion(content_type, topic)
    topic["标题风格"] = title_style(content_type, topic)
    topic["可发布标题"] = publishable
    topic["标题备选"] = title_alternatives(topic, item, publishable)
    topic["标题是否过度内部化"] = is_over_internalized_title(topic["内部切入角度"])
    topic["标题改写原因"] = publish_rewrite_reason(topic, item)
    return topic


ABSTRACT_TITLE_TERMS = ["流程", "验收", "判断", "业务动作", "工作流", "基础设施", "这类更新", "可执行动作", "业务验收清单"]
GENERIC_SUBJECT_TERMS = ["内容团队", "业务团队", "非技术团队"]
STRATEGY_TITLE_PHRASES = ["怎么蹭", "应该讲成", "可执行动作", "别只看发布信息", "别只看发布", "普通人蹭", "不只是新闻", "先放观察", "上新后"]
PERSONA_TERMS = [
    "内容", "营销", "品牌", "增长", "AI视频", "视频", "导演", "分镜", "脚本", "口播",
    "Brief", "飞书", "账号", "复盘", "Agent", "智能体", "工作流", "项目", "交付",
    "素材", "转化", "服务", "工具链", "自动化",
]
NEWS_ONLY_TERMS = ["发布", "更新", "开源", "上线", "融资", "论文", "排行榜", "版本", "指南"]


def content_credibility(item: ContentItem) -> str:
    if item.source_type == "公众号文章":
        return "全文" if is_full_text_item(item) == "是" else "摘要"
    if item.source_type == "对标视频" and item.fetch_method == "douyin_public_router_data":
        return "抖音浅层"
    if item.source_type == "AIHOT热点":
        return "AIHOT摘要"
    return "摘要"


def real_user_question(topic: dict[str, Any], item: ContentItem) -> str:
    text = " ".join([topic.get("可发布标题", ""), topic.get("业务场景", ""), item.title, item.body_snippet])
    if any(k in text for k in ["Claude Code", "Agent", "智能体", "OpenJarvis", "Nemotron"]):
        return "这个 AI 能不能真的帮我完成任务，结果谁来检查？"
    if any(k in text for k in ["Grok", "视频", "口播", "Imagine", "短剧", "小云雀"]):
        return "这个视频能力能不能变成可交付的内容效果，而不是只看演示？"
    if any(k in text for k in ["品牌", "素材", "AI假人", "Shein", "审核"]):
        return "AI生成内容上线前，怎样避免翻车和信任风险？"
    if item.source_type == "公众号文章":
        return "这篇长文真正值得学的判断方法是什么？"
    return "这个热点对我今天的内容选题有什么真实帮助？"


def why_today(topic: dict[str, Any], item: ContentItem) -> str:
    if item.source_type == "AIHOT热点":
        return "今天仍在热点窗口，适合借势讲一个具体影响，而不是复述新闻。"
    if item.reused_url == "是":
        return "这是已投喂内容复用测试，适合和当天热点一起比较选题质量。"
    return "来源内容已进入本轮候选，适合判断是否值得拆成自己的表达。"


def title_lint(title: str, item: ContentItem) -> tuple[int, str, list[str]]:
    penalties = 0
    reasons: list[str] = []
    if not title.strip():
        return 0, "高", ["没有生成可发布标题"]
    forbidden_hits = forbidden_title_hits(title)
    if forbidden_hits:
        penalties += min(70, 28 * len(forbidden_hits))
        reasons.append(f"命中禁用模板词：{','.join(forbidden_hits)}")
    abstract_hits = [term for term in ABSTRACT_TITLE_TERMS if term in title]
    subject_hits = [term for term in GENERIC_SUBJECT_TERMS if term in title]
    if abstract_hits:
        penalties += min(35, 10 * len(abstract_hits))
        reasons.append(f"抽象词过多：{','.join(abstract_hits)}")
    if subject_hits:
        penalties += min(20, 8 * len(subject_hits))
        reasons.append(f"泛化主体：{','.join(subject_hits)}")
    anchor = extract_event_anchor(item)
    if anchor and anchor[:4] not in title and specific_event_title(item)[:4] not in title:
        penalties += 18
        reasons.append("缺少具体热点名或来源锚点")
    if any(k in title for k in STRATEGY_TITLE_PHRASES):
        penalties += 25
        reasons.append("像策略说明，不像标题")
    if item.source_type == "对标视频" and item.fetch_method == "douyin_public_router_data" and any(k in title for k in ["口播", "镜头结构", "评论区", "完整"]):
        penalties += 25
        reasons.append("超过抖音浅层解析可支撑范围")
    score = max(0, 100 - penalties)
    if score >= 78:
        risk = "低"
    elif score >= 58:
        risk = "中"
    else:
        risk = "高"
    return score, risk, reasons


def persona_match_score(topic: dict[str, Any], item: ContentItem) -> tuple[int, list[str]]:
    text = " ".join([
        item.title, item.body_snippet, item.cover_text,
        topic.get("我的蹭热点角度", ""), topic.get("业务场景", ""),
        topic.get("可沉淀资产", ""),
    ])
    hits = list(dict.fromkeys([term for term in PERSONA_TERMS if term.lower() in text.lower() or term in text]))
    score = min(100, 30 + len(hits) * 10)
    if item.source_type == "公众号文章" and is_full_text_item(item) == "是":
        score += 10
    if item.source_type == "对标视频" and item.fetch_method == "douyin_public_router_data":
        score -= 12
    score = max(0, min(100, score))
    return score, hits


def is_news_only(topic: dict[str, Any], item: ContentItem, persona_score: int) -> str:
    text = " ".join([item.title, item.body_snippet, topic.get("我的蹭热点角度", "")])
    news_hits = sum(1 for term in NEWS_ONLY_TERMS if term in text)
    has_project_angle = persona_score >= 60
    return "是" if news_hits >= 2 and not has_project_angle else "否"


def support_level(topic: dict[str, Any], item: ContentItem) -> str:
    if item.source_type == "公众号文章":
        return "足够" if is_full_text_item(item) == "是" else "不足"
    if item.source_type == "对标视频" and item.fetch_method == "douyin_public_router_data":
        return "浅层"
    if item.source_type == "AIHOT热点":
        return "摘要可用"
    return "摘要可用" if item.body_snippet or item.cover_text else "不足"


def editor_visible_note(topic: dict[str, Any], item: ContentItem, suggested: str, not_recommend: list[str], persona_score: int, support: str) -> str:
    if suggested == "是":
        if item.source_type == "公众号文章" and is_full_text_item(item) == "是":
            return "这条和账号人设强相关，且有全文支撑，可以拆成一篇对标学习。"
        if item.source_type == "AIHOT热点":
            return "这条有热点窗口，也能接到我的AI业务系统视角，适合今天推进。"
        return "这条能体现我的内容现场和项目判断，适合进入制作。"
    if item.source_type == "对标视频" and item.fetch_method == "douyin_public_router_data":
        return "抖音浅层解析只有标题和文案，适合观察选题包装，不适合直接做深拆。"
    if item.source_type == "公众号文章" and is_full_text_item(item) != "是":
        return "只有摘要，能看出方向，但缺全文细节，先暂存。"
    if support in {"不足", "浅层"}:
        return "信息支撑还不够，先暂存，等补到案例、全文或口播再判断。"
    if persona_score < 60:
        return "和当前账号人设关联还不够强，先暂存。"
    if not_recommend:
        return not_recommend[0]
    return "能看出方向，但今天还缺一个足够具体的切入点，先暂存。"


def editorial_judgement(topic: dict[str, Any], item: ContentItem) -> dict[str, Any]:
    credibility = content_credibility(item)
    title = topic.get("可发布标题", "")
    combined_visible = "\n".join([title, topic.get("标题备选", ""), topic.get("我的蹭热点角度", ""), topic.get("推荐理由", "")])
    template_hits = list(dict.fromkeys(forbidden_title_hits(combined_visible)))
    title_score, ai_risk, lint_reasons = title_lint(title, item)
    persona_score, persona_hits = persona_match_score(topic, item)
    support = support_level(topic, item)
    news_only = is_news_only(topic, item, persona_score)
    base = int(topic.get("推荐分", 0) or 0)
    credibility_bonus = {"全文": 12, "AIHOT摘要": 5, "摘要": 0, "抖音浅层": -10}.get(credibility, 0)
    action_bonus = 5 if topic.get("推荐动作") in {"立即蹭热点", "进入Brief", "本周做"} else -5
    editor_score = max(0, min(100, round(base * 0.45 + title_score * 0.2 + persona_score * 0.25 + credibility_bonus + action_bonus)))
    not_recommend: list[str] = []
    if template_hits:
        not_recommend.append(f"用户可见字段命中模板词：{','.join(template_hits)}")
        ai_risk = "高" if len(template_hits) >= 2 else ("高" if ai_risk == "中" else "中")
        title_score = min(title_score, 55 if len(template_hits) == 1 else 35)
    if not title.strip():
        not_recommend.append("没有足够自然、具体、有人味的可发布标题")
    if persona_score < 60:
        not_recommend.append("和AI内容系统/营销/导演/项目现场的人设关联弱")
    if news_only == "是":
        not_recommend.append("目前更像资讯搬运，缺少我的项目现场角度")
    if support == "不足":
        not_recommend.append("解析文本支撑不足")
    if item.source_type == "公众号文章" and is_full_text_item(item) != "是":
        not_recommend.append("公众号不是全文解析，不能当作深度拆解推进")
    if credibility == "抖音浅层":
        not_recommend.append("只有标题/文案/封面等浅层信息，缺口播和评论，不适合直接下深结论")
    if title_score < 65:
        not_recommend.append("标题 AI 味或抽象词偏重")
    if editor_score < 68:
        not_recommend.append("编辑判断分不足，先观察")
    if ai_risk == "高":
        not_recommend.append("AI味风险高")
    if topic.get("推荐动作") == "暂存观察":
        not_recommend.append("当前推荐动作已是暂存观察，先不生成可发布标题")

    editor_score = max(0, min(100, round(base * 0.45 + title_score * 0.2 + persona_score * 0.25 + credibility_bonus + action_bonus)))
    suggested = "是" if editor_score >= 78 and title_score >= 72 and persona_score >= 60 and ai_risk != "高" and news_only == "否" else ("暂存观察" if editor_score >= 55 else "否")
    if topic.get("推荐动作") == "暂存观察":
        suggested = "暂存观察"
    if item.source_type == "公众号文章" and is_full_text_item(item) != "是":
        suggested = "暂存观察"
    if suggested != "是" and topic.get("推荐动作") in {"立即蹭热点", "进入Brief", "本周做"}:
        topic["推荐动作"] = "暂存观察"
    topic["真实用户问题"] = real_user_question(topic, item)
    topic["为什么今天值得做"] = why_today(topic, item)
    topic["我能讲出的独特角度"] = topic.get("我的蹭热点角度", "")
    topic["我的账号为什么能讲"] = "、".join(persona_hits[:8]) or "暂时缺少明显人设锚点"
    topic["是否只是资讯搬运"] = news_only
    topic["是否有足够内容支撑"] = support
    topic["不建议做的原因"] = "；".join(not_recommend) or "暂无明显不做理由。"
    topic["内容可信度"] = credibility
    topic["人设匹配分"] = str(persona_score)
    topic["编辑判断分"] = str(editor_score)
    topic["标题质量分"] = str(title_score)
    topic["AI味风险"] = ai_risk
    topic["是否建议进入制作"] = suggested
    topic["主编判断"] = "值得今天推进" if suggested == "是" else ("先暂存，等更具体角度或材料" if suggested == "暂存观察" else "不建议制作")
    topic["模板词命中情况"] = "、".join(template_hits) or "无"
    topic["推荐理由"] = editor_visible_note(topic, item, suggested, not_recommend, persona_score, support)
    topic["推荐动作原因"] = "；".join(lint_reasons + not_recommend) or "标题具体、信息密度足够，适合进入今日判断。"
    topic["降级原因"] = "；".join(not_recommend) if suggested != "是" else ""
    if suggested != "是":
        topic["可发布标题"] = ""
        topic["标题备选"] = ""
    return topic


def apply_editorial_judgement(topics: list[dict[str, Any]], item_by_fp: dict[str, ContentItem]) -> list[dict[str, Any]]:
    for topic in topics:
        item = item_by_fp.get(topic.get("内容指纹", ""))
        if item:
            ensure_publish_metadata(topic, item)
            editorial_judgement(topic, item)
    return topics


def assign_today_priority(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recommended = sorted(
        [topic for topic in topics if topic.get("是否建议进入制作") == "是"],
        key=lambda row: int(row.get("编辑判断分", 0) or 0),
        reverse=True,
    )
    top_fps = {topic.get("内容指纹") for topic in recommended[:3]}
    for topic in topics:
        if topic.get("内容指纹") in top_fps:
            topic["今日建议级别"] = "今日最值得做"
        elif topic.get("是否建议进入制作") == "是":
            topic["今日建议级别"] = "可选候选"
        elif topic.get("是否建议进入制作") == "暂存观察":
            topic["今日建议级别"] = "暂存观察"
        else:
            topic["今日建议级别"] = "不建议制作"
    return topics


def assign_publish_metadata(topics: list[dict[str, Any]], item_by_fp: dict[str, ContentItem]) -> list[dict[str, Any]]:
    for topic in topics:
        item = item_by_fp.get(topic.get("内容指纹", ""))
        if item:
            ensure_publish_metadata(topic, item)
    return topics


def title_generation_rule(item: ContentItem, scene: str) -> str:
    if item.source_type == "AIHOT热点":
        return hotspot_angle(item, scene).get("标题规则", "aihot_unknown")
    if item.source_type == "公众号文章":
        return "article_fulltext_subject"
    if item.source_type == "对标视频" and item.fetch_method == "douyin_public_router_data":
        return "douyin_shallow_title_only"
    return f"competitor_{scene}"


def angle_score(item: ContentItem, scene: str) -> int:
    angle = hotspot_angle(item, scene) if item.source_type == "AIHOT热点" else {}
    if angle.get("角度类型") and angle.get("角度类型") != "暂存观察":
        return 5
    text = item_text(item)
    if any(k in text for k in ["流程", "Brief", "分镜", "Agent", "清单", "模板", "复盘", "转化"]):
        return 4
    return 2


def score_item(item: ContentItem, scene: str) -> int:
    text = item_text(item)
    angle = hotspot_angle(item, scene) if item.source_type == "AIHOT热点" else {}
    heat = 5 if item.source_type == "AIHOT热点" else 3
    account_angle = angle_score(item, scene)
    business = 5 if any(k in text for k in ["流程", "SOP", "清单", "Brief", "分镜", "Agent", "复盘", "模板", "产品", "工具", "团队"]) else 3
    diff = 5 if account_angle >= 4 else 3
    action = 5 if any(k in text for k in ["工具", "模板", "流程", "Agent", "视频", "内容", "产品", "服务", "发布", "更新"]) else 3
    cost_reverse = 4 if item.body_snippet or item.cover_text else 2
    score = round(
        heat * 20 / 5
        + account_angle * 20 / 5
        + business * 20 / 5
        + diff * 15 / 5
        + action * 15 / 5
        + cost_reverse * 10 / 5
    )
    if angle.get("角度类型") == "暂存观察":
        score = min(score, 64)
    return score


def recommend_action(item: ContentItem, score: int, scene: str) -> str:
    angle = angle_score(item, scene)
    if item.fetch_status == "failed" or angle < 3:
        return "不做" if score < 60 else "暂存观察"
    if item.source_type == "AIHOT热点":
        angle_type = hotspot_angle(item, scene).get("角度类型", "")
        if score >= 90 and angle_type in {"Agent落地", "AI导演流程"}:
            return "进入Brief"
        if score >= 82 and angle_type in {"产品生死线", "内容团队变化", "商业化机会", "品牌风控/信任危机"}:
            return "立即蹭热点"
        if score >= 76:
            return "本周做"
        if score >= 62:
            return "暂存观察"
        return "不做"
    if score >= 86:
        return "进入Brief"
    if score >= 76:
        return "本周做"
    if score >= 62:
        return "暂存观察"
    return "不做"


def breakdown(item: ContentItem) -> dict[str, Any]:
    text = item_text(item)
    scene = choose_scene(text)
    hook = detect_hook(item)
    structure = infer_structure(item)
    proof = proof_method(item)
    entrance = commercial_entrance(item)
    score = score_item(item, scene)
    action = recommend_action(item, score, scene)
    worth = "是" if action in {"立即蹭热点", "进入Brief", "本周做"} else "否"
    hot = hotspot_angle(item, scene) if item.source_type == "AIHOT热点" else {
        "角度类型": "对标内容拆解",
        "我的蹭热点角度": "不是热点切入，而是学习对标内容的钩子、结构、专业证明和转化方式。",
        "影响对象": "账号内容结构、信任感和商业入口。",
    }
    if item.source_type == "AIHOT热点":
        column = normalize_column(COLUMN_BY_SCENE.get(scene, "真实工作流改造"))
        if hot["角度类型"] == "AI导演流程":
            column = "AI导演工作流"
        elif hot.get("标题规则") in {"chatgpt_default_ai_interface", "traffic_radar_growth_check"}:
            column = "AI业务定调"
        elif hot.get("标题规则") in {"ai_gateway_ops_validation", "enterprise_ai_partnership_workflow"}:
            column = "AI项目复盘"
    else:
        column = normalize_column(item.column or COLUMN_BY_SCENE.get(scene, "真实工作流改造"))
    return {
        "内容指纹": item.fingerprint,
        "来源类型": item.source_type,
        "平台": item.platform,
        "账号名/公众号名": item.account_name,
        "内容标题": item.title,
        "内容链接": item.url,
        "对应栏目": column,
        "热点切入方式": hot["角度类型"],
        "这条内容讲了什么": item.body_snippet[:280] or item.cover_text or "待补内容文本",
        "标题/前三秒钩子": hook,
        "内容结构": structure,
        "专业性证明方式": proof,
        "商业入口/转化动作": entrance,
        "我可以学什么": item.learn_focus or f"学习它如何用{hook}和{structure}组织内容。",
        "不能照搬什么": item.do_not_copy or "不能复制对方人设、案例、数据和表达，必须换成你的业务现场。",
        "如何转成我的业务现场选题": item.convert_direction or f"从{scene}切入，转成旧流程痛点 -> AI介入点 -> 可展示结果 -> 可沉淀资产。",
        "这个热点为什么值得蹭": "有时效性和话题窗口，可借势表达业务影响。" if item.source_type == "AIHOT热点" else "非热点内容，价值在结构拆解。",
        "普通AI资讯号会怎么讲": "复述发布时间、参数、性能、融资、产品能力或官方说法。" if item.source_type == "AIHOT热点" else "通常会复述原作者观点或总结内容。",
        "我的蹭热点角度": hot["我的蹭热点角度"],
        "影响对象": hot["影响对象"],
        "是否进入今日10选题": worth,
        "推荐动作": action,
        "推荐分": score,
        "失败原因": item.failure_reason,
    }


def topic_from_breakdown(row: dict[str, Any], item: ContentItem) -> dict[str, Any]:
    scene = choose_scene(item_text(item))
    topic_title = regular_topic_title(item, scene)
    title_rule = title_generation_rule(item, scene)
    profile = business_profile(scene, row["热点切入方式"])
    column = normalize_column(row["对应栏目"])
    if row["热点切入方式"] == "产品生死线":
        column = "AI业务定调"
    if "AI公司讲幕后故事" in topic_title:
        column = "真实工作流改造"
        profile = {
            "业务场景": "AI公司技术叙事与内容信任构建",
            "旧流程痛点": "技术团队讲能力时容易堆术语和节点，内容团队很难把幕后探索转成用户能理解、愿意相信的专业叙事。",
            "AI介入点": "用AI辅助提炼技术故事里的冲突、证据、角色和用户影响，但专业判断和取舍由人完成。",
            "可展示结果": "技术幕后故事拆解卡 + 信任感表达清单",
            "可沉淀资产": "AI公司技术叙事拆解清单",
        }
    if "视觉验收" in topic_title:
        column = "真实工作流改造"
        profile = {
            "业务场景": "内容团队视觉物料验收流程",
            "旧流程痛点": "图像模型持续升级后，团队容易只看生成效果，却缺少品牌一致性、使用场景、修改标准和验收边界。",
            "AI介入点": "用AI生成和改版视觉素材，人负责品牌调性、版式优先级、事实风险和最终验收。",
            "可展示结果": "视觉物料验收表 + 修改反馈示例",
            "可沉淀资产": "内容视觉物料验收清单",
        }
    return {
        "我的选题标题": topic_title,
        "内部切入角度": topic_title,
        "可发布标题": "",
        "内容类型": "",
        "平台建议": "",
        "标题风格": "",
        "标题备选": "",
        "标题是否过度内部化": "",
        "标题改写原因": "",
        "真实用户问题": "",
        "为什么今天值得做": "",
        "我能讲出的独特角度": "",
        "我的账号为什么能讲": "",
        "是否只是资讯搬运": "",
        "是否有足够内容支撑": "",
        "不建议做的原因": "",
        "内容可信度": "",
        "人设匹配分": "",
        "编辑判断分": "",
        "标题质量分": "",
        "AI味风险": "",
        "是否建议进入制作": "",
        "主编判断": "",
        "模板词命中情况": "",
        "今日建议级别": "",
        "推荐动作原因": "",
        "降级原因": "",
        "来源内容": row["内容标题"],
        "来源链接": item.url,
        "来源类型": row["来源类型"],
        "对应栏目": normalize_column(column),
        "热点切入方式": row["热点切入方式"],
        "这个热点为什么值得蹭": row["这个热点为什么值得蹭"],
        "普通AI资讯号会怎么讲": row["普通AI资讯号会怎么讲"],
        "我的蹭热点角度": row["我的蹭热点角度"],
        "影响对象": row["影响对象"],
        "业务场景": profile["业务场景"],
        "旧流程痛点": profile["旧流程痛点"],
        "AI介入点": profile["AI介入点"],
        "可展示结果": profile["可展示结果"],
        "可沉淀资产": profile["可沉淀资产"],
        "推荐理由": f"{row['标题/前三秒钩子']}；{row['专业性证明方式']}；适合转成{scene}。",
        "推荐动作": row["推荐动作"],
        "推荐分": row["推荐分"],
        "内容指纹": row["内容指纹"],
        "相关来源": "",
        "标题生成规则": title_rule,
        "事件锚点": extract_event_anchor(item),
        "业务变化判断": infer_business_change(item, scene),
        "标题结构模板": title_structure_template(topic_title),
        "是否来自已解析URL复用": item.reused_url,
        "候选来源方式": "URL投喂/复用" if item.fetch_method in {"wechat_public_html_js_content", "douyin_public_router_data", "rss_atom_xml", "jina_reader"} else item.source_type,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def item_row(item: ContentItem) -> dict[str, Any]:
    return {
        "来源类型": item.source_type,
        "平台": item.platform,
        "账号名/公众号名": item.account_name,
        "内容标题": item.title,
        "内容链接": item.url,
        "内容形态": item.content_shape,
        "封面文字": item.cover_text,
        "正文/字幕/简介片段": item.body_snippet,
        "发布时间": item.published_at,
        "评论区问题": item.comment_questions,
        "截图/OCR文本": item.ocr_text,
        "抓取方式": item.fetch_method,
        "抓取状态": item.fetch_status,
        "失败原因": item.failure_reason,
        "内容指纹": item.fingerprint,
        "对应栏目": normalize_column(item.column),
        "重点学习": item.learn_focus,
        "不能照搬": item.do_not_copy,
        "转化方向": item.convert_direction,
        "是否来自已解析URL复用": item.reused_url,
    }


def require_feishu_env() -> str:
    missing = [name for name in ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_BASE_APP_TOKEN"] if not os.getenv(name)]
    if missing:
        raise SystemExit(f"Feishu write requires environment variables: {', '.join(missing)}")
    return str(os.getenv("FEISHU_BASE_APP_TOKEN"))


def list_tables(token: str, app_token: str) -> dict[str, str]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables", token=token)
    return {item["name"]: item["table_id"] for item in payload.get("data", {}).get("items", [])}


def fields_by_name(token: str, app_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token)
    return {field["field_name"]: field for field in payload.get("data", {}).get("items", [])}


def list_views(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views", token=token)
    return payload.get("data", {}).get("items", [])


def all_records(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page_token = ""
    while True:
        suffix = f"?page_size=500{('&page_token=' + page_token) if page_token else ''}"
        payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records{suffix}", token=token)
        data = payload.get("data", {})
        records.extend(data.get("items", []))
        if not data.get("has_more"):
            return records
        page_token = data.get("page_token", "")


def ensure_content_inbox_fields(token: str, app_token: str, table_id: str) -> list[str]:
    existing = fields_by_name(token, app_token, table_id)
    created: list[str] = []
    for field_name in CONTENT_INBOX_FIELDS:
        if field_name in existing:
            continue
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            token=token,
            body={"field_name": field_name, "type": 1},
        )
        created.append(field_name)
        time.sleep(0.1)
    return created


def is_full_text_item(item: ContentItem) -> str:
    raw_len = item.raw_text_length or len(item.body_snippet or "")
    if item.source_type in {"公众号文章", "公开网页", "RSS/Atom"} and raw_len > 500:
        return "是"
    return "否"


def parse_note(item: ContentItem) -> str:
    if item.source_type == "对标视频" and item.platform == "抖音":
        return "P0浅层解析：当前仅含标题/文案/作者/封面/发布时间，不含口播字幕和评论区。"
    if is_full_text_item(item) == "是":
        raw_len = item.raw_text_length or len(item.body_snippet or "")
        if raw_len > 20000:
            return f"已解析全文并用于内容拆解；飞书正文字段截断到20000字，原始payload路径保留本地全文，原始长度{raw_len}字。"
        return "已解析全文并用于内容拆解；原始payload路径保留本地全文。"
    if item.source_type == "AIHOT热点":
        return "AIHOT条目摘要进入内容拆解；建议发布前回原文核对。"
    return "已进入内容拆解，按当前可获取文本分析。"


def item_to_content_inbox_fields(item: ContentItem, run_id: str, is_new: bool, duplicate: bool = False) -> dict[str, str]:
    status = "success" if item.fetch_status == "ok" else item.fetch_status
    failed = status not in {"ok", "success"}
    body = item.body_snippet or ""
    raw_len = item.raw_text_length or len(body)
    date = today_slug()
    return {
        "标题": item.title or item.url,
        "来源类型": item.source_type,
        "来源名称": item.account_name or item.platform,
        "平台": item.platform,
        "链接": item.url,
        "发布时间": item.published_at,
        "采集时间": now_iso(),
        "采集状态": status,
        "失败原因": item.failure_reason,
        "摘要/片段": body[:1000],
        "作者/账号": item.account_name,
        "内容指纹": item.fingerprint,
        "正文/全文": body[:20000],
        "正文长度": str(raw_len),
        "是否全文解析": is_full_text_item(item),
        "原始payload路径": item.ocr_text if item.fetch_method in {"wechat_public_html_js_content", "douyin_public_router_data", "rss_atom_xml", "jina_reader"} else "",
        "解析说明": parse_note(item),
        "运行日期": date,
        "运行批次": run_id if is_new else "",
        "是否本次新增": "是" if is_new else "否",
        "最近参与运行批次": run_id,
        "最近采样日期": date,
        "是否重复": "是" if duplicate else "否",
        "处理状态": "重复" if duplicate else ("跳过" if failed else "待分析"),
    }


def batch_create_records(token: str, app_token: str, table_id: str, rows: list[dict[str, str]]) -> int:
    total = 0
    for start in range(0, len(rows), 500):
        chunk = rows[start:start + 500]
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
            token=token,
            body={"records": [{"fields": {key: row.get(key, "") for key in CONTENT_INBOX_FIELDS}} for row in chunk]},
        )
        total += len(chunk)
        time.sleep(0.15)
    return total


def update_record_fields(token: str, app_token: str, table_id: str, record_id: str, fields: dict[str, str]) -> None:
    feishu.request_json(
        "PUT",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
        token=token,
        body={"fields": fields},
    )


def ensure_content_inbox_today_view(token: str, app_token: str, table_id: str) -> dict[str, Any]:
    views = {view.get("view_name"): view for view in list_views(token, app_token, table_id)}
    created: list[str] = []
    if "今日采集" not in views:
        payload = feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/views",
            token=token,
            body={"view_name": "今日采集", "view_type": "grid"},
        )
        views["今日采集"] = payload.get("data", {}).get("view", payload.get("data", {}))
        created.append("今日采集")
        time.sleep(0.1)
    fields = fields_by_name(token, app_token, table_id)
    view = views.get("今日采集", {})
    date_field = fields.get("最近采样日期") or fields.get("运行日期")
    if not view.get("view_id") or not date_field:
        return {"created": created, "configured": "missing_view_or_date_field"}
    visible = {"标题", "来源类型", "来源名称", "平台", "链接", "摘要/片段", "正文长度", "是否全文解析", "原始payload路径", "解析说明", "采集状态", "处理状态", "最近采样日期", "最近参与运行批次", "是否本次新增", "内容指纹"}
    hidden = [field["field_id"] for name, field in fields.items() if name not in visible]
    body = {
        "view_name": "今日采集",
        "property": {
            "filter_info": {
                "conditions": [{
                    "field_id": date_field["field_id"],
                    "operator": "is",
                    "value": json.dumps([today_slug()], ensure_ascii=False),
                }],
                "conjunction": "and",
            },
            "hidden_fields": hidden,
        },
    }
    try:
        feishu.request_json("PATCH", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views/{view['view_id']}", token=token, body=body)
        return {"created": created, "configured": "ok", "hidden_fields": len(hidden)}
    except Exception as exc:
        return {"created": created, "configured": f"failed:{exc}"}


def write_content_ledger_to_feishu(items: list[ContentItem], run_id: str) -> dict[str, Any]:
    app_token = require_feishu_env()
    token = feishu.tenant_token()
    table_id = resolve_table_id(list_tables(token, app_token), "content_inbox")
    if not table_id:
        raise SystemExit(f"Missing Feishu table: {table_name('content_inbox')}")
    created_fields = ensure_content_inbox_fields(token, app_token, table_id)
    existing = all_records(token, app_token, table_id)
    by_fp = {str(record.get("fields", {}).get("内容指纹", "")): record for record in existing if record.get("fields", {}).get("内容指纹")}
    by_url = {str(record.get("fields", {}).get("链接", "")): record for record in existing if record.get("fields", {}).get("链接")}
    to_create: list[dict[str, str]] = []
    updated_existing = 0
    skipped_duplicates = 0
    for item in items:
        record = by_fp.get(item.fingerprint) or by_url.get(item.url)
        if record:
            record_id = record.get("record_id") or record.get("id") or ""
            if not record_id:
                skipped_duplicates += 1
                continue
            record_fields = record.get("fields", {})
            same_run_new = str(record_fields.get("运行批次", "")) == run_id or str(record_fields.get("最近参与运行批次", "")) == run_id
            fields = item_to_content_inbox_fields(item, run_id, is_new=same_run_new, duplicate=not same_run_new)
            update_fields = {
                "最近参与运行批次": fields["最近参与运行批次"],
                "最近采样日期": fields["最近采样日期"],
                "是否本次新增": "是" if same_run_new else "否",
                "是否重复": str(record_fields.get("是否重复", "否")) if same_run_new else "是",
            }
            update_record_fields(token, app_token, table_id, record_id, update_fields)
            updated_existing += 1
            if not same_run_new:
                skipped_duplicates += 1
            time.sleep(0.1)
            continue
        fields = item_to_content_inbox_fields(item, run_id, is_new=True, duplicate=False)
        to_create.append(fields)
        by_fp[item.fingerprint] = {"record_id": ""}
        by_url[item.url] = {"record_id": ""}
    created_records = batch_create_records(token, app_token, table_id, to_create) if to_create else 0
    return {
        "table": table_name("content_inbox"),
        "run_id": run_id,
        "created_fields": created_fields,
        "created_records": created_records,
        "updated_existing": updated_existing,
        "skipped_duplicates": skipped_duplicates,
        "today_view": ensure_content_inbox_today_view(token, app_token, table_id),
    }


def write_today10_markdown(path: Path, topics: list[dict[str, Any]], logs: list[str]) -> None:
    best = next((t for t in topics if t["推荐动作"] == "立即蹭热点"), topics[0] if topics else None)
    lines = [
        f"# 今日10选题 {datetime.now().strftime('%Y-%m-%d')}",
        "",
        "定位提醒：这不是热点榜，也不是竞品数据榜。每条选题都来自 AIHOT 热点、对标视频或公众号文章的内容拆解，并被转成 AI业务系统导演 视角。",
        "",
    ]
    if best:
        lines.extend([
            "## 今日最建议动作",
            f"- 推荐：{best.get('可发布标题') or best['我的选题标题']}",
            f"- 内部切入角度：{best.get('内部切入角度') or best['我的选题标题']}",
            f"- 动作：{best['推荐动作']}",
            "- 理由：今天更适合先蹭一个有明确差异化角度的热点，做 30-60 秒短评；不要同时把多条都推进成完整 Brief。",
            "",
        ])
    for idx, topic in enumerate(topics, start=1):
        lines.extend([
            f"## {idx}. {topic.get('可发布标题') or topic['我的选题标题']}",
            f"- 内部切入角度：{topic.get('内部切入角度') or topic['我的选题标题']}",
            f"- 内容类型：{topic.get('内容类型', '')}",
            f"- 平台建议：{topic.get('平台建议', '')}",
            f"- 标题风格：{topic.get('标题风格', '')}",
            f"- 今日建议级别：{topic.get('今日建议级别', '')}",
            f"- 编辑判断分 / 标题质量分 / AI味风险：{topic.get('编辑判断分', '')} / {topic.get('标题质量分', '')} / {topic.get('AI味风险', '')}",
            f"- 主编判断：{topic.get('主编判断', '')}",
            f"- 人设匹配分：{topic.get('人设匹配分', '')}",
            f"- 真实用户问题：{topic.get('真实用户问题', '')}",
            f"- 为什么今天值得做：{topic.get('为什么今天值得做', '')}",
            f"- 来源：{topic['来源类型']} / {topic['来源内容']}",
            f"- 栏目：{topic['对应栏目']}",
            f"- 热点切入方式：{topic['热点切入方式']}",
            f"- 这个热点为什么值得蹭：{topic['这个热点为什么值得蹭']}",
            f"- 普通AI资讯号会怎么讲：{topic['普通AI资讯号会怎么讲']}",
            f"- 我的蹭热点角度：{topic['我的蹭热点角度']}",
            f"- 影响对象：{topic['影响对象']}",
            f"- 业务场景：{topic['业务场景']}",
            f"- 旧流程痛点：{topic['旧流程痛点']}",
            f"- AI介入点：{topic['AI介入点']}",
            f"- 可展示结果：{topic['可展示结果']}",
            f"- 可沉淀资产：{topic['可沉淀资产']}",
            f"- 推荐理由：{topic['推荐理由']}",
            f"- 推荐动作：{topic['推荐动作']}",
            "",
        ])
    lines.extend(["## 采样日志", *[f"- {log}" for log in logs], ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def text_basis(item: ContentItem) -> str:
    if item.source_type == "公众号文章":
        if is_full_text_item(item) == "是":
            return "全文"
        if item.reused_url == "是":
            return "复用内容不完整：仅摘要"
        return "摘要/片段"
    if item.source_type == "对标视频" and item.fetch_method == "douyin_public_router_data":
        return "抖音P0浅层字段：标题/文案/作者/封面/标签/下载链接，不含口播转写"
    if item.source_type == "AIHOT热点":
        return "AIHOT摘要/日报条目"
    return "当前可获取文本"


def debug_flags(topic: dict[str, Any], item: ContentItem, template_counts: dict[str, int]) -> tuple[str, str, str, str, str]:
    title = topic.get("可发布标题") or topic.get("我的选题标题", "")
    template = title_structure_template(title)
    repeated = "是" if template != "specific" and template_counts.get(template, 0) > 2 else "否"
    event = specific_event_title(item)
    anchor = extract_event_anchor(item)
    kept_anchor = "是" if not anchor or anchor[:4] in title or specific_event_title(item)[:4] in title else "否"
    detached = "否" if kept_anchor == "是" else "是"
    over_infer = "是" if item.source_type == "对标视频" and item.fetch_method == "douyin_public_router_data" and any(k in title for k in ["完整", "口播全文", "镜头结构", "评论"]) else "否"
    reasons: list[str] = []
    if repeated == "是":
        reasons.append(f"同批标题结构 {template} 重复")
    if detached == "是":
        reasons.append("标题未明显保留原始热点事件词")
    if item.source_type == "对标视频" and item.fetch_method == "douyin_public_router_data":
        reasons.append("抖音仅浅层解析，缺口播字幕/评论，深度结论需人工复核")
    if item.source_type == "公众号文章" and is_full_text_item(item) != "是":
        reasons.append("公众号未达到全文解析阈值")
    if over_infer == "是":
        reasons.append("抖音浅层解析标题超出可支撑范围")
    return repeated, detached, kept_anchor, over_infer, "；".join(reasons) or "无"


def write_debug_top10(
    topics: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    breakdown_by_fp: dict[str, dict[str, Any]],
    item_by_fp: dict[str, ContentItem],
) -> None:
    for topic in topics:
        item = item_by_fp.get(topic.get("内容指纹", ""))
        if item:
            ensure_publish_metadata(topic, item)
            editorial_judgement(topic, item)
    template_counts = collections.Counter(title_structure_template(topic.get("可发布标题") or topic.get("我的选题标题", "")) for topic in topics)
    selected_fps = {topic.get("内容指纹", "") for topic in topics}
    rank_by_fp = {topic.get("内容指纹", ""): rank for rank, topic in enumerate(topics, start=1)}
    ordered = [*topics, *[candidate for candidate in candidates if candidate.get("内容指纹", "") not in selected_fps]]
    rows: list[dict[str, Any]] = []
    for topic in ordered:
        fp = topic.get("内容指纹", "")
        item = item_by_fp.get(fp)
        row = breakdown_by_fp.get(fp, {})
        if not item:
            continue
        ensure_publish_metadata(topic, item)
        editorial_judgement(topic, item)
        repeated, detached, kept_anchor, over_infer, review_reason = debug_flags(topic, item, template_counts)
        in_top10 = fp in selected_fps
        rows.append({
            "今日排名": rank_by_fp.get(fp, ""),
            "是否进入Top10": "是" if in_top10 else "否",
            "是否进入候选但未进Top10": "否" if in_top10 else "是",
            "原始来源标题": item.title,
            "原始来源类型": item.source_type,
            "原始摘要/片段": (item.body_snippet or item.cover_text)[:500],
            "文本使用方式": text_basis(item),
            "事件锚点": topic.get("事件锚点", extract_event_anchor(item)),
            "业务变化判断": topic.get("业务变化判断", infer_business_change(item, choose_scene(item_text(item)))),
            "命中的栏目": topic.get("对应栏目", ""),
            "命中的场景映射规则": choose_scene(item_text(item)),
            "栏目映射来源": "hotspot_angle_override" if item.source_type == "AIHOT热点" else ("source_config" if item.column else "choose_scene"),
            "命中的标题模板或标题生成规则": topic.get("标题生成规则", title_generation_rule(item, choose_scene(item_text(item)))),
            "标题结构模板": title_structure_template(topic.get("可发布标题") or topic.get("我的选题标题", "")),
            "推荐动作": topic.get("推荐动作", ""),
            "推荐分": topic.get("推荐分", ""),
            "内部切入角度": topic.get("内部切入角度", topic.get("我的选题标题", "")),
            "可发布标题": topic.get("可发布标题", ""),
            "标题备选": topic.get("标题备选", ""),
            "内容类型": topic.get("内容类型", ""),
            "平台建议": topic.get("平台建议", ""),
            "标题风格": topic.get("标题风格", ""),
            "标题是否过度内部化": topic.get("标题是否过度内部化", ""),
            "标题改写原因": topic.get("标题改写原因", ""),
            "真实用户问题": topic.get("真实用户问题", ""),
            "人设匹配分": topic.get("人设匹配分", ""),
            "我的账号为什么能讲": topic.get("我的账号为什么能讲", ""),
            "为什么今天值得做": topic.get("为什么今天值得做", ""),
            "我能讲出的独特角度": topic.get("我能讲出的独特角度", ""),
            "是否只是资讯搬运": topic.get("是否只是资讯搬运", ""),
            "是否有足够内容支撑": topic.get("是否有足够内容支撑", ""),
            "不建议做的原因": topic.get("不建议做的原因", ""),
            "内容可信度": topic.get("内容可信度", ""),
            "编辑判断分": topic.get("编辑判断分", ""),
            "标题质量分": topic.get("标题质量分", ""),
            "AI味风险": topic.get("AI味风险", ""),
            "是否建议进入制作": topic.get("是否建议进入制作", ""),
            "主编判断": topic.get("主编判断", ""),
            "模板词命中情况": topic.get("模板词命中情况", ""),
            "今日建议级别": topic.get("今日建议级别", ""),
            "推荐动作原因": topic.get("推荐动作原因", ""),
            "最终选题标题": topic.get("可发布标题") or topic.get("我的选题标题", ""),
            "为什么推荐": topic.get("推荐理由", ""),
            "普通资讯号会怎么讲": topic.get("普通AI资讯号会怎么讲", ""),
            "我的角度是什么": topic.get("我的蹭热点角度", ""),
            "是否疑似模板重复": repeated,
            "是否结构重复": repeated,
            "是否AIHOT重复主题": "是" if topic.get("相关来源") else "否",
            "是否保留真实热点词": kept_anchor,
            "是否疑似脱离原始热点": detached,
            "是否超过解析文本支撑范围": over_infer,
            "是否来自已解析URL复用": "是" if item.reused_url == "是" or topic.get("是否来自已解析URL复用") == "是" else "否",
            "降级或改写原因": topic.get("降级原因") or (review_reason if topic.get("推荐动作") in {"暂存观察", "不做"} else ""),
            "需要人工复核原因": review_reason,
            "内容指纹": fp,
            "内容结构": row.get("内容结构", ""),
        })
    csv_path = OUT / "debug_today10_generation.csv"
    md_path = OUT / "debug_today10_generation.md"
    write_csv(csv_path, rows)
    lines = [
        f"# 今日Top10生成诊断 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "这份文件用于审计标题生成、场景映射和模板重复，不写入飞书。",
        "",
    ]
    for row in rows:
        prefix = f"## {row['今日排名']}. " if row["今日排名"] else "## 候选未入选. "
        lines.extend([
            f"{prefix}{row['最终选题标题']}",
            f"- 是否进入Top10：{row['是否进入Top10']}",
            f"- 原始来源：{row['原始来源类型']} / {row['原始来源标题']}",
            f"- 文本使用方式：{row['文本使用方式']}",
            f"- 内部切入角度：{row['内部切入角度']}",
            f"- 真实用户问题：{row['真实用户问题']}",
            f"- 人设匹配分：{row['人设匹配分']}",
            f"- 我的账号为什么能讲：{row['我的账号为什么能讲']}",
            f"- 为什么今天值得做：{row['为什么今天值得做']}",
            f"- 是否只是资讯搬运：{row['是否只是资讯搬运']}",
            f"- 是否有足够内容支撑：{row['是否有足够内容支撑']}",
            f"- 独特角度：{row['我能讲出的独特角度']}",
            f"- 内容可信度/编辑分/标题分/AI味：{row['内容可信度']} / {row['编辑判断分']} / {row['标题质量分']} / {row['AI味风险']}",
            f"- 主编判断：{row['主编判断']}",
            f"- 模板词命中情况：{row['模板词命中情况']}",
            f"- 是否建议进入制作：{row['是否建议进入制作']} / {row['今日建议级别']}",
            f"- 推荐动作原因：{row['推荐动作原因']}",
            f"- 不建议做的原因：{row['不建议做的原因']}",
            f"- 内容类型/平台建议：{row['内容类型']} / {row['平台建议']}",
            f"- 标题风格：{row['标题风格']}",
            f"- 标题改写原因：{row['标题改写原因']}",
            f"- 事件锚点：{row['事件锚点']}",
            f"- 业务变化判断：{row['业务变化判断']}",
            f"- 场景映射：{row['命中的场景映射规则']} -> {row['命中的栏目']}",
            f"- 标题规则：{row['命中的标题模板或标题生成规则']}",
            f"- 标题结构模板：{row['标题结构模板']}",
            f"- 推荐动作/分数：{row['推荐动作']} / {row['推荐分']}",
            f"- 我的角度：{row['我的角度是什么']}",
            f"- 是否结构重复：{row['是否结构重复']}",
            f"- 是否保留真实热点词：{row['是否保留真实热点词']}",
            f"- 是否超过解析文本支撑范围：{row['是否超过解析文本支撑范围']}",
            f"- 是否来自已解析URL复用：{row['是否来自已解析URL复用']}",
            f"- 人工复核：{row['需要人工复核原因']}",
            "",
        ])
    md_path.write_text("\n".join(lines), encoding="utf-8")


def assign_action_quotas(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    immediate = 0
    brief = 0
    weekly = 0
    for topic in topics:
        if topic.get("是否建议进入制作") != "是":
            topic["推荐动作"] = "暂存观察" if topic.get("是否建议进入制作") == "暂存观察" else "不做"
            continue
        desired = topic["推荐动作"]
        if desired == "立即蹭热点":
            immediate += 1
            if immediate > 4:
                topic["推荐动作"] = "暂存观察"
        elif desired == "进入Brief":
            brief += 1
            if brief > 2:
                topic["推荐动作"] = "本周做" if weekly < 2 else "暂存观察"
                if topic["推荐动作"] == "本周做":
                    weekly += 1
        elif desired == "本周做":
            weekly += 1
            if weekly > 2:
                topic["推荐动作"] = "暂存观察"
    return topics


def theme_cluster(topic: dict[str, Any]) -> str:
    text = " ".join([
        topic.get("我的选题标题", ""),
        topic.get("来源内容", ""),
        topic.get("我的蹭热点角度", ""),
        topic.get("可沉淀资产", ""),
    ])
    lower = text.lower()
    if any(k in text for k in ["公众号首图", "小红书", "宣传图", "视觉物料", "视觉模板", "图文卡"]):
        return "视觉物料自动化"
    if any(k in text for k in ["AI假人", "Shein", "虚假广告", "素材审核", "品牌风控"]):
        return "品牌素材风控"
    if any(k in lower for k in ["runway", "kling", "seedance", "sora"]) or any(k in text for k in ["AI视频", "分镜", "成片"]):
        return "AI视频导演流程"
    if any(k in lower for k in ["llamaindex", "openrouter", "codex", "mcp"]) or any(k in text for k in ["Agent", "智能体", "任务验收"]):
        return "Agent任务验收"
    if any(k in text for k in ["选题", "Brief", "对标内容"]):
        return "内容选题Brief"
    return fingerprint(topic.get("来源内容", ""), topic.get("我的选题标题", ""))


def similar_asset_key(topic: dict[str, Any]) -> str:
    asset = topic["可沉淀资产"]
    cluster = theme_cluster(topic)
    if topic.get("热点切入方式") == "产品生死线":
        return cluster
    if cluster in {"视觉物料自动化", "品牌素材风控", "AI视频导演流程", "Agent任务验收", "内容选题Brief"}:
        return cluster
    if any(k in asset for k in ["视觉", "首图", "宣传图", "图文", "品牌审核"]):
        return "内容视觉物料人工判断"
    if any(k in asset for k in ["素材审核", "风控", "风险"]):
        return "品牌AI素材审核"
    if any(k in asset for k in ["Agent", "任务拆解", "验收"]):
        return "Agent任务验收"
    if any(k in asset for k in ["视频", "分镜", "导演"]):
        return "AI视频导演工作流"
    if any(k in asset for k in ["Brief", "选题"]):
        return "内容选题Brief"
    return re.sub(r"\s+", "", asset)[:18]


def topic_theme_key(topic: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        topic["业务场景"],
        topic["热点切入方式"],
        similar_asset_key(topic),
        topic.get("标题生成规则", ""),
    )


def merge_same_theme(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for topic in candidates:
        key = topic_theme_key(topic)
        if key not in by_key:
            by_key[key] = topic
            merged.append(topic)
            continue
        kept = by_key[key]
        related = [part for part in kept.get("相关来源", "").split("；") if part]
        if topic["热点切入方式"] in {"内容团队变化", "AI导演流程", "品牌风控/信任危机"}:
            related_source = f"{topic['来源类型']}：{topic['来源内容']}"
            kept_source = f"{kept['来源类型']}：{kept['来源内容']}"
            if related_source != kept_source:
                related.append(related_source)
        kept["相关来源"] = "；".join(dict.fromkeys(related))
    return merged


def credibility_rank(topic: dict[str, Any]) -> int:
    return {
        "全文": 5,
        "摘要": 3,
        "AIHOT摘要": 3,
        "摘要可用": 3,
        "抖音浅层": 1,
    }.get(topic.get("内容可信度", ""), 2)


def ai_risk_rank(topic: dict[str, Any]) -> int:
    return {"低": 3, "中": 2, "高": 0}.get(topic.get("AI味风险", ""), 1)


def editorial_sort_key(topic: dict[str, Any]) -> tuple[int, int, int, int, int, int, int]:
    suggested = 1 if topic.get("是否建议进入制作") == "是" else 0
    return (
        suggested,
        ai_risk_rank(topic),
        int(topic.get("编辑判断分", 0) or 0),
        int(topic.get("标题质量分", 0) or 0),
        credibility_rank(topic),
        int(topic.get("人设匹配分", 0) or 0),
        int(topic.get("推荐分", 0) or 0),
    )


def quality_band(topic: dict[str, Any]) -> str:
    if topic.get("是否建议进入制作") == "是" and topic.get("AI味风险") != "高":
        return "make"
    if topic.get("是否建议进入制作") == "暂存观察":
        return "watch"
    return "no"


def select_today10(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select Top10 after editorial judgement; column quota is only a soft tie-breaker.

    Freeze the anti-template baseline:
    - no natural angle -> keep as watch, do not force a title;
    - insufficient support -> keep as watch, do not pretend it is production-ready;
    - weak persona fit -> keep as watch, do not rescue it with workflow/checklist wording;
    - 今日最值得做 can be only one item; never fill it by quota.
    """
    for row in candidates:
        row["对应栏目"] = normalize_column(row["对应栏目"])
    sorted_candidates = merge_same_theme(sorted(candidates, key=editorial_sort_key, reverse=True))

    selected: list[dict[str, Any]] = []
    seen_fp: set[str] = set()
    seen_titles: set[str] = set()
    template_counts: dict[str, int] = {}
    column_counts: dict[str, int] = {}

    def add(row: dict[str, Any], allow_overflow: bool = False) -> bool:
        if row["内容指纹"] in seen_fp or len(selected) >= 10:
            return False
        visible_title = (row.get("可发布标题") or row.get("来源内容") or row.get("我的选题标题", "")).strip()
        if visible_title and visible_title in seen_titles:
            return False
        if row.get("来源类型") == "AIHOT热点" and not allow_overflow:
            if sum(1 for item in selected if item.get("来源类型") == "AIHOT热点") >= 8:
                return False
        template = title_structure_template(row.get("可发布标题") or row.get("我的选题标题", ""))
        if template != "specific" and template_counts.get(template, 0) >= 2:
            return False
        column = normalize_column(row.get("对应栏目", ""))
        _minimum, maximum = TOP10_COLUMN_LIMITS.get(column, (0, 10))
        if not allow_overflow and quality_band(row) != "make" and column_counts.get(column, 0) >= maximum:
            return False
        selected.append(row)
        seen_fp.add(row["内容指纹"])
        if visible_title:
            seen_titles.add(visible_title)
        template_counts[template] = template_counts.get(template, 0) + 1
        column_counts[column] = column_counts.get(column, 0) + 1
        return True

    for band in ("make", "watch"):
        for row in sorted_candidates:
            if quality_band(row) == band:
                add(row)
                if len(selected) >= 10:
                    break
        if len(selected) >= 10:
            break

    for row in sorted_candidates:
        add(row, allow_overflow=True)
        if len(selected) >= 10:
            break

    return selected[:10]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-fetch-aihot", action="store_true")
    parser.add_argument("--manual", default=str(MANUAL_ITEMS))
    parser.add_argument("--write-feishu", action="store_true", help="Write all analyzed ContentItems into Feishu 03 内容收件箱 as the content ledger.")
    parser.add_argument("--run-id", default="", help="Stable run id shared by 03 内容收件箱 and 04 分析与选题.")
    parser.add_argument("--debug-top10", action="store_true", help="Write local Top10 generation diagnostics to output/debug_today10_generation.*")
    args = parser.parse_args()

    run_id = args.run_id or default_run_id()
    items, logs = collect_items(not args.no_fetch_aihot, Path(args.manual))
    item_rows = [item_row(item) for item in items]
    breakdown_rows = [breakdown(item) for item in items]
    item_by_fp = {item.fingerprint: item for item in items}
    breakdown_by_fp = {row["内容指纹"]: row for row in breakdown_rows}
    candidates = [
        topic_from_breakdown(row, item_by_fp[row["内容指纹"]])
        for row in breakdown_rows
        if row["是否进入今日10选题"] == "是"
    ]
    candidates = apply_editorial_judgement(candidates, item_by_fp)
    today10 = select_today10(candidates)
    today10 = assign_action_quotas(today10)
    today10 = apply_editorial_judgement(today10, item_by_fp)
    today10 = assign_today_priority(today10)
    write_debug_top10(today10, candidates, breakdown_by_fp, item_by_fp)

    write_csv(OUT / "content_items.csv", item_rows)
    write_csv(OUT / "content_breakdowns.csv", breakdown_rows)
    write_csv(OUT / "today_10_topics.csv", today10)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    md_path = REPORT_DIR / f"today_10_topics_{datetime.now().strftime('%Y-%m-%d')}.md"
    write_today10_markdown(md_path, today10, logs)
    run_log = {
        "generated_at": now_iso(),
        "run_id": run_id,
        "items": len(items),
        "breakdowns": len(breakdown_rows),
        "today_10_topics": len(today10),
        "logs": logs,
        "outputs": {
            "content_items": str(OUT / "content_items.csv"),
            "content_breakdowns": str(OUT / "content_breakdowns.csv"),
            "today_10_topics": str(OUT / "today_10_topics.csv"),
            "today_10_markdown": str(md_path),
            "debug_top10_csv": str(OUT / "debug_today10_generation.csv"),
            "debug_top10_markdown": str(OUT / "debug_today10_generation.md"),
        },
    }
    if args.write_feishu:
        run_log["feishu_content_ledger"] = write_content_ledger_to_feishu(items, run_id)
    (OUT / "content_sampler_log.json").write_text(json.dumps(run_log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(run_log, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

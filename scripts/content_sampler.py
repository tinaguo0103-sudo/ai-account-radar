#!/usr/bin/env python3
"""Content sampler + teardown pipeline for AI account topic discovery.

This is not a competitor metrics crawler. It treats every input as a content
object, then analyzes hook, structure, proof, commercial entrance, and how it
can become the user's own AI-business-system-director topic.
"""
from __future__ import annotations

import argparse
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

    for raw in load_manual_items(manual_path):
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
            )
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
    if any(k in text for k in visual_content_terms):
        return "内容团队选题到Brief流程"
    if any(k.lower() in lower for k in ["runway", "kling", "luma", "seedance", "视频", "分镜", "镜头", "成片", "宣传图", "视觉物料"]):
        return "AI导演工作流与视频交付" if any(k.lower() in lower for k in ["runway", "kling", "luma", "seedance", "视频", "分镜", "镜头", "成片"]) else "内容团队选题到Brief流程"
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


def hotspot_angle(item: ContentItem, scene: str) -> dict[str, str]:
    text = item_text(item)
    title = short_title(item.title)
    lower = text.lower()
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
        }
    if any(k.lower() in lower for k in ["shein", "ai假人", "虚假广告", "带货", "品牌", "营销", "信任", "合规", "审核", "骗子"]):
        return {
            "角度类型": "品牌风控/信任危机",
            "我的蹭热点角度": "这类热点不该只讲AI翻车，而要讲品牌内容团队如何补素材审核、AI生成标识、虚假人设识别和投放前风控。",
            "影响对象": "品牌内容团队、投放团队、素材审核流程、AI营销合规与消费者信任。",
            "标题": "AI假人带货翻车后，品牌内容团队最该补的是素材审核流程",
        }
    if any(k.lower() in lower for k in ["runway", "kling", "luma", "seedance", "sora", "视频", "图像", "画面", "音效", "剪辑"]):
        if "runway" in lower:
            hot_title = "Runway API开放后，AI视频服务最先被重做的是哪一层"
        elif "luma" in lower:
            hot_title = "Luma更新后，内容团队最该重排的是视觉交付流程"
        else:
            hot_title = "AI视频模型更新后，导演工作流里最先变的是哪一步"
        return {
            "角度类型": "AI导演流程" if any(k.lower() in lower for k in ["runway", "kling", "seedance", "sora", "视频", "分镜", "镜头", "成片"]) else "内容团队变化",
            "我的蹭热点角度": "AI视频模型更新后，导演工作流里最先变的不是prompt，而是Brief、分镜、素材修改和验收标准。",
            "影响对象": "AI视频服务、品牌内容团队、短视频制作流程、分镜和成片验收。",
            "标题": hot_title,
        }
    if any(k.lower() in lower for k in ["agent", "agents", "智能体", "codex", "claude code", "mcp", "llamaindex", "guardrails", "openrouter", "comfyui"]):
        if "llamaindex" in lower:
            hot_title = "非技术人看Agent模板，先别看框架名，要看任务怎么验收"
        elif "openrouter" in lower or "补丁" in text:
            hot_title = "模型能生成补丁后，Codex类工具怎么进入非技术工作流"
        else:
            hot_title = f"非技术人看{title}，先看任务怎么验收"
        return {
            "角度类型": "Agent落地",
            "我的蹭热点角度": f"非技术人看 {title}，不该只看工具名，而要看哪些任务的输入、输出、验收和异常处理能被接管。",
            "影响对象": "Agent服务、企业流程自动化、非技术业务人的重复任务、工具链整合。",
            "标题": hot_title,
        }
    if "文档自动化" in text or "documentation" in lower or "mcg toolkit" in lower:
        return {
            "角度类型": "Agent落地",
            "我的蹭热点角度": "文档自动化不只是省写文档，而是让Agent项目的输入、输出、验收和异常记录开始可交接。",
            "影响对象": "Agent项目交付、模型文档、验收记录、非技术团队协作。",
            "标题": "AI模型文档自动化后，Agent项目最该补的是验收文档",
        }
    if "推理速度" in text or "reasoning speed" in lower or "inference" in lower:
        return {
            "角度类型": "工作流重排",
            "我的蹭热点角度": "模型变快的价值不只是跑分，而是哪些内容生产、资料筛选和复盘任务可以从人工盯着做改成后台自动跑。",
            "影响对象": "内容团队重复任务、资料筛选、日报生成、轻量Agent工作流。",
            "标题": "推理速度变快后，内容团队哪些任务可以交给后台自动跑",
        }
    if any(k.lower() in lower for k in ["模型", "model", "api", "框架", "framework", "平台", "推理", "训练", "开源", "发布", "更新"]):
        if any(k in text for k in ["训练框架", "自研训练", "JAX", "GPU"]):
            hot_title = "大厂自研训练框架变多后，中间层AI工具还能靠什么活"
            angle = "产品生死线"
        elif "gemini" in lower and any(k in text for k in ["幕后", "架构师", "探索"]):
            hot_title = "AI公司讲幕后故事时，内容团队该学的是信任感而不是术语"
            angle = "内容团队变化"
        elif "banana" in lower or "图像" in text or "多模态" in text:
            hot_title = "图像模型继续升级后，内容团队最该重排的是视觉验收"
            angle = "内容团队变化"
        else:
            hot_title = f"{title}背后，哪类AI工具会先失去壁垒"
            angle = "产品生死线"
        return {
            "角度类型": angle,
            "我的蹭热点角度": f"这次 {title} 的重点不是参数，而是它会不会让某类AI产品、插件或中间层工具失去壁垒。",
            "影响对象": "AI工具产品、包装型SaaS、插件生态、内容/运营团队的工具选择。",
            "标题": hot_title,
        }
    if any(k in text for k in ["公众号", "小红书", "图文", "卡片", "文章", "内容", "素材", "设计"]):
        return {
            "角度类型": "内容团队变化",
            "我的蹭热点角度": f"{title} 影响的是内容团队从选题、图文素材到复盘的哪一步被合并或重排。",
            "影响对象": "内容团队、品牌运营、图文生产、素材复用和投放复盘。",
            "标题": f"{title}发布后，内容团队最该重排的是哪一步",
        }
    if scene == "汽车与内容营销流程":
        return {
            "角度类型": "商业化机会",
            "我的蹭热点角度": f"这个热点要看它如何改变汽车与品牌内容团队的素材生产、审核、卖点表达和信任建立。",
            "影响对象": "汽车内容团队、品牌营销、素材审核、投放前风控和产品卖点表达。",
            "标题": f"{title}背后，品牌内容团队该重做哪条审核线",
        }
    return {
        "角度类型": "暂存观察",
        "我的蹭热点角度": "目前只能看出资讯价值，业务影响和行动建议还不够明确，适合暂存观察。",
        "影响对象": "待补：需要进一步判断影响哪类产品、流程、团队或商业机会。",
        "标题": f"{title} 可以蹭，但要先找到业务影响而不是复述资讯",
    }


def regular_topic_title(item: ContentItem, scene: str) -> str:
    core = short_title(item.title)
    if item.source_type == "AIHOT热点":
        return hotspot_angle(item, scene)["标题"]
    if scene == "AI导演工作流与视频交付":
        return "AI视频不是prompt，而是从Brief到分镜到验收的交付流程"
    if scene == "非技术Agent处理重复业务任务":
        return "非技术人做Agent，先别聊工具，先把任务拆成输入输出和验收"
    if scene == "汽车与内容营销流程":
        return "AI营销素材越容易生成，品牌团队越要先补审核流程"
    if scene == "项目复盘与能力产品化":
        return "做AI项目不能只复盘情绪，要把过程沉淀成模板和服务入口"
    return "一个内容团队如何把AI热点和对标内容转成可执行Brief"


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
    heat = 5 if item.source_type == "AIHOT热点" else 3
    account_angle = angle_score(item, scene)
    business = 5 if any(k in text for k in ["流程", "SOP", "清单", "Brief", "分镜", "Agent", "复盘", "模板", "产品", "工具", "团队"]) else 3
    diff = 5 if account_angle >= 4 else 3
    action = 5 if any(k in text for k in ["工具", "模板", "流程", "Agent", "视频", "内容", "产品", "服务", "发布", "更新"]) else 3
    cost_reverse = 4 if item.body_snippet or item.cover_text else 2
    return round(
        heat * 20 / 5
        + account_angle * 20 / 5
        + business * 20 / 5
        + diff * 15 / 5
        + action * 15 / 5
        + cost_reverse * 10 / 5
    )


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
    if item.source_type == "AIHOT热点":
        column = normalize_column(COLUMN_BY_SCENE.get(scene, "真实工作流改造"))
    else:
        column = normalize_column(item.column or COLUMN_BY_SCENE.get(scene, "真实工作流改造"))
    hot = hotspot_angle(item, scene) if item.source_type == "AIHOT热点" else {
        "角度类型": "对标内容拆解",
        "我的蹭热点角度": "不是热点切入，而是学习对标内容的钩子、结构、专业证明和转化方式。",
        "影响对象": "账号内容结构、信任感和商业入口。",
    }
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
    if item.source_type in {"公众号文章", "公开网页", "RSS/Atom"} and len(item.body_snippet or "") > 500:
        return "是"
    return "否"


def parse_note(item: ContentItem) -> str:
    if item.source_type == "对标视频" and item.platform == "抖音":
        return "P0浅层解析：当前仅含标题/文案/作者/封面/发布时间，不含口播字幕和评论区。"
    if is_full_text_item(item) == "是":
        return "已解析正文，可用于内容拆解；正文字段可能按飞书长度截断，原始payload路径保留本地全文。"
    if item.source_type == "AIHOT热点":
        return "AIHOT条目摘要进入内容拆解；建议发布前回原文核对。"
    return "已进入内容拆解，按当前可获取文本分析。"


def item_to_content_inbox_fields(item: ContentItem, run_id: str, is_new: bool, duplicate: bool = False) -> dict[str, str]:
    status = "success" if item.fetch_status == "ok" else item.fetch_status
    failed = status not in {"ok", "success"}
    body = item.body_snippet or ""
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
        "正文长度": str(len(body)),
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
            record_fields = record.get("fields", {})
            same_run_new = str(record_fields.get("运行批次", "")) == run_id or str(record_fields.get("最近参与运行批次", "")) == run_id
            fields = item_to_content_inbox_fields(item, run_id, is_new=same_run_new, duplicate=not same_run_new)
            update_fields = {
                "最近参与运行批次": fields["最近参与运行批次"],
                "最近采样日期": fields["最近采样日期"],
                "是否本次新增": "是" if same_run_new else "否",
                "是否重复": str(record_fields.get("是否重复", "否")) if same_run_new else "是",
            }
            update_record_fields(token, app_token, table_id, record["record_id"], update_fields)
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
            f"- 推荐：{best['我的选题标题']}",
            f"- 动作：{best['推荐动作']}",
            "- 理由：今天更适合先蹭一个有明确差异化角度的热点，做 30-60 秒短评；不要同时把多条都推进成完整 Brief。",
            "",
        ])
    for idx, topic in enumerate(topics, start=1):
        lines.extend([
            f"## {idx}. {topic['我的选题标题']}",
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


def assign_action_quotas(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    immediate = 0
    brief = 0
    weekly = 0
    for topic in topics:
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
    if sum(1 for t in topics if t["推荐动作"] == "立即蹭热点") < 3:
        for topic in topics:
            if topic["来源类型"] == "AIHOT热点" and topic["推荐动作"] in {"本周做", "暂存观察"}:
                topic["推荐动作"] = "立即蹭热点"
                if sum(1 for t in topics if t["推荐动作"] == "立即蹭热点") >= 3:
                    break
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


def topic_theme_key(topic: dict[str, Any]) -> tuple[str, str, str]:
    return (
        topic["业务场景"],
        topic["热点切入方式"],
        similar_asset_key(topic),
    )


def merge_same_theme(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for topic in candidates:
        key = topic_theme_key(topic)
        if key not in by_key:
            by_key[key] = topic
            merged.append(topic)
            continue
        kept = by_key[key]
        related = [part for part in kept.get("相关来源", "").split("；") if part]
        if topic["热点切入方式"] in {"内容团队变化", "AI导演流程", "品牌风控/信任危机"}:
            related.append(f"{topic['来源类型']}：{topic['来源内容']}")
        kept["相关来源"] = "；".join(dict.fromkeys(related))
    return merged


def select_today10(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select a diverse Top10 with soft column quotas from config/system rules."""
    sorted_candidates = merge_same_theme(sorted(candidates, key=lambda row: int(row["推荐分"]), reverse=True))
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in sorted_candidates:
        row["对应栏目"] = normalize_column(row["对应栏目"])
        buckets.setdefault(row["对应栏目"], []).append(row)

    selected: list[dict[str, Any]] = []
    seen_fp: set[str] = set()

    def add(row: dict[str, Any]) -> bool:
        if row["内容指纹"] in seen_fp or len(selected) >= 10:
            return False
        selected.append(row)
        seen_fp.add(row["内容指纹"])
        return True

    for column, (minimum, maximum) in TOP10_COLUMN_LIMITS.items():
        column_rows = buckets.get(column, [])
        for row in column_rows[:maximum]:
            if len([item for item in selected if item["对应栏目"] == column]) >= minimum:
                break
            add(row)

    for column, (_minimum, maximum) in TOP10_COLUMN_LIMITS.items():
        for row in buckets.get(column, []):
            if len([item for item in selected if item["对应栏目"] == column]) >= maximum:
                break
            add(row)

    for row in sorted_candidates:
        add(row)
        if len(selected) >= 10:
            break

    return selected[:10]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-fetch-aihot", action="store_true")
    parser.add_argument("--manual", default=str(MANUAL_ITEMS))
    parser.add_argument("--write-feishu", action="store_true", help="Write all analyzed ContentItems into Feishu 03 内容收件箱 as the content ledger.")
    parser.add_argument("--run-id", default="", help="Stable run id shared by 03 内容收件箱 and 04 分析与选题.")
    args = parser.parse_args()

    run_id = args.run_id or default_run_id()
    items, logs = collect_items(not args.no_fetch_aihot, Path(args.manual))
    item_rows = [item_row(item) for item in items]
    breakdown_rows = [breakdown(item) for item in items]
    item_by_fp = {item.fingerprint: item for item in items}
    candidates = [
        topic_from_breakdown(row, item_by_fp[row["内容指纹"]])
        for row in breakdown_rows
        if row["是否进入今日10选题"] == "是"
    ]
    today10 = select_today10(candidates)
    today10 = assign_action_quotas(today10)

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
        },
    }
    if args.write_feishu:
        run_log["feishu_content_ledger"] = write_content_ledger_to_feishu(items, run_id)
    (OUT / "content_sampler_log.json").write_text(json.dumps(run_log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(run_log, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

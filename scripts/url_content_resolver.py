#!/usr/bin/env python3
"""Resolve URL intake into standard ContentItem rows.

Supported P0 sources:
- WeChat public article URLs
- Douyin single video URLs, including search URLs with modal_id
- RSS/Atom feeds
- Public web pages via Jina Reader

Default mode is dry-run/local output only. Use --write-feishu to write new,
deduped rows to Feishu 03 内容收件箱. This script never changes table structure.
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
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import push_to_feishu as feishu
from feishu_table_registry import resolve_table_id, table_name


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
DEFAULT_OUT = OUT / "url_content_items.jsonl"
DEFAULT_CSV = OUT / "url_content_items.csv"
DEFAULT_RAW_DIR = OUT / "url_content_resolver_raw"
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)
DOUYIN_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
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
    "是否重复",
    "处理状态",
]
URL_INBOX_WRITE_FIELDS = ["处理状态", "解析结果", "失败原因"]


@dataclass
class ContentItem:
    source_type: str
    platform: str
    account_name: str
    content_title: str
    content_url: str
    content_shape: str
    cover_text: str
    body_or_transcript: str
    summary_or_description: str
    published_at: str
    comments_or_questions: str
    raw_payload_path: str
    fetch_method: str
    fetch_status: str
    failure_reason: str
    content_fingerprint: str


@dataclass
class IntakeRecord:
    record_id: str
    url: str
    status: str


class BlockTextParser(HTMLParser):
    block_tags = {"p", "div", "section", "br", "li", "h1", "h2", "h3", "h4", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data and data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        joined = "".join(self.parts)
        joined = re.sub(r"[ \t\r\f\v]+", " ", joined)
        joined = re.sub(r"\n[ \t]+", "\n", joined)
        joined = re.sub(r"\n{3,}", "\n\n", joined)
        return html.unescape(joined).strip()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def fingerprint(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_text(url: str, user_agent: str = DEFAULT_UA, timeout: int = 30) -> tuple[str, str, str]:
    req = Request(url, headers={"User-Agent": user_agent})
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace"), "ok", resp.url
    except HTTPError as exc:
        return exc.read().decode("utf-8", errors="replace"), f"http_{exc.code}", url
    except (URLError, TimeoutError) as exc:
        return "", f"failed:{exc.__class__.__name__}", url


def make_failure(url: str, source_type: str, platform: str, method: str, reason: str, raw_path: str = "") -> ContentItem:
    return ContentItem(
        source_type=source_type,
        platform=platform,
        account_name="",
        content_title="",
        content_url=url,
        content_shape="",
        cover_text="",
        body_or_transcript="",
        summary_or_description="",
        published_at="",
        comments_or_questions="",
        raw_payload_path=raw_path,
        fetch_method=method,
        fetch_status="failed",
        failure_reason=reason,
        content_fingerprint=fingerprint(url, method, reason),
    )


def js_string_var(page: str, var_name: str) -> str:
    match = re.search(rf"var\s+{re.escape(var_name)}\s*=\s*(.*?);", page, flags=re.S)
    if not match:
        return ""
    raw = match.group(1).strip()
    raw = re.sub(r"\.html\(false\)$", "", raw).strip()
    if raw.startswith("htmlDecode(") and raw.endswith(")"):
        raw = raw[len("htmlDecode("):-1].strip()
    if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
        raw = raw[1:-1]
    if "\\u" in raw or "\\x" in raw:
        raw = raw.encode("utf-8").decode("unicode_escape", errors="ignore")
    return html.unescape(raw)


def js_numeric_var(page: str, var_name: str) -> str:
    matches = re.findall(rf"var\s+{re.escape(var_name)}\s*=\s*['\"]?(\d{{9,13}})['\"]?\s*;", page)
    return matches[-1] if matches else ""


def html_to_text(fragment: str) -> str:
    parser = BlockTextParser()
    parser.feed(fragment)
    return parser.text()


def extract_wechat_content_html(page: str) -> str:
    match = re.search(r'<div[^>]+id="js_content"[^>]*>(.*?)</div>\s*<script', page, flags=re.S)
    if match:
        return match.group(1)
    match = re.search(r'<div[^>]+id="js_content"[^>]*>(.*?)</div>', page, flags=re.S)
    return match.group(1) if match else ""


def extract_headings(content_html: str) -> list[str]:
    headings: list[str] = []
    for match in re.finditer(r"<(?:h[1-4]|strong|b)[^>]*>(.*?)</(?:h[1-4]|strong|b)>", content_html, flags=re.S):
        text = html_to_text(match.group(1))
        if 2 <= len(text) <= 80 and text not in headings:
            headings.append(text)
    return headings[:12]


def extract_images(content_html: str) -> list[str]:
    return [
        html.unescape(match.group(1))
        for match in re.finditer(r'<img[^>]+(?:data-src|src)="([^"]+)"', content_html, flags=re.I)
    ][:12]


def resolve_wechat(url: str, raw_dir: Path) -> list[ContentItem]:
    page, status, final_url = fetch_text(url, DEFAULT_UA)
    raw_html = raw_dir / f"wechat_{fingerprint(url)}.html"
    write_text(raw_html, page)
    if status != "ok":
        return [make_failure(url, "公众号文章", "微信公众号", "wechat_public_html_js_content", status, str(raw_html))]
    title = js_string_var(page, "msg_title")
    account = js_string_var(page, "nickname")
    desc = js_string_var(page, "msg_desc")
    ct = js_numeric_var(page, "ct") or js_numeric_var(page, "createTimestamp")
    published_at = datetime.fromtimestamp(int(ct)).isoformat(timespec="seconds") if ct and ct.isdigit() else ""
    content_html = extract_wechat_content_html(page)
    if not content_html:
        return [make_failure(url, "公众号文章", "微信公众号", "wechat_public_html_js_content", "页面中没有找到 js_content 正文。", str(raw_html))]
    body = html_to_text(content_html)
    if len(body) < 200:
        return [make_failure(url, "公众号文章", "微信公众号", "wechat_public_html_js_content", "正文过短，疑似验证页或未获取到文章主体。", str(raw_html))]
    headings = extract_headings(content_html)
    images = extract_images(content_html)
    raw_meta = raw_dir / f"wechat_{fingerprint(url)}.json"
    write_json(raw_meta, {
        "final_url": final_url,
        "title": title,
        "account_name": account,
        "description": desc,
        "published_at": published_at,
        "headings": headings,
        "image_refs": images,
    })
    markdown = f"# {title}\n\n公众号：{account}\n\n摘要：{desc}\n\n" + body
    raw_md = raw_dir / f"wechat_{fingerprint(url)}.md"
    write_text(raw_md, markdown)
    summary = "；".join(part for part in [desc, f"结构线索：{' / '.join(headings[:8])}" if headings else ""] if part)
    return [ContentItem(
        source_type="公众号文章",
        platform="微信公众号",
        account_name=account,
        content_title=title,
        content_url=url,
        content_shape="长文",
        cover_text="",
        body_or_transcript=markdown[:20000],
        summary_or_description=summary or body[:500],
        published_at=published_at,
        comments_or_questions="",
        raw_payload_path=str(raw_md),
        fetch_method="wechat_public_html_js_content",
        fetch_status="success",
        failure_reason="",
        content_fingerprint=fingerprint("wechat", url, title),
    )]


def douyin_video_id(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    if params.get("modal_id"):
        return params["modal_id"][0]
    match = re.search(r"/video/(\d+)", url)
    if match:
        return match.group(1)
    match = re.search(r"modal_id=(\d+)", url)
    return match.group(1) if match else ""


def first_url(value: Any) -> str:
    if isinstance(value, dict):
        urls = value.get("url_list")
        if isinstance(urls, list) and urls:
            return str(urls[0])
    if isinstance(value, list) and value:
        return str(value[0])
    return str(value or "")


def resolve_douyin(url: str, raw_dir: Path) -> list[ContentItem]:
    video_id = douyin_video_id(url)
    fetch_url = f"https://www.iesdouyin.com/share/video/{video_id}" if video_id else url
    page, status, final_url = fetch_text(fetch_url, DOUYIN_UA)
    raw_html = raw_dir / f"douyin_{fingerprint(url)}.html"
    write_text(raw_html, page)
    if status != "ok":
        return [make_failure(url, "对标视频", "抖音", "douyin_public_router_data", status, str(raw_html))]
    match = re.search(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", page, flags=re.S)
    if not match:
        return [make_failure(url, "对标视频", "抖音", "douyin_public_router_data", "页面中没有找到 window._ROUTER_DATA。", str(raw_html))]
    try:
        router = json.loads(match.group(1).strip())
        loader = router.get("loaderData", {})
        video_info = None
        for key in ("video_(id)/page", "note_(id)/page"):
            if isinstance(loader.get(key), dict):
                video_info = loader[key].get("videoInfoRes")
                break
        if not video_info:
            raise ValueError("ROUTER_DATA 中没有 videoInfoRes。")
        item = video_info["item_list"][0]
    except Exception as exc:
        return [make_failure(url, "对标视频", "抖音", "douyin_public_router_data", str(exc), str(raw_html))]
    raw_json = raw_dir / f"douyin_{fingerprint(url)}.json"
    write_json(raw_json, router)
    title = str(item.get("desc", "")).strip()
    author = item.get("author") or {}
    video = item.get("video") or {}
    play_url = first_url((video.get("play_addr") or {}).get("url_list", "")).replace("playwm", "play")
    cover_url = first_url((video.get("cover") or {}).get("url_list", ""))
    hashtags = [
        "#" + str(extra["hashtag_name"])
        for extra in (item.get("text_extra") or [])
        if isinstance(extra, dict) and extra.get("hashtag_name")
    ]
    published_at = datetime.fromtimestamp(int(item["create_time"])).isoformat(timespec="seconds") if item.get("create_time") else ""
    summary = "；".join(part for part in [
        title,
        f"作者签名：{author.get('signature', '')}" if author.get("signature") else "",
        f"标签：{' '.join(hashtags)}" if hashtags else "",
        f"封面：{cover_url}" if cover_url else "",
        f"下载链接：{play_url}" if play_url else "",
    ] if part)
    return [ContentItem(
        source_type="对标视频",
        platform="抖音",
        account_name=str(author.get("nickname", "")),
        content_title=title,
        content_url=url,
        content_shape="短视频",
        cover_text=title,
        body_or_transcript=title,
        summary_or_description=summary,
        published_at=published_at,
        comments_or_questions="",
        raw_payload_path=str(raw_json),
        fetch_method="douyin_public_router_data",
        fetch_status="success",
        failure_reason="",
        content_fingerprint=fingerprint("douyin", url, title),
    )]


def ns_free(tag: str) -> str:
    return tag.split("}", 1)[-1].lower()


def child_text(node: ET.Element, names: set[str]) -> str:
    for child in list(node):
        if ns_free(child.tag) in names:
            return (child.text or "").strip()
    return ""


def child_link(node: ET.Element) -> str:
    for child in list(node):
        if ns_free(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href.strip()
        if child.text:
            return child.text.strip()
    return ""


def resolve_feed(url: str, raw_dir: Path, max_items: int) -> list[ContentItem]:
    text, status, final_url = fetch_text(url, DEFAULT_UA)
    raw_xml = raw_dir / f"feed_{fingerprint(url)}.xml"
    write_text(raw_xml, text)
    if status != "ok":
        return [make_failure(url, "RSS/Atom", "RSS/Atom", "rss_atom_xml", status, str(raw_xml))]
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return [make_failure(url, "RSS/Atom", "RSS/Atom", "rss_atom_xml", f"XML解析失败：{exc}", str(raw_xml))]
    entries = [node for node in root.iter() if ns_free(node.tag) in {"item", "entry"}]
    if not entries:
        return [make_failure(url, "RSS/Atom", "RSS/Atom", "rss_atom_xml", "未找到 RSS item 或 Atom entry。", str(raw_xml))]
    items: list[ContentItem] = []
    for entry in entries[:max_items]:
        title = child_text(entry, {"title"}) or "未命名RSS条目"
        link = child_link(entry) or url
        summary = child_text(entry, {"description", "summary", "content", "encoded"})
        published = child_text(entry, {"published", "updated", "pubdate"})
        items.append(ContentItem(
            source_type="RSS/Atom",
            platform="RSS/Atom",
            account_name="",
            content_title=title,
            content_url=link,
            content_shape="feed_entry",
            cover_text="",
            body_or_transcript=summary,
            summary_or_description=summary[:1000],
            published_at=published,
            comments_or_questions="",
            raw_payload_path=str(raw_xml),
            fetch_method="rss_atom_xml",
            fetch_status="success",
            failure_reason="",
            content_fingerprint=fingerprint("rss", link, title),
        ))
    return items


def title_from_markdown(text: str) -> str:
    for line in text.splitlines():
        clean = line.strip()
        if clean.startswith("Title:"):
            return clean.replace("Title:", "", 1).strip()
        if clean.startswith("#"):
            return clean.lstrip("#").strip()
    return "未命名网页"


def resolve_web(url: str, raw_dir: Path) -> list[ContentItem]:
    jina_url = f"https://r.jina.ai/{url}"
    text, status, final_url = fetch_text(jina_url, DEFAULT_UA)
    raw_md = raw_dir / f"web_{fingerprint(url)}.md"
    write_text(raw_md, text)
    if status != "ok":
        return [make_failure(url, "公开网页", "Web", "jina_reader", status, str(raw_md))]
    if len(text.strip()) < 200:
        return [make_failure(url, "公开网页", "Web", "jina_reader", "返回正文过短。", str(raw_md))]
    title = title_from_markdown(text)
    return [ContentItem(
        source_type="公开网页",
        platform="Web",
        account_name="",
        content_title=title,
        content_url=url,
        content_shape="web_article",
        cover_text="",
        body_or_transcript=text[:20000],
        summary_or_description=" ".join(text.split())[:1000],
        published_at="",
        comments_or_questions="",
        raw_payload_path=str(raw_md),
        fetch_method="jina_reader",
        fetch_status="success",
        failure_reason="",
        content_fingerprint=fingerprint("web", url, title),
    )]


def detect_url_type(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    path = urllib.parse.urlparse(url).path.lower()
    if "mp.weixin.qq.com" in host:
        return "wechat"
    if "douyin.com" in host or "iesdouyin.com" in host:
        return "douyin"
    if any(part in path for part in ["/feed", "/rss", ".xml", ".atom"]) or path.endswith("/atom"):
        return "feed"
    return "web"


def resolve_url(url: str, raw_dir: Path, max_feed_items: int) -> list[ContentItem]:
    kind = detect_url_type(url)
    if kind == "wechat":
        return resolve_wechat(url, raw_dir)
    if kind == "douyin":
        return resolve_douyin(url, raw_dir)
    if kind == "feed":
        return resolve_feed(url, raw_dir, max_feed_items)
    return resolve_web(url, raw_dir)


def normalize_url(value: Any) -> str:
    text = str(value or "").strip()
    match = re.search(r"https?://\S+", text)
    return match.group(0).rstrip("，,。)") if match else ""


def read_file_urls(path: Path) -> list[str]:
    return [normalize_url(line) for line in path.read_text(encoding="utf-8").splitlines() if normalize_url(line)]


def require_feishu_env() -> str:
    app_token = os.getenv("FEISHU_BASE_APP_TOKEN")
    missing = [name for name in ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_BASE_APP_TOKEN"] if not os.getenv(name)]
    if missing:
        raise SystemExit(f"Feishu access requires environment variables: {', '.join(missing)}")
    return str(app_token)


def table_map(token: str, app_token: str) -> dict[str, str]:
    return {table["name"]: table["table_id"] for table in feishu.list_tables(token, app_token)}


def fields_by_name(token: str, app_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token)
    return {field["field_name"]: field for field in payload.get("data", {}).get("items", [])}


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


def read_feishu_intake_records(token: str, app_token: str) -> tuple[str, list[IntakeRecord]]:
    tables = table_map(token, app_token)
    table_id = resolve_table_id(tables, "url_inbox")
    if not table_id:
        raise SystemExit(f"Missing Feishu table: {table_name('url_inbox')}")
    rows = all_records(token, app_token, table_id)
    records: list[IntakeRecord] = []
    seen_urls: set[str] = set()
    for record in rows:
        fields = record.get("fields", {})
        status = str(fields.get("处理状态", ""))
        if status in {"已解析", "解析失败", "重复", "已存在", "已处理", "跳过"}:
            continue
        url = normalize_url(fields.get("URL", ""))
        if url and url not in seen_urls:
            records.append(IntakeRecord(record_id=record.get("record_id", ""), url=url, status=status))
            seen_urls.add(url)
    return table_id, records


def item_to_manual_row(item: ContentItem) -> dict[str, str]:
    return {
        "来源类型": item.source_type,
        "平台": item.platform,
        "账号名/公众号名": item.account_name,
        "内容标题": item.content_title,
        "内容链接": item.content_url,
        "内容形态": item.content_shape,
        "封面文字": item.cover_text,
        "正文/字幕/简介片段": item.body_or_transcript,
        "发布时间": item.published_at,
        "评论区问题": item.comments_or_questions,
        "截图/OCR文本": item.raw_payload_path,
        "抓取方式": item.fetch_method,
        "抓取状态": item.fetch_status,
        "失败原因": item.failure_reason,
        "内容指纹": item.content_fingerprint,
    }


def item_to_feishu_fields(item: ContentItem, duplicate: bool = False) -> dict[str, str]:
    failed = item.fetch_status != "success"
    return {
        "标题": item.content_title or item.content_url,
        "来源类型": item.source_type,
        "来源名称": item.account_name or item.platform,
        "平台": item.platform,
        "链接": item.content_url,
        "发布时间": item.published_at,
        "采集时间": now_iso(),
        "采集状态": item.fetch_status,
        "失败原因": item.failure_reason,
        "摘要/片段": item.summary_or_description or item.body_or_transcript[:1000],
        "作者/账号": item.account_name,
        "内容指纹": item.content_fingerprint,
        "是否重复": "是" if duplicate else "否",
        "处理状态": "重复" if duplicate else ("跳过" if failed else "待分析"),
    }


def write_local_outputs(items: list[ContentItem], out_jsonl: Path, out_csv: Path) -> None:
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    rows = [asdict(item) for item in items]
    out_jsonl.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")
    with out_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else [field.name for field in ContentItem.__dataclass_fields__.values()])
        writer.writeheader()
        writer.writerows(rows)
    manual_path = out_jsonl.with_name(out_jsonl.stem + "_manual.jsonl")
    manual_rows = [item_to_manual_row(item) for item in items]
    manual_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in manual_rows) + ("\n" if manual_rows else ""), encoding="utf-8")


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


def update_url_intake_records(
    token: str,
    app_token: str,
    table_id: str,
    records: list[IntakeRecord],
    outcomes_by_url: dict[str, dict[str, str]],
) -> dict[str, Any]:
    existing_fields = fields_by_name(token, app_token, table_id)
    writable_fields = [field for field in URL_INBOX_WRITE_FIELDS if field in existing_fields]
    if not writable_fields:
        return {"updated_records": 0, "skipped": len(records), "reason": "02 URL投喂入口 缺少可回写字段：处理状态/解析结果/失败原因"}
    updated = 0
    skipped = 0
    for record in records:
        outcome = outcomes_by_url.get(record.url)
        if not outcome:
            skipped += 1
            continue
        fields = {field: outcome.get(field, "") for field in writable_fields if outcome.get(field, "")}
        if not fields:
            skipped += 1
            continue
        feishu.request_json(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record.record_id}",
            token=token,
            body={"fields": fields},
        )
        updated += 1
        time.sleep(0.1)
    return {"updated_records": updated, "skipped": skipped, "fields": writable_fields}


def summarize_url_items(items: list[ContentItem], created: int, duplicates: int) -> str:
    titles = [item.content_title for item in items if item.content_title][:3]
    fingerprints = [item.content_fingerprint for item in items if item.content_fingerprint][:5]
    parts = [
        f"解析{len(items)}条",
        f"新写入{created}条",
        f"重复{duplicates}条",
    ]
    if titles:
        parts.append("标题：" + " / ".join(titles))
    if fingerprints:
        parts.append("指纹：" + " / ".join(fingerprints))
    parts.append(f"解析时间：{now_iso()}")
    return "；".join(parts)[:1800]


def write_feishu_content_inbox(
    token: str,
    app_token: str,
    items: list[ContentItem],
    intake_records: list[IntakeRecord] | None = None,
    url_inbox_table_id: str = "",
) -> dict[str, Any]:
    tables = table_map(token, app_token)
    table_id = resolve_table_id(tables, "content_inbox")
    if not table_id:
        raise SystemExit(f"Missing Feishu table: {table_name('content_inbox')}")
    existing = all_records(token, app_token, table_id)
    existing_fp = {str(record.get("fields", {}).get("内容指纹", "")) for record in existing}
    existing_urls = {str(record.get("fields", {}).get("链接", "")) for record in existing}
    rows: list[dict[str, str]] = []
    duplicates = 0
    per_url: dict[str, dict[str, Any]] = {}
    for item in items:
        bucket = per_url.setdefault(item.content_url, {"items": [], "created": 0, "duplicates": 0, "failed": 0, "failure_reasons": []})
        bucket["items"].append(item)
        if item.fetch_status != "success":
            bucket["failed"] += 1
            if item.failure_reason:
                bucket["failure_reasons"].append(item.failure_reason)
            continue
        if item.content_fingerprint in existing_fp or item.content_url in existing_urls:
            duplicates += 1
            bucket["duplicates"] += 1
            continue
        rows.append(item_to_feishu_fields(item))
        bucket["created"] += 1
        existing_fp.add(item.content_fingerprint)
        existing_urls.add(item.content_url)
    created = batch_create_records(token, app_token, table_id, rows) if rows else 0
    outcomes_by_url: dict[str, dict[str, str]] = {}
    for url, bucket in per_url.items():
        url_items = bucket["items"]
        if bucket["created"]:
            status = "已解析"
            failure = ""
        elif bucket["duplicates"] and not bucket["created"]:
            status = "重复"
            failure = ""
        elif bucket["failed"] and not bucket["created"]:
            status = "解析失败"
            failure = "；".join(bucket["failure_reasons"])[:1800]
        else:
            status = "已解析"
            failure = ""
        outcomes_by_url[url] = {
            "处理状态": status,
            "解析结果": summarize_url_items(url_items, int(bucket["created"]), int(bucket["duplicates"])),
            "失败原因": failure,
        }
    intake_update: dict[str, Any] = {}
    if intake_records and url_inbox_table_id:
        intake_update = update_url_intake_records(token, app_token, url_inbox_table_id, intake_records, outcomes_by_url)
    return {
        "table": table_name("content_inbox"),
        "created_records": created,
        "skipped_duplicates": duplicates,
        "url_outcomes": outcomes_by_url,
        "intake_update": intake_update,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve URL intake into ContentItem rows.")
    parser.add_argument("--file", help="Local text file with one URL per line.")
    parser.add_argument("--url", action="append", default=[], help="URL to resolve; can be repeated.")
    parser.add_argument("--feishu-intake", action="store_true", help="Read URLs from Feishu 02 URL投喂入口.")
    parser.add_argument("--write-feishu", action="store_true", help="Write deduped rows to Feishu 03 内容收件箱. Default is dry-run.")
    parser.add_argument("--dry-run", action="store_true", help="Dry-run alias for clarity; dry-run is the default.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output JSONL path.")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help="Output CSV path.")
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR), help="Raw payload output directory.")
    parser.add_argument("--max-feed-items", type=int, default=5, help="Max entries to emit for one RSS/Atom feed.")
    args = parser.parse_args()

    urls: list[str] = []
    intake_records: list[IntakeRecord] = []
    url_inbox_table_id = ""
    token = ""
    app_token = ""
    if args.file:
        urls.extend(read_file_urls(Path(args.file)))
    urls.extend(normalize_url(url) for url in args.url if normalize_url(url))
    if args.feishu_intake:
        app_token = require_feishu_env()
        token = feishu.tenant_token()
        url_inbox_table_id, intake_records = read_feishu_intake_records(token, app_token)
        urls.extend(record.url for record in intake_records)
    urls = list(dict.fromkeys(urls))
    if not urls:
        raise SystemExit("No URLs provided. Use --file, --url, or --feishu-intake.")

    raw_dir = Path(args.raw_dir)
    items: list[ContentItem] = []
    for url in urls:
        items.extend(resolve_url(url, raw_dir, args.max_feed_items))
    seen: set[str] = set()
    deduped: list[ContentItem] = []
    local_duplicates = 0
    for item in items:
        key = item.content_fingerprint or item.content_url
        if key in seen:
            local_duplicates += 1
            continue
        seen.add(key)
        deduped.append(item)

    out_jsonl = Path(args.out)
    out_csv = Path(args.csv)
    write_local_outputs(deduped, out_jsonl, out_csv)
    summary: dict[str, Any] = {
        "ok": True,
        "mode": "write-feishu" if args.write_feishu else "dry-run",
        "urls": len(urls),
        "items": len(deduped),
        "local_duplicates": local_duplicates,
        "output": str(out_jsonl),
        "csv": str(out_csv),
        "manual_jsonl": str(out_jsonl.with_name(out_jsonl.stem + "_manual.jsonl")),
        "by_status": {},
    }
    for item in deduped:
        summary["by_status"][item.fetch_status] = summary["by_status"].get(item.fetch_status, 0) + 1
    if args.write_feishu:
        app_token = app_token or require_feishu_env()
        token = token or feishu.tenant_token()
        summary["feishu"] = write_feishu_content_inbox(token, app_token, deduped, intake_records, url_inbox_table_id)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for item in deduped:
        print(f"- {item.platform}: {item.fetch_status} | {item.account_name} | {item.content_title or item.failure_reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

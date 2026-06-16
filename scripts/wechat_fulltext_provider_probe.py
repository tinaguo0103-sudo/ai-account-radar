#!/usr/bin/env python3
"""Probe a local WeChat full-text provider without writing Feishu.

The provider can be we-mp-rss, wewe-rss, or any compatible RSS/Atom/JSON feed.
This script only reads a configured local feed/API and converts items into the
same manual ContentItem JSONL shape used by content_sampler. It does not log in,
does not store cookies, and does not write 03/04.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "wechat_fulltext_provider.example.yaml"
DEFAULT_OUT = ROOT / "output" / "wechat_fulltext_provider_items.jsonl"
DEFAULT_CSV = ROOT / "output" / "wechat_fulltext_provider_items.csv"
DEFAULT_RAW_DIR = ROOT / "output" / "wechat_fulltext_provider_raw"
MAX_FEED_BYTES = 50_000_000
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 AIAccountRadar/0.1"


@dataclass
class Provider:
    provider_id: str
    provider: str
    name: str
    source_name: str
    platform: str
    source_type: str
    base_url: str = ""
    feed_path: str = ""
    mode: str = "fulltext"
    default_enabled: bool = False
    source_id: str = ""
    topic_bucket: str = ""
    max_items: int = 5
    note: str = ""

    @property
    def provider_name(self) -> str:
        return self.provider

    @property
    def account_name(self) -> str:
        return self.source_name

    @property
    def feed_url(self) -> str:
        base = self.base_url.rstrip("/")
        path = self.feed_path if self.feed_path.startswith("/") else f"/{self.feed_path}"
        url = f"{base}{path}"
        query = {"limit": str(self.max_items)}
        if self.mode:
            query["mode"] = self.mode
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}{urllib.parse.urlencode(query)}"


class TextExtractor(HTMLParser):
    block_tags = {"p", "div", "section", "br", "li", "h1", "h2", "h3", "h4", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.in_skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self.in_skip = True
        if tag in self.block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self.in_skip = False
        if tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.in_skip and data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return html.unescape(text).strip()


def parse_scalar(value: str) -> Any:
    cleaned = value.strip().strip('"').strip("'")
    if cleaned.lower() == "true":
        return True
    if cleaned.lower() == "false":
        return False
    if re.fullmatch(r"\d+", cleaned):
        return int(cleaned)
    return cleaned


def load_yaml_list(path: Path, section_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    section = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        stripped = raw_line.strip()
        if not raw_line.startswith(" ") and stripped.endswith(":"):
            if current and section == section_name:
                rows.append(current)
            current = None
            section = stripped[:-1]
            continue
        if section != section_name:
            continue
        if stripped.startswith("- "):
            if current:
                rows.append(current)
            current = {}
            stripped = stripped[2:].strip()
            if ":" in stripped:
                key, value = stripped.split(":", 1)
                current[key.strip()] = parse_scalar(value)
            continue
        if current is not None and ":" in stripped:
            key, value = stripped.split(":", 1)
            current[key.strip()] = parse_scalar(value)
    if current and section == section_name:
        rows.append(current)
    return rows


def normalize_provider_row(row: dict[str, Any]) -> dict[str, Any]:
    if "provider_name" in row or "feed_url" in row:
        feed_url = str(row.get("feed_url", ""))
        parsed = urllib.parse.urlparse(feed_url)
        return {
            "provider_id": str(row.get("provider_id", "")),
            "provider": str(row.get("provider_name", row.get("provider", ""))).replace(" local", ""),
            "name": str(row.get("provider_name", row.get("name", ""))),
            "base_url": f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "",
            "feed_path": parsed.path or feed_url,
            "mode": urllib.parse.parse_qs(parsed.query).get("mode", ["fulltext"])[0],
            "default_enabled": bool(row.get("default_enabled", False)),
            "source_id": str(row.get("source_id", row.get("provider_id", ""))),
            "source_name": str(row.get("account_name", row.get("source_name", ""))),
            "platform": str(row.get("platform", "微信公众号")),
            "source_type": str(row.get("source_type", "公众号文章")),
            "topic_bucket": str(row.get("topic_bucket", "")),
            "max_items": int(row.get("max_items", 5) or 5),
            "note": str(row.get("note", "")),
        }
    return row


def load_providers(path: Path) -> list[Provider]:
    return [Provider(**normalize_provider_row(row)) for row in load_yaml_list(path, "providers")]


def fetch(url: str) -> tuple[bytes, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read(MAX_FEED_BYTES), "ok", resp.headers.get("Content-Type", "")
    except Exception as exc:
        return b"", f"failed:{type(exc).__name__}:{exc}", ""


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def ns_free(tag: str) -> str:
    return tag.split("}", 1)[-1].lower()


def child_text(node: ET.Element, names: set[str]) -> str:
    for child in list(node):
        if ns_free(child.tag) in names:
            return (child.text or "").strip()
    for child in node.iter():
        if ns_free(child.tag) in names and child.text:
            return child.text.strip()
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


def html_to_text(fragment: str) -> str:
    parser = TextExtractor()
    parser.feed(fragment or "")
    return parser.text()


def fingerprint(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def raw_payload_path(provider: Provider, url: str, title: str, suffix: str = "html") -> Path:
    safe_id = fingerprint(provider.provider_id, url, title)
    return DEFAULT_RAW_DIR / f"{provider.provider_id}_{safe_id}.{suffix}"


def parse_xml_items(raw: bytes, provider: Provider) -> list[dict[str, str]]:
    root = ET.fromstring(raw)
    entries = [node for node in root.iter() if ns_free(node.tag) in {"item", "entry"}]
    rows: list[dict[str, str]] = []
    for entry in entries[: provider.max_items]:
        title = child_text(entry, {"title"}) or "未命名公众号文章"
        url = child_link(entry)
        content = child_text(entry, {"encoded", "content", "summary", "description"})
        text = html_to_text(content)
        published = child_text(entry, {"pubdate", "published", "updated", "date"})
        rows.append({
            "title": title,
            "url": url,
            "published_at": published,
            "body": text or content,
            "raw_body": content,
            "raw_payload_path": str(raw_payload_path(provider, url, title, "html")),
        })
    return rows


def parse_json_items(raw: bytes, provider: Provider) -> list[dict[str, str]]:
    data = json.loads(raw.decode("utf-8"))
    if isinstance(data, dict):
        candidates = data.get("items") or data.get("data") or data.get("list") or []
    else:
        candidates = data
    rows: list[dict[str, str]] = []
    for item in candidates[: provider.max_items]:
        if not isinstance(item, dict):
            continue
        body = str(
            item.get("content_html")
            or item.get("content_text")
            or item.get("content")
            or item.get("body")
            or item.get("description")
            or item.get("summary")
            or ""
        )
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        rows.append({
            "title": str(item.get("title") or item.get("name") or "未命名公众号文章"),
            "url": str(item.get("url") or item.get("link") or item.get("guid") or ""),
            "published_at": str(
                item.get("published_at")
                or item.get("pubDate")
                or item.get("date")
                or item.get("updated")
                or item.get("date_modified")
                or item.get("date_published")
                or ""
            ),
            "body": html_to_text(body),
            "raw_body": body,
            "author": str(author.get("name") or ""),
            "raw_payload_path": str(raw_payload_path(provider, str(item.get("url") or item.get("link") or item.get("guid") or ""), str(item.get("title") or item.get("name") or ""), "html")),
        })
    return rows


def to_manual_row(provider: Provider, item: dict[str, str], status: str, failure: str) -> dict[str, str]:
    body = item.get("body", "")
    title = item.get("title", "")
    url = item.get("url", "")
    is_full = "是" if len(body) >= 800 else "否"
    raw_payload = item.get("raw_payload_path", "")
    if raw_payload and item.get("raw_body"):
        write_text(Path(raw_payload), item.get("raw_body", ""))
    parse_note = (
        f"provider={provider.provider}; source_id={provider.source_id}; "
        f"feed_url_or_api_url={provider.feed_url}; "
        f"topic_bucket={provider.topic_bucket}; "
        f"{'已解析全文' if is_full == '是' else '未达到全文阈值'}"
    )
    return {
        "来源类型": provider.source_type,
        "平台": provider.platform,
        "账号名/公众号名": provider.source_name,
        "内容标题": title,
        "内容链接": url,
        "内容形态": "长文",
        "封面文字": "",
        "正文/字幕/简介片段": body,
        "发布时间": item.get("published_at", ""),
        "评论区问题": "",
        "截图/OCR文本": raw_payload,
        "抓取方式": "wechat_feed",
        "抓取状态": status,
        "失败原因": failure,
        "内容指纹": fingerprint("wechat", url, title),
        "正文原始长度": str(len(body)),
        "正文是否截断": "否",
        "是否来自已解析URL复用": "否",
        "是否全文解析": is_full,
        "解析说明": parse_note,
        "provider": provider.provider,
        "source_id": provider.source_id,
        "feed_url_or_api_url": provider.feed_url,
    }


def probe_provider(provider: Provider) -> tuple[list[dict[str, str]], str, str]:
    raw, status, content_type = fetch(provider.feed_url)
    if status != "ok":
        return [], status, content_type
    try:
        if "json" in content_type or provider.feed_url.endswith(".json"):
            items = parse_json_items(raw, provider)
        else:
            items = parse_xml_items(raw, provider)
        return items, "ok", content_type
    except Exception as exc:
        return [], f"parse_failed:{type(exc).__name__}:{exc}", content_type


def write_outputs(rows: list[dict[str, str]], out_jsonl: Path, out_csv: Path) -> None:
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    out_jsonl.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")
    with out_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = list(rows[0].keys()) if rows else ["内容标题", "内容链接", "抓取状态", "失败原因"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe local WeChat full-text provider into ContentItem-compatible JSONL.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--provider-id", default="")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--csv", default=str(DEFAULT_CSV))
    parser.add_argument("--dry-run", action="store_true", help="Dry-run alias; this script never writes Feishu.")
    args = parser.parse_args()

    providers = load_providers(Path(args.config))
    if args.provider_id:
        providers = [
            provider for provider in providers
            if provider.provider_id == args.provider_id or provider.provider == args.provider_id
        ]
    rows: list[dict[str, str]] = []
    report: list[dict[str, Any]] = []
    for provider in providers:
        if args.limit is not None:
            provider.max_items = args.limit
        items, status, content_type = probe_provider(provider)
        if not items:
            rows.append(to_manual_row(provider, {}, "failed", status))
        else:
            for item in items:
                failure = "" if len(item.get("body", "")) >= 800 else "provider返回内容不足800字，暂不视为稳定全文。"
                rows.append(to_manual_row(provider, item, "success", failure))
        report.append({
            "provider_id": provider.provider_id,
            "status": status,
            "content_type": content_type,
            "items": len(items),
            "fulltext_items": sum(1 for item in items if len(item.get("body", "")) >= 800),
        })
    write_outputs(rows, Path(args.out), Path(args.csv))
    print(json.dumps({"ok": True, "providers": report, "output": args.out, "csv": args.csv}, ensure_ascii=False, indent=2))
    for row in rows:
        print(f"- {row.get('抓取状态')} | 全文={row.get('是否全文解析')} | 长度={row.get('正文原始长度')} | {row.get('内容标题') or row.get('失败原因')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

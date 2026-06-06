#!/usr/bin/env python3
"""Explicit WeChat feed intake for P1 source-watch validation.

This script is intentionally opt-in. It reads configured WeChat RSS/Atom feeds,
emits standard ContentItem rows, and tries to resolve each article URL with the
existing WeChat full-text resolver. It never writes Feishu by itself.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import url_content_resolver as resolver


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "wechat_feed_candidates.yaml"
DEFAULT_OUT = ROOT / "output" / "wechat_feed_content_items.jsonl"
DEFAULT_CSV = ROOT / "output" / "wechat_feed_content_items.csv"
DEFAULT_RAW_DIR = ROOT / "output" / "wechat_feed_raw"


@dataclass
class WechatFeedSource:
    source_id: str
    name: str
    platform: str
    source_type: str
    feed_url: str
    fetch_mode: str = "rss"
    default_enabled: bool = False
    topic_bucket: str = ""
    matrix_id: str = ""
    parse_fulltext: bool = True
    max_items: int = 5
    note: str = ""


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
    """Parse simple top-level YAML lists without adding a runtime dependency."""
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


def load_sources(path: Path) -> list[WechatFeedSource]:
    sources: list[WechatFeedSource] = []
    for row in load_yaml_list(path, "sources"):
        if not row.get("feed_url"):
            continue
        sources.append(WechatFeedSource(**row))
    return sources


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


def feed_entries(source: WechatFeedSource, raw_dir: Path, limit: int) -> tuple[list[dict[str, str]], str]:
    text, status, final_url = resolver.fetch_text(source.feed_url, resolver.DEFAULT_UA)
    raw_path = raw_dir / f"feed_{source.source_id}_{resolver.fingerprint(source.feed_url)}.xml"
    resolver.write_text(raw_path, text)
    if status != "ok":
        return [], f"{source.name} feed请求失败：{status}；payload={raw_path}"
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return [], f"{source.name} feed XML解析失败：{exc}；payload={raw_path}"
    entries = [node for node in root.iter() if ns_free(node.tag) in {"item", "entry"}]
    if not entries:
        return [], f"{source.name} feed未找到 item/entry；payload={raw_path}"
    rows: list[dict[str, str]] = []
    for entry in entries[:limit]:
        title = child_text(entry, {"title"})
        url = child_link(entry)
        summary = child_text(entry, {"description", "summary", "content", "encoded"})
        published = child_text(entry, {"pubdate", "published", "updated", "date"})
        rows.append({
            "title": title,
            "url": url or final_url,
            "summary": summary,
            "published_at": published,
            "raw_feed_path": str(raw_path),
        })
    return rows, ""


def fallback_item(source: WechatFeedSource, entry: dict[str, str], reason: str) -> resolver.ContentItem:
    body = entry.get("summary", "")
    title = entry.get("title", "")
    url = entry.get("url", "")
    return resolver.ContentItem(
        source_type=source.source_type,
        platform=source.platform,
        account_name=source.name,
        content_title=title or url,
        content_url=url,
        content_shape="长文",
        cover_text="",
        body_or_transcript=body,
        summary_or_description=body[:1000],
        published_at=entry.get("published_at", ""),
        comments_or_questions="",
        raw_payload_path=entry.get("raw_feed_path", ""),
        fetch_method="wechat_feed",
        fetch_status="success" if body else "failed",
        failure_reason=reason,
        content_fingerprint=resolver.fingerprint("wechat", url, title),
        raw_text_length=len(body),
        body_truncated="否",
    )


def resolve_entry(source: WechatFeedSource, entry: dict[str, str], raw_dir: Path) -> resolver.ContentItem:
    url = entry.get("url", "")
    if not source.parse_fulltext or "mp.weixin.qq.com" not in url:
        return fallback_item(source, entry, "feed条目不是公众号文章链接，未做全文解析。")
    resolved = resolver.resolve_wechat(url, raw_dir)
    success = next((item for item in resolved if item.fetch_status == "success"), None)
    if success:
        success.account_name = success.account_name or source.name
        success.source_type = source.source_type
        success.platform = source.platform
        success.content_shape = "长文"
        success.fetch_method = "wechat_feed"
        success.summary_or_description = success.summary_or_description or entry.get("summary", "")
        success.published_at = success.published_at or entry.get("published_at", "")
        # Keep the same fingerprint as single-URL WeChat resolving, so cross-source
        # dedupe works when the same article was already pasted into 02 URL投喂入口.
        success.content_fingerprint = resolver.fingerprint("wechat", url, success.content_title or entry.get("title", ""))
        return success
    failure_reason = resolved[0].failure_reason if resolved else "未知全文解析失败"
    item = fallback_item(source, entry, f"全文解析失败：{failure_reason}")
    item.fetch_status = "success" if item.body_or_transcript else "failed"
    return item


def item_to_manual_row(item: resolver.ContentItem) -> dict[str, str]:
    row = resolver.item_to_manual_row(item)
    row["抓取方式"] = "wechat_feed"
    return row


def write_outputs(items: list[resolver.ContentItem], out_jsonl: Path, out_csv: Path) -> Path:
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    rows = [asdict(item) for item in items]
    out_jsonl.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")
    with out_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else list(resolver.ContentItem.__dataclass_fields__.keys()))
        writer.writeheader()
        writer.writerows(rows)
    manual_path = out_jsonl.with_name(out_jsonl.stem + "_manual.jsonl")
    manual_rows = [item_to_manual_row(item) for item in items]
    manual_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in manual_rows) + ("\n" if manual_rows else ""), encoding="utf-8")
    return manual_path


def collect(config: Path, limit: int | None, source_id: str = "") -> tuple[list[resolver.ContentItem], list[str], dict[str, Any]]:
    raw_dir = DEFAULT_RAW_DIR
    raw_dir.mkdir(parents=True, exist_ok=True)
    sources = load_sources(config)
    if source_id:
        sources = [source for source in sources if source.source_id == source_id]
    items: list[resolver.ContentItem] = []
    logs: list[str] = []
    feed_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        max_items = limit if limit is not None else int(source.max_items or 5)
        entries, failure = feed_entries(source, raw_dir, max_items)
        if failure:
            logs.append(failure)
            continue
        for entry in entries:
            item = resolve_entry(source, entry, raw_dir)
            key = item.content_fingerprint or item.content_url
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
            feed_rows.append({
                "source_id": source.source_id,
                "title": item.content_title or entry.get("title", ""),
                "url": item.content_url or entry.get("url", ""),
                "published_at": item.published_at or entry.get("published_at", ""),
                "fetch_status": item.fetch_status,
                "fulltext": "是" if item.raw_text_length > 500 and item.fetch_status == "success" else "否",
                "raw_text_length": item.raw_text_length,
                "failure_reason": item.failure_reason,
            })
            time.sleep(0.2)
    return items, logs, {"sources": [asdict(source) for source in sources], "feed_rows": feed_rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="Explicitly fetch configured WeChat feeds into local ContentItem rows.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--source-id", default="")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--csv", default=str(DEFAULT_CSV))
    parser.add_argument("--dry-run", action="store_true", help="Dry-run alias; this script never writes Feishu.")
    args = parser.parse_args()

    items, logs, meta = collect(Path(args.config), args.limit, args.source_id)
    out_jsonl = Path(args.out)
    manual_path = write_outputs(items, out_jsonl, Path(args.csv))
    summary = {
        "ok": True,
        "mode": "dry-run",
        "items": len(items),
        "output": str(out_jsonl),
        "csv": args.csv,
        "manual_jsonl": str(manual_path),
        "logs": logs,
        "recent_articles": meta["feed_rows"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for row in meta["feed_rows"]:
        print(f"- {row['published_at']} | {row['fetch_status']} | 全文={row['fulltext']} | {row['title']} | {row['url']}")
    if logs:
        print("\n".join(f"[warn] {line}" for line in logs), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

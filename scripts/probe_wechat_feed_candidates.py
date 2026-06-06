#!/usr/bin/env python3
"""Probe WeChat public-account feed candidates without writing Feishu.

The script checks whether candidate URLs expose RSS/Atom/JSON Feed directly or
via HTML alternate links. GitHub issue URLs are also inspected through the
GitHub API so issue bodies can reveal Biz IDs, sample article URLs, or feed
clues. It writes a Markdown verification report for source-watch decisions.
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "wechat_feed_candidates.yaml"
DEFAULT_OUTPUT = ROOT / "docs" / "spikes" / "wechat_feed_candidate_verification.md"
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)
REQUEST_TIMEOUT_SECONDS = 8
MAX_DISCOVERED_FEED_LINKS = 5

socket.setdefaulttimeout(REQUEST_TIMEOUT_SECONDS)


@dataclass
class Candidate:
    name: str
    type: str
    url: str
    target: str = ""
    note: str = ""


@dataclass
class ProbeResult:
    candidate_url: str
    candidate_name: str
    candidate_type: str
    http_status: str = ""
    content_type: str = ""
    feed_detected: str = "否"
    feed_url: str = ""
    item_count: int = 0
    latest_titles: list[str] = field(default_factory=list)
    latest_urls: list[str] = field(default_factory=list)
    latest_published_at: list[str] = field(default_factory=list)
    issue_clues: list[str] = field(default_factory=list)
    failure_reason: str = ""
    recommendation: str = ""


class FeedLinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.feed_links: list[str] = []
        self.hrefs: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "link":
            rel = attr.get("rel", "").lower()
            typ = attr.get("type", "").lower()
            href = attr.get("href", "")
            if href and "alternate" in rel and any(mark in typ for mark in ["rss", "atom", "json"]):
                self.feed_links.append(urllib.parse.urljoin(self.base_url, href))
        if tag.lower() == "a":
            href = attr.get("href", "")
            if href:
                full = urllib.parse.urljoin(self.base_url, href)
                self.hrefs.append(full)
                if re.search(r"(rss|atom|feed|json)", full, re.I):
                    self.feed_links.append(full)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data.strip()


def load_candidates(path: Path) -> list[Candidate]:
    """Parse the small project YAML without adding a PyYAML dependency."""
    candidates: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#") or line.strip() == "candidates:":
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            if current:
                candidates.append(current)
            current = {}
            stripped = stripped[2:].strip()
            if ":" in stripped:
                key, value = stripped.split(":", 1)
                current[key.strip()] = value.strip().strip('"').strip("'")
            continue
        if current is not None and ":" in stripped:
            key, value = stripped.split(":", 1)
            current[key.strip()] = value.strip().strip('"').strip("'")
    if current:
        candidates.append(current)
    return [Candidate(**item) for item in candidates]


def fetch(url: str) -> tuple[str, str, str, bytes]:
    req = Request(url, headers={"User-Agent": DEFAULT_UA, "Accept": "application/rss+xml, application/atom+xml, application/feed+json, application/json, text/html;q=0.9, */*;q=0.8"})
    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            status = str(resp.status)
            content_type = resp.headers.get("Content-Type", "")
            final_url = resp.geturl()
            body = resp.read(2_000_000)
            return status, content_type, final_url, body
    except HTTPError as exc:
        body = exc.read(2000)
        return str(exc.code), exc.headers.get("Content-Type", ""), url, body
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def detect_json_feed(raw: bytes) -> tuple[bool, list[dict[str, str]]]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return False, []
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return False, []
    parsed = []
    for item in items[:10]:
        if not isinstance(item, dict):
            continue
        parsed.append({
            "title": str(item.get("title") or item.get("summary") or item.get("content_text") or "")[:160],
            "url": str(item.get("url") or item.get("external_url") or ""),
            "published": str(item.get("date_published") or item.get("date_modified") or ""),
        })
    return True, parsed


def text_of(element: ET.Element, names: list[str]) -> str:
    for name in names:
        found = element.find(name)
        if found is not None and found.text:
            return found.text.strip()
    for child in element.iter():
        bare = child.tag.split("}")[-1]
        if bare in names and child.text:
            return child.text.strip()
    return ""


def detect_xml_feed(raw: bytes) -> tuple[bool, list[dict[str, str]], str]:
    try:
        root = ET.fromstring(raw)
    except Exception:
        return False, [], ""
    bare_root = root.tag.split("}")[-1].lower()
    if bare_root not in {"rss", "feed", "rdf"}:
        return False, [], bare_root
    items = root.findall(".//item") if bare_root == "rss" else root.findall(".//{*}entry")
    parsed = []
    for item in items[:10]:
        link = text_of(item, ["link"])
        if bare_root == "feed":
            for child in item:
                if child.tag.split("}")[-1] == "link" and child.attrib.get("href"):
                    link = child.attrib.get("href", "")
                    break
        parsed.append({
            "title": text_of(item, ["title"])[:160],
            "url": link,
            "published": text_of(item, ["pubDate", "published", "updated", "date"]),
        })
    return True, parsed, bare_root


def github_issue_api_url(url: str) -> str:
    match = re.search(r"github\.com/([^/]+)/([^/]+)/issues/(\d+)", url)
    if not match:
        return ""
    owner, repo, issue = match.groups()
    return f"https://api.github.com/repos/{owner}/{repo}/issues/{issue}"


def extract_clues(text: str) -> list[str]:
    clues: list[str] = []
    patterns = [
        r"__biz=[A-Za-z0-9_\-=]+",
        r"biz[:：]\s*[A-Za-z0-9_\-=]+",
        r"https?://mp\.weixin\.qq\.com/[^\s)\"'<>]+",
        r"https?://[^\s)\"'<>]*(?:rss|atom|feed|json)[^\s)\"'<>]*",
        r"数字生命卡兹克",
        r"卡兹克",
    ]
    for pattern in patterns:
        for hit in re.findall(pattern, text, flags=re.I):
            if hit not in clues:
                clues.append(hit)
    return clues[:20]


def feed_like_urls(clues: list[str]) -> list[str]:
    urls: list[str] = []
    for clue in clues:
        cleaned = clue.replace("\\", "").replace("&quot;", "").strip("`'\"<>")
        cleaned = cleaned.split("\\u003c")[0]
        if not cleaned.startswith("http"):
            continue
        if re.search(r"(/feed/|rss|atom|\.xml|json)", cleaned, flags=re.I) and "github.com" not in cleaned:
            if cleaned not in urls:
                urls.append(cleaned)
    return urls[:5]


def parse_feed_from_bytes(result: ProbeResult, raw: bytes, url: str, content_type: str) -> None:
    is_json, json_items = detect_json_feed(raw)
    if is_json:
        result.feed_detected = "是"
        result.feed_url = url
        result.item_count = len(json_items)
        result.latest_titles = [item["title"] for item in json_items[:5]]
        result.latest_urls = [item["url"] for item in json_items[:5]]
        result.latest_published_at = [item["published"] for item in json_items[:5]]
        return
    is_xml, xml_items, _root = detect_xml_feed(raw)
    if is_xml:
        result.feed_detected = "是"
        result.feed_url = url
        result.item_count = len(xml_items)
        result.latest_titles = [item["title"] for item in xml_items[:5]]
        result.latest_urls = [item["url"] for item in xml_items[:5]]
        result.latest_published_at = [item["published"] for item in xml_items[:5]]


def try_feed_urls_from_clues(result: ProbeResult) -> None:
    if result.feed_detected == "是":
        return
    for feed_url in feed_like_urls(result.issue_clues):
        try:
            status, content_type, final_url, body = fetch(feed_url)
            result.issue_clues.append(f"tested_feed_url={feed_url} status={status} content_type={content_type}")
            if not status.startswith("2"):
                continue
            probe = ProbeResult(result.candidate_url, result.candidate_name, result.candidate_type)
            probe.http_status = status
            probe.content_type = content_type
            parse_feed_from_bytes(probe, body, final_url, content_type)
            if probe.feed_detected == "是":
                result.feed_detected = "是"
                result.feed_url = final_url
                result.item_count = probe.item_count
                result.latest_titles = probe.latest_titles
                result.latest_urls = probe.latest_urls
                result.latest_published_at = probe.latest_published_at
                return
        except Exception as exc:
            result.issue_clues.append(f"tested_feed_url={feed_url} failed={exc}")


def inspect_html_for_feeds(result: ProbeResult, raw: bytes, base_url: str) -> None:
    html = raw.decode("utf-8", errors="replace")
    parser = FeedLinkParser(base_url)
    parser.feed(html)
    seen: set[str] = set()
    for feed_url in parser.feed_links[:MAX_DISCOVERED_FEED_LINKS]:
        if feed_url in seen:
            continue
        seen.add(feed_url)
        try:
            status, content_type, final_url, body = fetch(feed_url)
            if not status.startswith("2"):
                continue
            probe = ProbeResult(result.candidate_url, result.candidate_name, result.candidate_type)
            probe.http_status = status
            probe.content_type = content_type
            parse_feed_from_bytes(probe, body, final_url, content_type)
            if probe.feed_detected == "是":
                result.feed_detected = "是"
                result.feed_url = final_url
                result.item_count = probe.item_count
                result.latest_titles = probe.latest_titles
                result.latest_urls = probe.latest_urls
                result.latest_published_at = probe.latest_published_at
                return
        except Exception:
            continue
    result.issue_clues.extend(extract_clues(html))


def inspect_github_issue(result: ProbeResult) -> None:
    api_url = github_issue_api_url(result.candidate_url)
    if not api_url:
        return
    try:
        status, content_type, _final_url, body = fetch(api_url)
        result.issue_clues.append(f"GitHub API status={status} content_type={content_type}")
        if status.startswith("2"):
            data = json.loads(body.decode("utf-8"))
            title = str(data.get("title") or "")
            issue_body = str(data.get("body") or "")
            state = str(data.get("state") or "")
            result.issue_clues.append(f"issue_title={title}")
            result.issue_clues.append(f"issue_state={state}")
            result.issue_clues.extend(extract_clues(title + "\n" + issue_body))
    except Exception as exc:
        result.issue_clues.append(f"GitHub API failed: {exc}")
    try_feed_urls_from_clues(result)


def recommendation_for(candidate: Candidate, result: ProbeResult) -> str:
    if result.feed_detected == "是" and result.item_count > 0 and candidate.type != "tool_route":
        return "auto_ready：检测到可读 feed，可进入后续 source_watch_probe。"
    if result.feed_detected == "是" and candidate.type == "tool_route":
        return "needs_user_dependency：检测到的是工具项目自身 feed，不是目标公众号文章 feed；可用于跟踪工具更新，但不能直接作为卡兹克来源。"
    if candidate.type == "rssabc_page":
        return "needs_user_dependency：未检测到公开 feed；可能需要 RSS之家账号/商业订阅或用户提供实际 feed_url。"
    if candidate.type == "github_issue":
        if any("__biz=" in clue or "biz" in clue.lower() for clue in result.issue_clues):
            return "needs_user_dependency：issue 有公众号/Biz 线索，但未发现可直接读取的 feed；需要 Wechat2RSS 收录源或自建服务。"
        return "blocked_not_recommended：issue 只能作为线索页，未发现可直接接入 feed。"
    if "we-mp-rss" in candidate.name:
        return "needs_user_dependency：适合作为自建中间层候选，需要部署服务并配置公众号订阅。"
    if "wewe-rss" in candidate.name:
        return "needs_user_dependency：适合作为私有化候选，但通常需要微信读书登录态/服务维护。"
    if "RSSHub" in candidate.name:
        return "unstable_spike_only：可作补充路由，公众号指定源通常依赖第三方路线或 cookie，不建议默认接入。"
    return "blocked_not_recommended：未发现可直接接入能力。"


def probe_candidate(candidate: Candidate) -> ProbeResult:
    result = ProbeResult(candidate.url, candidate.name, candidate.type)
    try:
        fetch_url = candidate.url
        status, content_type, final_url, body = fetch(fetch_url)
        result.http_status = status
        result.content_type = content_type
        if status.startswith("2"):
            parse_feed_from_bytes(result, body, final_url, content_type)
            if result.feed_detected != "是" and ("html" in content_type.lower() or body.lstrip().startswith(b"<!")):
                inspect_html_for_feeds(result, body, final_url)
            if candidate.type == "github_issue":
                inspect_github_issue(result)
                try_feed_urls_from_clues(result)
            if candidate.type == "tool_route":
                result.issue_clues.extend(extract_clues(body.decode("utf-8", errors="replace")))
        else:
            result.failure_reason = f"HTTP {status}"
            if candidate.type == "github_issue":
                inspect_github_issue(result)
    except Exception as exc:
        if "rssabc.com" in candidate.url and "www.rssabc.com" in candidate.url:
            fallback = candidate.url.replace("https://www.rssabc.com", "https://rssabc.com")
            result.issue_clues.append(f"www SSL failed, retried without www: {fallback}")
            try:
                status, content_type, final_url, body = fetch(fallback)
                result.http_status = status
                result.content_type = content_type
                if status.startswith("2"):
                    parse_feed_from_bytes(result, body, final_url, content_type)
                    if result.feed_detected != "是" and ("html" in content_type.lower() or body.lstrip().startswith(b"<!")):
                        inspect_html_for_feeds(result, body, final_url)
                else:
                    result.failure_reason = f"HTTP {status}"
            except Exception as fallback_exc:
                result.failure_reason = f"{exc}; fallback failed: {fallback_exc}"
        else:
            result.failure_reason = str(exc)
        if candidate.type == "github_issue":
            inspect_github_issue(result)
    result.recommendation = recommendation_for(candidate, result)
    return result


def render_report(results: list[ProbeResult], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    target_auto_ready = [item for item in results if item.recommendation.startswith("auto_ready") and item.candidate_type != "tool_route"]
    any_detected_feed = [item for item in results if item.feed_detected == "是"]
    rssabc = next((item for item in results if item.candidate_type == "rssabc_page"), None)
    wechat_issues = [item for item in results if item.candidate_type == "github_issue"]
    lines: list[str] = []
    lines.append("# 微信公众号 Feed 候选验证")
    lines.append("")
    lines.append("验证对象：`数字生命卡兹克` 公众号自动发现文章列表的候选 feed / 订阅服务。")
    lines.append("")
    lines.append("## 一页结论")
    if target_auto_ready:
        lines.append(f"- 找到 `数字生命卡兹克` 可直接读取的 feed：`{target_auto_ready[0].feed_url}`。")
    else:
        lines.append("- 没有找到 `数字生命卡兹克` 可直接用于默认采集的公开 RSS/Atom/JSON Feed。")
    if any_detected_feed:
        lines.append("- 本轮检测到的 feed 若来自工具项目 releases，只能说明工具项目可被订阅，不代表卡兹克公众号已可自动发现。")
    lines.append("- 当前最稳路径仍是 `02 URL投喂入口` 单篇 URL；下一步若要自动发现，优先做 `we-mp-rss / wewe-rss` 自建服务 PoC。")
    lines.append("- 本轮不建议把 RSS之家页面或 GitHub issue 直接接入 `daily_pipeline.py`。")
    lines.append("")
    lines.append("## 能力矩阵")
    lines.append("")
    lines.append("| 候选 | 类型 | HTTP | Content-Type | feed_detected | feed_url | item_count | recommendation |")
    lines.append("| --- | --- | --- | --- | --- | --- | ---: | --- |")
    for item in results:
        lines.append(
            f"| {item.candidate_name} | {item.candidate_type} | {item.http_status or '-'} | "
            f"{(item.content_type or '-').replace('|', '/')} | {item.feed_detected} | "
            f"{item.feed_url or '-'} | {item.item_count} | {item.recommendation} |"
        )
    lines.append("")
    lines.append("## RSS之家页面验证")
    if rssabc:
        lines.append(f"- URL：{rssabc.candidate_url}")
        lines.append(f"- HTTP：{rssabc.http_status} / `{rssabc.content_type}`")
        lines.append(f"- 是否检测到公开 feed：{rssabc.feed_detected}")
        if rssabc.feed_url:
            lines.append(f"- feed_url：{rssabc.feed_url}")
        if rssabc.failure_reason:
            lines.append(f"- 失败原因：{rssabc.failure_reason}")
        lines.append(f"- 判断：{rssabc.recommendation}")
    lines.append("")
    lines.append("## Wechat2RSS issue 线索验证")
    for item in wechat_issues:
        lines.append(f"### {item.candidate_name}")
        lines.append(f"- URL：{item.candidate_url}")
        lines.append(f"- HTTP：{item.http_status or '-'} / `{item.content_type or '-'}`")
        lines.append(f"- 是否检测到可读 feed：{item.feed_detected}")
        lines.append(f"- 判断：{item.recommendation}")
        if item.issue_clues:
            lines.append("- 线索：")
            for clue in item.issue_clues[:12]:
                lines.append(f"  - `{clue}`")
        else:
            lines.append("- 线索：未发现公众号 Biz、文章样本或 feed URL。")
    lines.append("")
    lines.append("## 工具路线判断")
    for keyword in ["we-mp-rss", "wewe-rss", "RSSHub"]:
        matching = [item for item in results if keyword.lower() in item.candidate_name.lower()]
        if not matching:
            continue
        lines.append(f"### {keyword}")
        for item in matching:
            lines.append(f"- {item.candidate_name}：{item.recommendation}")
            if item.issue_clues:
                lines.append(f"  - 页面线索：{'; '.join(item.issue_clues[:5])}")
    lines.append("")
    lines.append("## 下一步最小接入方案")
    lines.append("")
    lines.append("1. 继续保留单篇公众号 URL 作为 P0：`02 URL投喂入口 -> url_content_resolver.py -> 03 内容收件箱`。")
    lines.append("2. 如要自动发现卡兹克新文章，先单独开 `source_watch_probe`，部署或接入一个用户可控的公众号 RSS 服务。")
    lines.append("3. probe 输出只落本地报告，不写飞书、不进 Top10；连续稳定后再考虑接 `03 内容收件箱`。")
    lines.append("4. 正式接入前必须提供：feed_url、标题、文章链接、发布时间、去重指纹、失败原因。")
    lines.append("")
    lines.append("## 原始结果摘要")
    lines.append("")
    for item in results:
        lines.append(f"### {item.candidate_name}")
        lines.append(f"- candidate_url：{item.candidate_url}")
        lines.append(f"- candidate_type：{item.candidate_type}")
        lines.append(f"- http_status：{item.http_status or '-'}")
        lines.append(f"- content_type：`{item.content_type or '-'}`")
        lines.append(f"- feed_detected：{item.feed_detected}")
        lines.append(f"- feed_url：{item.feed_url or '-'}")
        lines.append(f"- item_count：{item.item_count}")
        if item.latest_titles:
            lines.append("- latest_titles：")
            for title, url, published in zip(item.latest_titles, item.latest_urls, item.latest_published_at):
                lines.append(f"  - {title} | {published or '-'} | {url or '-'}")
        if item.failure_reason:
            lines.append(f"- failure_reason：{item.failure_reason}")
        lines.append(f"- recommendation：{item.recommendation}")
        lines.append("")
    output.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    candidates = load_candidates(Path(args.config))
    if not candidates:
        raise SystemExit(f"No candidates found in {args.config}")
    results = []
    for candidate in candidates:
        print(f"Probing: {candidate.name} | {candidate.url}", flush=True)
        results.append(probe_candidate(candidate))
    render_report(results, Path(args.output))
    print(json.dumps({
        "ok": True,
        "candidates": len(candidates),
        "auto_ready": [item.feed_url for item in results if item.recommendation.startswith("auto_ready")],
        "output": args.output,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

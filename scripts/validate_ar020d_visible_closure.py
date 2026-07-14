#!/usr/bin/env python3
"""Validate AR-020D staging records, card pages, and visible DOM by content identity."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


VISIBLE_FIELDS = (
    "选题标题",
    "来源链接",
    "原始来源标题",
    "原始发布文案",
    "研究摘要",
    "受众钩子",
    "研究置信度",
    "我的切入",
    "内容结构",
    "需要补的证据",
    "推荐动作",
)

DOM_LABELS = {
    "原始来源标题": "原始标题：",
    "原始发布文案": "原始发布文案：",
    "研究摘要": "来源摘要：",
    "受众钩子": "受众钩子：",
    "我的切入": "Austin 角度：",
    "内容结构": "内容结构：",
    "研究置信度": "研究置信度：",
    "需要补的证据": "缺口：",
}


class VisibleClosureError(RuntimeError):
    pass


def normalized(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def canonical_snapshot(fields: dict[str, Any]) -> dict[str, str]:
    return {name: normalized(fields.get(name, "")) for name in VISIBLE_FIELDS}


def expected_staging_rows_from_original(
    original_rows: list[dict[str, Any]], visible_title_marker: str
) -> list[dict[str, str]]:
    """Map original final rows without consulting writer output or fallback helpers."""
    expected = []
    for row in original_rows:
        title = normalized(row.get("选题命题") or row.get("我的选题标题"))
        expected.append({
            "选题标题": f"{visible_title_marker}{title}",
            "来源链接": normalized(row.get("来源链接")),
            "原始来源标题": normalized(row.get("原始来源标题")),
            "原始发布文案": normalized(row.get("原始发布文案")),
            "研究摘要": normalized(row.get("研究摘要")),
            "受众钩子": normalized(row.get("受众钩子")),
            "研究置信度": normalized(row.get("研究置信度")),
            "我的切入": normalized(row.get("我的切入") or row.get("locked_natural_austin_angle")),
            "内容结构": normalized(row.get("内容结构")),
            "需要补的证据": normalized(row.get("需要补的证据")),
            "推荐动作": normalized(row.get("推荐动作")),
        })
    return expected


def snapshot_hash(fields: dict[str, Any]) -> str:
    payload = json.dumps(canonical_snapshot(fields), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def display_equivalent(source: str, displayed: str, *, empty_placeholder: str = "") -> bool:
    source = normalized(source)
    displayed = normalized(displayed)
    if not source and empty_placeholder:
        return displayed == empty_placeholder
    if displayed == source:
        return True
    return displayed.endswith("...") and source.startswith(displayed[:-3].rstrip())


def assert_record_readback(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> list[dict[str, str]]:
    if len(expected) != len(actual):
        raise VisibleClosureError(f"record_count_mismatch:{len(expected)}!={len(actual)}")
    results = []
    for index, (expected_row, actual_row) in enumerate(zip(expected, actual), start=1):
        expected_fields = canonical_snapshot(expected_row)
        actual_fields = canonical_snapshot(actual_row.get("fields", actual_row))
        mismatches = [name for name in VISIBLE_FIELDS if expected_fields[name] != actual_fields[name]]
        if mismatches:
            raise VisibleClosureError(f"record_{index}_field_mismatch:{','.join(mismatches)}")
        results.append({
            "record_id": normalized(actual_row.get("record_id")),
            "source_url": expected_fields["来源链接"],
            "snapshot_hash": snapshot_hash(expected_fields),
        })
    record_ids = [item["record_id"] for item in results]
    if any(not item for item in record_ids) or len(record_ids) != len(set(record_ids)):
        raise VisibleClosureError("record_ids_missing_or_duplicate")
    return results


def card_markdown_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in manifest.get("pages", []):
        candidate_ids = page.get("candidate_ids", [])
        markdown = [
            item.get("content", "")
            for item in page.get("card", {}).get("body", {}).get("elements", [])
            if item.get("tag") == "markdown" and str(item.get("content", "")).startswith("**")
        ]
        if len(markdown) != len(candidate_ids):
            raise VisibleClosureError(f"page_{page.get('page')}_manifest_candidate_count_mismatch")
        for candidate_id, content in zip(candidate_ids, markdown):
            title_match = re.match(r"\*\*\d+\.\s*(.*?)\*\*", content)
            link_match = re.search(r"\[查看原始(?:视频|文章)\]\((https?://[^)]+)\)", content)
            values = {"选题标题": normalized(title_match.group(1) if title_match else ""), "来源链接": normalized(link_match.group(1) if link_match else "")}
            for field, label in DOM_LABELS.items():
                match = re.search(rf"(?:^|\n){re.escape(label)}([^\n]*)", content)
                values[field] = normalized(match.group(1) if match else "")
            values["推荐动作"] = "生成脚本包"
            rows.append({"record_id": normalized(candidate_id), "fields": values})
    return rows


def parse_dom_page(text: str, html: str) -> list[dict[str, Any]]:
    starts = list(re.finditer(r"(?m)^(\d+)\.\s+(.+)$", text))
    links = re.findall(r'href="(https?://[^"]+)"[^>]*>查看原始(?:视频|文章)</a>', html)
    rows = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        block = text[start.end():end]
        values = {"选题标题": normalized(start.group(2)), "来源链接": normalized(links[index] if index < len(links) else "")}
        for field, label in DOM_LABELS.items():
            match = re.search(rf"(?m)^{re.escape(label)}(.*)$", block)
            values[field] = normalized(match.group(1) if match else "")
        values["推荐动作"] = "生成脚本包"
        rows.append({"fields": values})
    return rows


def dom_snapshot_hash(text: str, html: str) -> str:
    return hashlib.sha256((normalized(text) + "\n" + html).encode("utf-8")).hexdigest()


def validate_screenshot_page_identity(
    manifest: dict[str, Any], evidence: list[dict[str, Any]], *, run_marker: str
) -> dict[str, Any]:
    """Bind each screenshot to the same page DOM and viewport identity captured beside it."""
    page_count = int(manifest.get("page_count") or 0)
    if len(evidence) != page_count * 2:
        raise VisibleClosureError("screenshot_evidence_count_mismatch")
    by_key: dict[tuple[int, str], dict[str, Any]] = {}
    screenshot_hashes: set[str] = set()
    results: list[dict[str, Any]] = []
    for item in evidence:
        page_index = int(item.get("page_index") or 0)
        position = normalized(item.get("position"))
        key = (page_index, position)
        if key in by_key or position not in {"top", "bottom"}:
            raise VisibleClosureError("screenshot_duplicate_page_or_position")
        if page_index < 1 or page_index > page_count or int(item.get("page_count") or 0) != page_count:
            raise VisibleClosureError("screenshot_wrong_page_identity")
        page = manifest["pages"][page_index - 1]
        screenshot = Path(str(item.get("screenshot_path") or ""))
        dom_text_path = Path(str(item.get("dom_text_path") or ""))
        dom_html_path = Path(str(item.get("dom_html_path") or ""))
        if not screenshot.is_file() or not dom_text_path.is_file() or not dom_html_path.is_file():
            raise VisibleClosureError("screenshot_or_dom_artifact_missing")
        screenshot_hash = hashlib.sha256(screenshot.read_bytes()).hexdigest()
        if screenshot_hash != normalized(item.get("screenshot_sha256")):
            raise VisibleClosureError("screenshot_hash_mismatch")
        if screenshot_hash in screenshot_hashes:
            raise VisibleClosureError("screenshot_duplicate_page_capture")
        screenshot_hashes.add(screenshot_hash)
        dom_text = dom_text_path.read_text(encoding="utf-8")
        dom_html = dom_html_path.read_text(encoding="utf-8")
        if dom_snapshot_hash(dom_text, dom_html) != normalized(item.get("dom_snapshot_hash")):
            raise VisibleClosureError("screenshot_dom_snapshot_hash_mismatch")
        dom_rows = parse_dom_page(dom_text, dom_html)
        if len(dom_rows) != len(page.get("candidate_ids", [])):
            raise VisibleClosureError("screenshot_manifest_candidate_count_mismatch")
        expected_first_id = normalized(page.get("first_candidate_id") or page.get("candidate_ids", [""])[0])
        expected_first_title = normalized(page.get("first_candidate_title"))
        actual_first_title = normalized(dom_rows[0]["fields"].get("选题标题")) if dom_rows else ""
        if normalized(item.get("first_candidate_id")) != expected_first_id:
            raise VisibleClosureError("screenshot_first_candidate_id_mismatch")
        if not display_equivalent(expected_first_title, actual_first_title):
            raise VisibleClosureError("screenshot_dom_first_candidate_mismatch")
        if not display_equivalent(expected_first_title, normalized(item.get("first_candidate_title"))):
            raise VisibleClosureError("screenshot_first_candidate_title_mismatch")
        viewport_text = normalized(item.get("viewport_text"))
        page_label = f"第 {page_index}/{page_count} 页"
        if run_marker not in dom_text or page_label not in dom_text:
            raise VisibleClosureError("screenshot_dom_run_or_page_marker_missing")
        if position == "top":
            if "今日选题速选" not in viewport_text or page_label not in viewport_text:
                raise VisibleClosureError("screenshot_top_missing_header_or_page_identity")
            if not display_equivalent(expected_first_title, normalized(item.get("viewport_first_candidate_title"))):
                raise VisibleClosureError("screenshot_top_is_mid_card")
        elif "本页都不选" not in viewport_text:
            raise VisibleClosureError("screenshot_bottom_missing_page_action")
        by_key[key] = item
        results.append({
            "page_index": page_index,
            "position": position,
            "candidate_count": len(dom_rows),
            "first_candidate_id": expected_first_id,
            "first_candidate_title": expected_first_title,
            "screenshot_sha256": screenshot_hash,
            "dom_snapshot_hash": normalized(item.get("dom_snapshot_hash")),
        })
    return {"ok": True, "page_count": page_count, "captures": sorted(results, key=lambda row: (row["page_index"], row["position"]))}


def validate_content_closure(
    expected_rows: list[dict[str, Any]],
    readback_records: list[dict[str, Any]],
    manifest: dict[str, Any],
    dom_pages: list[tuple[str, str]],
    *,
    required_marker: str,
    forbidden_markers: tuple[str, ...] = ("[AR-020D R5 TEST]",),
) -> dict[str, Any]:
    record_results = assert_record_readback(expected_rows, readback_records)
    expected_by_id = {item["record_id"]: row for item, row in zip(record_results, expected_rows)}
    manifest_rows = card_markdown_rows(manifest)
    manifest_ids = [row["record_id"] for row in manifest_rows]
    if manifest_ids != [item["record_id"] for item in record_results]:
        raise VisibleClosureError("manifest_record_order_mismatch")
    manifest_by_id = {row["record_id"]: row["fields"] for row in manifest_rows}
    for candidate_id in manifest_ids:
        source = canonical_snapshot(expected_by_id[candidate_id])
        displayed = canonical_snapshot(manifest_by_id[candidate_id])
        mismatches = []
        for name in VISIBLE_FIELDS:
            if name == "选题标题":
                matches = display_equivalent(source[name], displayed[name])
            elif name == "原始来源标题":
                matches = display_equivalent(source[name], displayed[name], empty_placeholder="平台未提供独立标题")
            elif name == "原始发布文案":
                matches = display_equivalent(
                    source[name], displayed[name], empty_placeholder="平台未提供独立发布文案"
                )
            else:
                matches = source[name] == displayed[name]
            if not matches:
                mismatches.append(name)
        if mismatches:
            raise VisibleClosureError(f"manifest_{candidate_id}_field_mismatch:{','.join(mismatches)}")
    dom_rows = [row for text, html in dom_pages for row in parse_dom_page(text, html)]
    if len(dom_rows) != len(manifest_rows):
        raise VisibleClosureError("dom_candidate_count_mismatch")
    page_hashes = []
    offset = 0
    for page_index, page in enumerate(manifest.get("pages", []), start=1):
        text, _html = dom_pages[page_index - 1]
        if required_marker not in text:
            raise VisibleClosureError(f"page_{page_index}_required_marker_missing")
        if any(marker in text for marker in forbidden_markers):
            raise VisibleClosureError(f"page_{page_index}_stale_marker")
        ids = page.get("candidate_ids", [])
        actual_page_rows = dom_rows[offset:offset + len(ids)]
        for candidate_id, actual in zip(ids, actual_page_rows):
            visible_expected = canonical_snapshot(manifest_by_id[candidate_id])
            actual_fields = canonical_snapshot(actual["fields"])
            mismatches = [name for name in VISIBLE_FIELDS if visible_expected[name] != actual_fields[name]]
            if mismatches:
                raise VisibleClosureError(f"dom_{candidate_id}_field_mismatch:{','.join(mismatches)}")
        page_payload = [snapshot_hash(manifest_by_id[candidate_id]) for candidate_id in ids]
        page_hashes.append({"page": page_index, "candidate_ids": ids, "snapshot_hash": hashlib.sha256("\n".join(page_payload).encode()).hexdigest()})
        offset += len(ids)
    return {
        "ok": True,
        "candidate_count": len(record_results),
        "candidate_ids": manifest_ids,
        "record_snapshots": record_results,
        "page_snapshots": page_hashes,
    }


def validate_original_content_closure(
    original_rows: list[dict[str, Any]],
    readback_records: list[dict[str, Any]],
    manifest: dict[str, Any],
    dom_pages: list[tuple[str, str]],
    *,
    visible_title_marker: str,
    forbidden_markers: tuple[str, ...] = ("[AR-020D R5 TEST]",),
) -> dict[str, Any]:
    expected_rows = expected_staging_rows_from_original(original_rows, visible_title_marker)
    result = validate_content_closure(
        expected_rows,
        readback_records,
        manifest,
        dom_pages,
        required_marker=visible_title_marker.strip(),
        forbidden_markers=forbidden_markers,
    )
    result["original_source_snapshots"] = [
        {
            "source_url": normalized(row.get("来源链接")),
            "original_source_title": normalized(row.get("原始来源标题")),
            "original_publication_copy": normalized(row.get("原始发布文案")),
            "source_semantics_hash": hashlib.sha256(json.dumps({
                "来源链接": normalized(row.get("来源链接")),
                "原始来源标题": normalized(row.get("原始来源标题")),
                "原始发布文案": normalized(row.get("原始发布文案")),
                "研究摘要": normalized(row.get("研究摘要")),
                "受众钩子": normalized(row.get("受众钩子")),
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        }
        for row in original_rows
    ]
    return result

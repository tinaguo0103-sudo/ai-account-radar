from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any
from urllib.parse import urlsplit, urlunsplit


MAX_REPRESENTATIVES = 5
DEFAULT_REPRESENTATIVES = 3


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _canonical_url(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


def _event_label(candidate: dict[str, Any]) -> str:
    label = _first(
        candidate,
        "event_name",
        "event_anchor",
        "事件锚点",
        "原始来源标题",
        "source_title",
        "title",
        "我的选题标题",
    )
    value = _text(label)
    return re.sub(r"[.…]{2,}$", "", value).strip(" ：:，,。")


def _event_descriptor(candidate: dict[str, Any]) -> str:
    explicit = _text(candidate.get("trend_event_key") or candidate.get("trend_event_id"))
    if explicit:
        return explicit
    event = _event_label(candidate)
    core_problem = _text(
        _first(candidate, "core_audience_problem", "真实用户问题", "选题命题")
    )
    impact = _text(_first(candidate, "primary_impact", "影响对象", "业务变化判断"))
    # Event anchors are the strongest durable signal in the current collection
    # contract. Core problem and impact only disambiguate exact same-name events.
    descriptor = event.casefold()
    if not descriptor:
        descriptor = _canonical_url(
            _first(candidate, "source_url", "来源链接", "内容链接", "canonical_url")
        )
    if not descriptor:
        descriptor = f"{core_problem.casefold()}|{impact.casefold()}"
    return descriptor


def _event_id(run_id: str, descriptor: str) -> str:
    digest = hashlib.sha256(f"{run_id}|{descriptor}".encode()).hexdigest()[:20]
    return f"trend:{digest}"


def _source_identity(candidate: dict[str, Any]) -> str:
    url = _canonical_url(
        _first(candidate, "source_url", "来源链接", "内容链接", "canonical_url")
    )
    if url:
        return url
    return _text(
        _first(candidate, "aweme_id", "external_id", "item_id", "candidate_id", "local_id")
    )


def _engagement(candidate: dict[str, Any]) -> dict[str, Any]:
    missing = candidate.get("fact_missing_reasons")
    if not isinstance(missing, dict):
        missing = {}
    output: dict[str, Any] = {}
    for name, keys in {
        "likes": ("likes", "点赞数"),
        "comments": ("comments", "评论数"),
        "favorites": ("favorites", "收藏数"),
        "shares": ("shares", "分享数"),
    }.items():
        value = _first(candidate, *keys)
        output[name] = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
        if output[name] is None:
            output[f"{name}_missing_reason"] = _text(missing.get(name) or "not_available")
    return output


def _source(candidate: dict[str, Any], item: dict[str, Any] | None) -> dict[str, Any]:
    rows = [candidate, item or {}]
    first = lambda *keys: next(
        (value for row in rows if (value := _first(row, *keys)) not in (None, "", [], {})),
        None,
    )
    url = _canonical_url(first("source_url", "来源链接", "内容链接", "canonical_url"))
    platform = _text(first("platform", "平台", "source"))
    provenance = first("fact_provenance", "provenance", "事实来源")
    if not isinstance(provenance, dict):
        provenance = {"capture": _text(provenance)} if provenance else {}
    source = {
        "source_id": _source_identity(candidate),
        "candidate_id": _text(candidate.get("candidate_id")),
        "item_id": _text(candidate.get("item_id")),
        "url": url,
        "platform": platform or ("douyin" if "douyin.com" in url else "web"),
        "author": _text(first("author", "作者", "原始来源账号", "account")),
        "title": _text(first("source_title", "原始来源标题", "title", "内容标题")),
        "summary": _text(first("source_summary", "原始发布文案", "summary", "正文/字幕/简介片段")),
        "published_at": _text(first("published_at", "发布时间")),
        "published_display": _text(first("published_at_display", "发布时间展示")),
        "recency": first("published_recency", "recency"),
        "engagement": _engagement(candidate),
        "provenance": provenance,
        "signal_source": _text(candidate.get("discovery_source") or candidate.get("候选来源方式")),
        "account_role": "auxiliary_signal",
        "source_role": (
            "conflicting_view"
            if _text(first("viewpoint_role", "观点关系")) == "conflicting_view"
            else "independent_view"
        ),
        "understanding_status": "metadata_only",
        "understanding_failure": "",
    }
    return source


def _source_role(source: dict[str, Any], *, strongest_id: str) -> str:
    if source.get("source_role") == "conflicting_view":
        return "conflicting_view"
    host = urlsplit(str(source.get("url") or "")).netloc.lower()
    if host and "douyin.com" not in host:
        return "original_or_official"
    if source.get("source_id") == strongest_id:
        return "traffic_signal"
    title = _text(source.get("title")).casefold()
    if any(word in title for word in ("实测", "教程", "演示", "复盘", "案例")):
        return "scene_or_demo"
    return "independent_view"


def _strongest_source_id(sources: list[dict[str, Any]]) -> str:
    def rank(row: dict[str, Any]) -> tuple[int, float, str]:
        likes = row.get("engagement", {}).get("likes")
        return (1 if isinstance(likes, (int, float)) else 0, float(likes or 0), row["source_id"])

    return max(sources, key=rank)["source_id"] if sources else ""


def _information_signature(source: dict[str, Any]) -> tuple[str, ...]:
    value = " ".join(
        _text(source.get(key)).casefold()
        for key in ("title", "summary", "source_role")
    )
    tokens = re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", value)
    compact = re.sub(r"\s+", "", value)
    shingles = [compact[index:index + 3] for index in range(max(0, len(compact) - 2))]
    return tuple(sorted(set(tokens + shingles)))


def select_representative_sources(
    sources: list[dict[str, Any]],
    *,
    default_count: int = DEFAULT_REPRESENTATIVES,
    hard_cap: int = MAX_REPRESENTATIVES,
) -> list[str]:
    if not sources:
        return []
    limit = min(max(1, default_count), hard_cap, len(sources))
    priority = {
        "original_or_official": 0,
        "traffic_signal": 1,
        "scene_or_demo": 2,
        "conflicting_view": 3,
        "independent_view": 4,
    }
    ordered = sorted(
        sources,
        key=lambda row: (
            priority.get(str(row.get("source_role")), 9),
            -(float(row.get("engagement", {}).get("likes") or -1)),
            str(row.get("source_id")),
        ),
    )
    selected: list[str] = []
    covered: set[str] = set()
    for source in ordered:
        signature = set(_information_signature(source))
        gain = signature - covered
        if selected and not gain:
            continue
        selected.append(str(source["source_id"]))
        covered.update(signature)
        if len(selected) >= limit:
            break
    return selected[:hard_cap]


def _traffic_stage(sources: list[dict[str, Any]]) -> dict[str, Any]:
    with_engagement = [
        row for row in sources
        if any(isinstance(row.get("engagement", {}).get(key), (int, float))
               for key in ("likes", "comments", "favorites", "shares"))
    ]
    with_time = [
        row for row in sources
        if row.get("published_at") or row.get("published_display") or row.get("recency")
    ]
    platforms = sorted({str(row.get("platform") or "") for row in sources if row.get("platform")})
    independent = len({str(row.get("author") or row.get("url") or "") for row in sources})
    return {
        "status": "evidence_present" if with_engagement and with_time else "evidence_limited",
        "engagement_source_count": len(with_engagement),
        "time_source_count": len(with_time),
        "independent_source_count": independent,
        "platforms": platforms,
        "raw_engagement_sum_forbidden": True,
    }


def _persona_stage(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    reasons = sorted({
        _text(_first(row, "我的账号为什么能讲", "persona_fit", "persona_reason"))
        for row in candidates
        if _first(row, "我的账号为什么能讲", "persona_fit", "persona_reason")
    })
    return {
        "status": "reviewable" if reasons else "needs_editorial_judgment",
        "reasons": reasons[:3],
        "legacy_four_scene_gate": False,
    }


def _differentiation_stage(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    angles = []
    mainstream = []
    for row in candidates:
        angle = _text(_first(row, "我的蹭热点角度", "我能讲出的独特角度", "内部切入角度"))
        common = _text(_first(row, "普通AI资讯号会怎么讲", "market_mainstream_angle"))
        if angle and angle not in angles:
            angles.append(angle)
        if common and common not in mainstream:
            mainstream.append(common)
    return {
        "status": "reviewable" if angles else "needs_editorial_judgment",
        "primary_angle": angles[0] if angles else "",
        "alternative_angles": angles[1:3],
        "mainstream_angles": mainstream[:3],
    }


def build_hotspot_cards(
    candidates: list[dict[str, Any]],
    *,
    items: list[dict[str, Any]],
    run_id: str,
) -> list[dict[str, Any]]:
    item_by_id = {str(row.get("item_id") or ""): row for row in items}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        descriptor = _event_descriptor(candidate)
        if not descriptor:
            continue
        groups[descriptor].append(candidate)
    cards: list[dict[str, Any]] = []
    for descriptor, rows in sorted(groups.items()):
        deduped: dict[str, dict[str, Any]] = {}
        for row in rows:
            identity = _source_identity(row)
            if not identity:
                continue
            deduped.setdefault(identity, row)
        if not deduped:
            continue
        source_rows = [
            _source(row, item_by_id.get(str(row.get("item_id") or "")))
            for row in deduped.values()
        ]
        strongest = _strongest_source_id(source_rows)
        for source in source_rows:
            source["source_role"] = _source_role(source, strongest_id=strongest)
        representative_ids = select_representative_sources(source_rows)
        event_id = _event_id(run_id, descriptor)
        label = _event_label(next(iter(deduped.values()))) or event_id
        representative_source = next(
            (row for row in source_rows if row["source_id"] in representative_ids),
            source_rows[0],
        )
        card = {
            "candidate_id": event_id,
            "trend_event_id": event_id,
            "run_id": run_id,
            "item_id": representative_source["item_id"],
            "representative_item_id": representative_source["item_id"],
            "event_name": label,
            "title": label,
            "source_url": representative_source["url"],
            "source_count": len(source_rows),
            "sources": source_rows,
            "representative_source_ids": representative_ids,
            "traffic_opportunity": _traffic_stage(source_rows),
            "persona_stability": _persona_stage(rows),
            "differentiation": _differentiation_stage(rows),
            "account_role": "auxiliary_signal",
            "legacy_candidate_ids": sorted(
                _text(row.get("candidate_id")) for row in rows
                if row.get("candidate_id")
            ),
            "merged_input_count": len(rows),
        }
        cards.append(card)
    return cards


def representative_candidates(
    cards: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_source = {_source_identity(row): row for row in candidates}
    output = []
    seen: set[str] = set()
    for card in cards:
        for source_id in card.get("representative_source_ids", []):
            row = by_source.get(str(source_id))
            if row is not None and source_id not in seen:
                output.append(row)
                seen.add(str(source_id))
    return output


def attach_understanding(
    cards: list[dict[str, Any]],
    packages: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    package_by_source = {
        _canonical_url(row.get("source_url")): row
        for row in packages
        if _canonical_url(row.get("source_url"))
    }
    failure_by_item = {
        _text(row.get("item_id") or row.get("candidate_id")): _text(
            row.get("reason") or row.get("failure")
        )
        for row in failures
    }
    understanding_results = []
    for card in cards:
        understood = []
        for source in card["sources"]:
            package = package_by_source.get(source["url"])
            if package and package.get("status") in {"completed", "completed_with_failures"}:
                source["understanding_status"] = "analyzed"
                source["understanding_failure"] = ""
                understood.append(package)
            else:
                item_failure = failure_by_item.get(source.get("item_id", ""), "")
                if item_failure:
                    source["understanding_status"] = "failed"
                    source["understanding_failure"] = item_failure
        representative_set = set(card.get("representative_source_ids", []))
        representative_understood = [
            package for package in understood
            if _canonical_url(package.get("source_url")) in {
                source["url"] for source in card["sources"]
                if source["source_id"] in representative_set
            }
        ]
        card["deep_read"] = {
            "requested_count": len(representative_set),
            "completed_count": len(representative_understood),
            "failed_count": sum(
                source["understanding_status"] == "failed"
                for source in card["sources"]
                if source["source_id"] in representative_set
            ),
            "status": (
                "completed" if representative_understood
                else "insufficient_evidence"
            ),
            "information_gain_stop": True,
            "max_sources": MAX_REPRESENTATIVES,
        }
        card["cluster_synthesis"] = synthesize_card(card, representative_understood)
        if representative_understood:
            understanding_results.append({
                "candidate_id": card["candidate_id"],
                "base_item_id": card["representative_item_id"],
                "package": {
                    "status": (
                        "completed" if card["deep_read"]["failed_count"] == 0
                        else "completed_with_failures"
                    ),
                    "source_url": card["source_url"],
                    "representative_packages": representative_understood,
                    "cluster_synthesis": card["cluster_synthesis"],
                },
            })
    return cards, understanding_results


def synthesize_card(
    card: dict[str, Any],
    representative_packages: list[dict[str, Any]],
) -> dict[str, Any]:
    scenes: list[str] = []
    unresolved: list[str] = []
    for package in representative_packages:
        text = _text(package.get("title"))
        if text and text not in scenes:
            scenes.append(text)
        for value in package.get("unresolved_terms", []) or []:
            normalized = _text(value)
            if normalized and normalized not in unresolved:
                unresolved.append(normalized)
    differentiation = card.get("differentiation", {})
    return {
        "event_name": card["event_name"],
        "timeline": [
            {
                "published_at": source.get("published_at"),
                "published_display": source.get("published_display"),
                "source_id": source.get("source_id"),
            }
            for source in card["sources"]
            if source.get("published_at") or source.get("published_display")
        ],
        "traffic_signals": card.get("traffic_opportunity"),
        "mainstream_angles": differentiation.get("mainstream_angles", []),
        "conflicting_views": [
            source["title"] for source in card["sources"]
            if source.get("source_role") == "conflicting_view"
        ],
        "scenes_actions_consequences": scenes[:5],
        "persona_connection": card.get("persona_stability"),
        "primary_angle": differentiation.get("primary_angle", ""),
        "alternative_angles": differentiation.get("alternative_angles", []),
        "unresolved": unresolved,
        "source_index": [
            {
                "source_id": source["source_id"],
                "url": source["url"],
                "role": source["source_role"],
                "understanding_status": source["understanding_status"],
            }
            for source in card["sources"]
        ],
    }


def cards_json(cards: list[dict[str, Any]]) -> str:
    return json.dumps(cards, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

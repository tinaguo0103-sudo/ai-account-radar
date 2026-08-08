from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit


MAX_REPRESENTATIVES = 5
DEFAULT_REPRESENTATIVES = 3
GENERIC_ANGLE_PHRASES = (
    "换成自己的语言",
    "放进真实场景",
    "不照搬表达",
    "吸收它的选题承诺",
)

EVENT_ANCHOR_PATTERNS = (
    (r"deepseek\s*v?4", "DeepSeek V4"),
    (r"minimax\s*h3", "MiniMax H3"),
    (r"trae\s*work", "TRAE Work"),
    (r"obsidian.*(?:5|五).*skill", "Obsidian 5 Skills"),
    (r"(?:长期记忆|long[- ]?term memory)", "Agent 长期记忆"),
    (r"(?:动画|动漫).*(?:配音|声音|voice)|(?:配音|声音|voice).*(?:动画|动漫)", "animated-voiceover"),
)

SEEDANCE_ENTITY_PATTERN = r"seedance\s*2[.．]?5|seedance\s*25"
SEEDANCE_TEST_PATTERN = re.compile(r"实测|测试|测评|案例|演示", re.IGNORECASE)


def _normalized_event_phrase(value: str) -> str:
    value = re.sub(r"#[^\s#]+", " ", value)
    value = re.sub(SEEDANCE_ENTITY_PATTERN, " ", value, flags=re.IGNORECASE)
    value = re.sub(r"[^\w\u4e00-\u9fff]+", " ", value)
    return _text(value).casefold()


def _seedance_experiment_anchor(source_text: str) -> str:
    facts: list[str] = []
    duration = re.search(r"(\d{1,3})\s*(秒|分钟)", source_text)
    if duration:
        seconds = int(duration.group(1)) * (60 if duration.group(2) == "分钟" else 1)
        facts.append(f"duration_{seconds}s")
    if "连续生成" in source_text:
        facts.append("action_continuous_generation")
    if re.search(r"(?:角色|人物)一致性(?:检查|验证|测试)?", source_text):
        facts.append("objective_character_consistency")
    if re.search(r"首尾帧.*运镜|运镜.*首尾帧", source_text):
        facts.append("objective_first_last_frame_camera_motion")
    return "|".join(facts)


def _seedance_event_descriptor(source_text: str, source_url: str) -> str:
    """Keep the model version as an entity, never as the event identity."""
    work_match = re.search(r"《([^》]{2,60})》", source_text)
    if work_match:
        return f"seedance 2.5|independent_work:{_normalized_event_phrase(work_match.group(1))}"

    if SEEDANCE_TEST_PATTERN.search(source_text):
        experiment = _seedance_experiment_anchor(source_text)
        if experiment:
            return f"seedance 2.5|test_demo_review:{experiment}"
        return f"seedance 2.5|test_demo_review:{source_url or _normalized_event_phrase(source_text)}"

    if re.search(r"发布|正式上线|上新|更新|升级|新功能", source_text):
        return "seedance 2.5|release_feature_announcement:model_release"

    # An entity-only mention is a signal, not evidence that two rows describe
    # the same event. Its canonical source keeps unrelated mentions separate.
    return f"seedance 2.5|entity_signal:{source_url or _normalized_event_phrase(source_text)}"


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
    source_text = " ".join(
        _text(_first(candidate, key))
        for key in ("原始来源标题", "source_title", "内容标题", "title", "正文/字幕/简介片段")
    ).casefold()
    source_url = _canonical_url(
        _first(candidate, "source_url", "来源链接", "内容链接", "canonical_url")
    )
    if re.search(SEEDANCE_ENTITY_PATTERN, source_text, flags=re.IGNORECASE):
        return _seedance_event_descriptor(source_text, source_url)
    for pattern, anchor in EVENT_ANCHOR_PATTERNS:
        if re.search(pattern, source_text, flags=re.IGNORECASE):
            return anchor.casefold()
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
        "status": "hypotheses_available" if angles else "needs_deep_read",
        "primary_angle": "",
        "alternative_angles": [],
        "angle_hypotheses": angles[:3],
        "mainstream_angles": mainstream[:3],
        "pre_read_only": True,
    }


def _source_age_seconds(source: dict[str, Any], run_id: str) -> float | None:
    recency = source.get("recency")
    if isinstance(recency, dict):
        values = [
            recency.get("minimum_seconds"),
            recency.get("maximum_seconds"),
        ]
        numeric = [float(value) for value in values if isinstance(value, (int, float))]
        if numeric:
            return max(numeric)
    published = _text(source.get("published_at"))
    match = re.search(r"run_(\d{8})_", run_id)
    if published and match:
        try:
            captured = datetime.strptime(match.group(1), "%Y%m%d").replace(tzinfo=timezone.utc)
            value = datetime.fromisoformat(published.replace("Z", "+00:00"))
            return max(0.0, (captured - value.astimezone(timezone.utc)).total_seconds())
        except ValueError:
            pass
    display = _text(source.get("published_display"))
    if display in {"今天", "刚刚"}:
        return 0.0
    units = {"分钟": 60, "小时": 3600, "天": 86400, "周": 604800, "月": 2592000}
    relative = re.search(r"(\d+)\s*(分钟|小时|天|周|月)前", display)
    if relative:
        return float(int(relative.group(1)) * units[relative.group(2)])
    return None


def _platform_key(value: Any) -> str:
    platform = _text(value).casefold()
    if platform in {"douyin", "抖音"}:
        return "douyin"
    return platform or "unknown"


def _recency_cohort(age_seconds: float | None) -> str:
    if age_seconds is None:
        return "unknown"
    if age_seconds <= 2 * 86400:
        return "0_2d"
    if age_seconds <= 7 * 86400:
        return "3_7d"
    if age_seconds <= 30 * 86400:
        return "8_30d"
    return "31d_plus"


def _persona_qualification(card: dict[str, Any]) -> tuple[bool, str]:
    reasons = card.get("persona_stability", {}).get("reasons") or []
    if reasons:
        return True, _text(reasons[0])
    text = " ".join(
        [card.get("event_name", "")]
        + [source.get("title", "") for source in card.get("sources", [])]
        + [source.get("summary", "") for source in card.get("sources", [])]
    ).casefold()
    markers = (
        "agent", "skill", "workflow", "work", "codex", "claude", "trae",
        "工作流", "工作台", "工具", "实战", "教程", "材料", "公文", "办公",
        "项目", "开发", "编程", "内容", "视频", "财务", "企业", "自动化",
    )
    if re.search(r"\bai\b", text):
        return True, "可自然连接到 AI 的判断、行动或实验"
    matched = next((marker for marker in markers if marker in text), "")
    if matched:
        return True, f"可自然连接到 {matched} 的判断、行动或实验"
    return False, "当前公开材料尚未显示 Austin 能自然提供的判断、行动或实验"


def qualify_hotspot_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    likes_by_cohort: dict[tuple[str, str], list[float]] = defaultdict(list)
    for card in cards:
        for source in card.get("sources", []):
            likes = source.get("engagement", {}).get("likes")
            age = _source_age_seconds(source, str(card.get("run_id") or ""))
            cohort = _recency_cohort(age)
            source["recency_cohort"] = cohort
            if isinstance(likes, (int, float)) and not isinstance(likes, bool):
                likes_by_cohort[(_platform_key(source.get("platform")), cohort)].append(float(likes))
    for card in cards:
        cohort_percentiles: list[float] = []
        visible_likes: list[float] = []
        cohorts: set[str] = set()
        for source in card.get("sources", []):
            likes = source.get("engagement", {}).get("likes")
            platform = _platform_key(source.get("platform"))
            cohort = str(source.get("recency_cohort") or "unknown")
            cohorts.add(cohort)
            if isinstance(likes, (int, float)) and not isinstance(likes, bool):
                values = likes_by_cohort.get((platform, cohort), [])
                visible_likes.append(float(likes))
                if values:
                    cohort_percentiles.append(
                        sum(value <= float(likes) for value in values) / len(values)
                    )
        strongest_percentile = max(cohort_percentiles, default=0.0)
        recent_signal = bool(cohorts & {"0_2d", "3_7d"})
        traffic = card.get("traffic_opportunity", {})
        multi_source = (
            int(traffic.get("independent_source_count") or 0) >= 2
            and int(traffic.get("time_source_count") or 0) >= 1
        )
        official_with_time = any(
            source.get("source_role") == "original_or_official"
            and (source.get("published_at") or source.get("published_display") or source.get("recency"))
            for source in card.get("sources", [])
        )
        relative_opportunity = bool(visible_likes and strongest_percentile > 0.5)
        # An official, recent publication proves that an event is authentic and
        # timely. It does not, by itself, prove a traffic opportunity.
        traffic_qualified = recent_signal and (relative_opportunity or multi_source)
        persona_qualified, persona_reason = _persona_qualification(card)
        eligible = traffic_qualified and persona_qualified
        if relative_opportunity:
            traffic_reason = (
                f"最强来源可见点赞 {int(max(visible_likes))}，"
                f"处于同平台同龄内容的相对前 {max(1, round((1 - strongest_percentile) * 100))}%"
            )
        elif multi_source:
            traffic_reason = (
                f"{int(traffic.get('independent_source_count') or 0)} 个独立来源在同一时间窗指向同一事件"
            )
        elif official_with_time:
            traffic_reason = (
                "存在带时间的原始或官方事件信号，但缺少相对互动或多源佐证，"
                "保留为热点线索"
            )
        elif visible_likes and not recent_signal:
            traffic_reason = (
                f"可见点赞 {int(max(visible_likes))}，但不属于今日或近期 cohort，"
                "保留历史信号而不占用今日深读预算"
            )
        elif visible_likes:
            traffic_reason = (
                f"可见点赞 {int(max(visible_likes))}，但未进入同平台同龄内容的相对高潜信号"
            )
        else:
            traffic_reason = "互动事实缺失，保留为热点线索，不据此评判账号质量"
        hypotheses = card.get("differentiation", {}).get("angle_hypotheses") or []
        card["qualification"] = {
            "status": "qualified" if eligible else "signal_only",
            "eligible_for_deep_read": eligible,
            "traffic_state": "qualified" if traffic_qualified else "insufficient",
            "traffic_reason": traffic_reason,
            "persona_state": "fit" if persona_qualified else "insufficient",
            "persona_reason": persona_reason,
            "angle_hypotheses": hypotheses,
            "angle_hypotheses_are_non_blocking": True,
            "recency_cohorts": sorted(cohorts),
            "traffic_comparison_contract": "same_platform_same_recency_cohort",
            "authenticity_state": (
                "official_with_time" if official_with_time else "not_established_by_official_time"
            ),
            "official_time_is_not_traffic_qualification": True,
            "relative_basis": {
                "platform_observation_counts": {
                    platform: sum(
                        len(value) for (pool_platform, _), value in likes_by_cohort.items()
                        if pool_platform == platform
                    )
                    for platform in sorted({key[0] for key in likes_by_cohort})
                },
                "platform_recency_observation_counts": {
                    f"{platform}:{cohort}": len(value)
                    for (platform, cohort), value in sorted(likes_by_cohort.items())
                },
                "strongest_like_percentile": round(strongest_percentile, 4),
            },
        }
        card["representative_source_ids"] = (
            select_representative_sources(card.get("sources", [])) if eligible else []
        )
        card["review_stage"] = "qualified_for_deep_read" if eligible else "signal_only"
    return cards


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
        event_id = _event_id(run_id, descriptor)
        label = _event_label(next(iter(deduped.values()))) or event_id
        representative_source = source_rows[0]
        fact_boundary = next(
            (
                row.get(key)
                for row in rows
                for key in ("fact_boundary", "fact_boundary_note", "事实边界")
                if row.get(key) not in (None, "", [], {})
            ),
            None,
        )
        cannot_claim = next(
            (
                row.get(key)
                for row in rows
                for key in ("cannot_claim", "cannot_claim_notes", "不能声称的部分")
                if row.get(key) not in (None, "", [], {})
            ),
            None,
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
            "fact_boundary": fact_boundary,
            "cannot_claim": cannot_claim,
            "source_count": len(source_rows),
            "sources": source_rows,
            "representative_source_ids": [],
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
    return qualify_hotspot_cards(cards)


def representative_candidates(
    cards: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_source = {_source_identity(row): row for row in candidates}
    output = []
    seen: set[str] = set()
    for card in cards:
        if not card.get("qualification", {}).get("eligible_for_deep_read"):
            continue
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
    failure_by_source = {
        _canonical_url(row.get("source_url")): _text(row.get("reason") or row.get("failure"))
        for row in failures
        if _canonical_url(row.get("source_url"))
    }
    understanding_results = []
    for card in cards:
        understood = []
        representative_set = set(card.get("representative_source_ids", []))
        for source in card["sources"]:
            package = package_by_source.get(source["url"])
            if package and package.get("status") in {"completed", "completed_with_failures"}:
                source["understanding_status"] = "analyzed"
                source["understanding_failure"] = ""
                understood.append(package)
            else:
                item_failure = (
                    failure_by_item.get(source.get("item_id", ""), "")
                    or failure_by_source.get(source.get("url", ""), "")
                )
                if item_failure:
                    source["understanding_status"] = "failed"
                    source["understanding_failure"] = item_failure
        representative_understood = [
            package for package in understood
            if _canonical_url(package.get("source_url")) in {
                source["url"] for source in card["sources"]
                if source["source_id"] in representative_set
            }
        ]
        requested = len(representative_set)
        completed = len(representative_understood)
        failed = sum(
            source["understanding_status"] == "failed"
            for source in card["sources"]
            if source["source_id"] in representative_set
        )
        attempted = completed + failed
        if not card.get("qualification", {}).get("eligible_for_deep_read"):
            deep_status = "not_qualified"
            deep_reason = "not_high_potential"
            card["review_stage"] = "signal_only"
        elif completed and failed:
            deep_status = "completed_with_failures"
            deep_reason = "representative_sources_partially_completed"
            card["review_stage"] = "ready_for_editorial"
        elif completed:
            deep_status = "completed"
            deep_reason = "representative_sources_completed"
            card["review_stage"] = "ready_for_editorial"
        elif attempted and failed:
            deep_status = "understanding_failed"
            deep_reason = "typed_representative_failure"
            card["review_stage"] = "understanding_failed"
        else:
            deep_status = "not_attempted"
            deep_reason = "no_supported_deep_read_attempt"
            card["review_stage"] = "signal_only"
        card["deep_read"] = {
            "requested_count": requested,
            "attempted_count": attempted,
            "completed_count": completed,
            "failed_count": failed,
            "status": deep_status,
            "reason": deep_reason,
            "information_gain_stop": True,
            "max_sources": MAX_REPRESENTATIVES,
        }
        card["cluster_synthesis"] = synthesize_card(card, representative_understood)
        if understood:
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
                    "available_packages": understood,
                    "cluster_synthesis": card["cluster_synthesis"],
                },
            })
    return cards, understanding_results


def deep_read_counts(cards: list[dict[str, Any]]) -> dict[str, int]:
    high_potential = [
        card for card in cards
        if card.get("qualification", {}).get("eligible_for_deep_read")
    ]
    attempted = [
        card for card in high_potential
        if int(card.get("deep_read", {}).get("attempted_count") or 0) > 0
    ]
    completed = [
        card for card in attempted
        if int(card.get("deep_read", {}).get("completed_count") or 0) > 0
    ]
    failed = [
        card for card in attempted
        if card.get("deep_read", {}).get("status") == "understanding_failed"
    ]
    summary = {
        "high_potential_total": len(high_potential),
        "deep_read_attempted_total": len(attempted),
        "deep_read_completed_total": len(completed),
        "deep_read_failed_total": len(failed),
        "editorial_candidate_total": len(editorial_candidates(cards)),
    }
    if summary["deep_read_completed_total"] + summary["deep_read_failed_total"] > summary["deep_read_attempted_total"]:
        raise ValueError("deep_read_count_conflict")
    if summary["editorial_candidate_total"] != summary["deep_read_completed_total"]:
        raise ValueError("editorial_deep_read_count_conflict")
    return summary


def synthesize_card(
    card: dict[str, Any],
    representative_packages: list[dict[str, Any]],
) -> dict[str, Any]:
    scenes: list[str] = []
    unresolved: list[str] = []
    representative_findings: list[dict[str, Any]] = []
    for package in representative_packages:
        captions = [
            _text(row.get("text"))
            for row in package.get("caption_timeline", []) or []
            if isinstance(row, dict) and _text(row.get("text"))
        ]
        asr = package.get("asr") if isinstance(package.get("asr"), dict) else {}
        asr_text = re.sub(r"<\|[^|]+\|>", "", _text(asr.get("text")))
        asr_sentences = [
            _text(value)[:220]
            for value in re.split(r"[\u3002！？!?]", asr_text)
            if len(_text(value)) >= 8
        ][:3]
        screen_facts = [
            _text(row.get("text") or row.get("value"))
            for row in package.get("screen_text", []) or []
            if isinstance(row, dict) and _text(row.get("text") or row.get("value"))
        ][:8]
        for text in captions[:3] + asr_sentences[:2] + screen_facts[:3]:
            if text and text not in scenes:
                scenes.append(text)
        for value in package.get("unresolved_terms", []) or []:
            normalized = _text(value)
            if normalized and normalized not in unresolved:
                unresolved.append(normalized)
        representative_findings.append({
            "source_url": _canonical_url(package.get("source_url")),
            "title": _text(package.get("title")),
            "caption_excerpts": captions[:3],
            "asr_excerpts": asr_sentences[:3],
            "screen_facts": screen_facts,
            "keyframe_count": len(package.get("keyframes", []) or []),
            "unresolved": [
                _text(value) for value in package.get("unresolved_terms", []) or []
                if _text(value)
            ],
            "failures": [
                _text(value) for value in package.get("failures", []) or []
                if _text(value)
            ],
        })
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
        "primary_angle": "",
        "alternative_angles": [],
        "pre_read_angle_hypotheses": differentiation.get("angle_hypotheses", []),
        "representative_findings": representative_findings,
        "actual_understanding_source_count": len(representative_findings),
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


def editorial_candidates(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        card for card in cards
        if card.get("review_stage") == "ready_for_editorial"
    ]


def validate_candidate_specific_decisions(
    topics: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> None:
    by_id = {str(row.get("candidate_id") or ""): row for row in candidates}
    reasons: set[str] = set()
    for topic in topics:
        identity = _text(topic.get("candidate_id"))
        candidate = by_id.get(identity)
        if candidate is None:
            raise ValueError("editorial_result_identity_conflict")
        reason = _text(topic.get("selection_reason"))
        normalized_reason = reason.casefold()
        if not reason:
            raise ValueError("editorial_candidate_reason_missing")
        if normalized_reason in reasons:
            raise ValueError("editorial_candidate_reason_reused")
        reasons.add(normalized_reason)
        source_ids = topic.get("evidence_source_ids")
        allowed_sources = {
            str(source.get("source_id") or "") for source in candidate.get("sources", [])
        }
        if not isinstance(source_ids, list) or not source_ids or any(
            str(value) not in allowed_sources for value in source_ids
        ):
            raise ValueError("editorial_candidate_evidence_source_invalid")
        basis = topic.get("decision_basis")
        if not isinstance(basis, dict) or any(
            not _text(basis.get(key))
            for key in ("traffic", "content", "persona", "differentiation")
        ):
            raise ValueError("editorial_candidate_basis_incomplete")
        if topic.get("decision") in {"select", "observe", "reject"}:
            angle = _text(topic.get("unique_judgment"))
            if not angle or any(phrase in angle for phrase in GENERIC_ANGLE_PHRASES):
                raise ValueError("editorial_primary_angle_not_concrete")
            if angle.casefold() == normalized_reason:
                raise ValueError("editorial_primary_angle_reason_not_distinct")


def complete_editorial_ledger(
    cards: list[dict[str, Any]],
    judged_topics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    judged = {str(row.get("candidate_id") or ""): row for row in judged_topics}
    output: list[dict[str, Any]] = []
    for card in cards:
        identity = str(card.get("candidate_id") or "")
        if identity in judged:
            row = json.loads(json.dumps(judged[identity], ensure_ascii=False))
            differentiation = json.loads(json.dumps(card.get("differentiation") or {}))
            cluster_synthesis = json.loads(json.dumps(card.get("cluster_synthesis") or {}))
            if row.get("decision") in {"select", "observe", "reject"}:
                primary_angle = _text(row.get("unique_judgment"))
                differentiation["primary_angle"] = primary_angle
                cluster_synthesis["primary_angle"] = primary_angle
            row["differentiation"] = differentiation
            row["cluster_synthesis"] = cluster_synthesis
            row["review_stage"] = (
                "recommended" if row.get("decision") == "select"
                else "observed_after_deep_read" if row.get("decision") == "observe"
                else "unsuitable" if row.get("decision") == "reject"
                else "understanding_failed"
            )
            output.append(row)
            continue
        review_stage = str(card.get("review_stage") or "signal_only")
        if review_stage == "understanding_failed":
            failed_sources = [
                source for source in card.get("sources", [])
                if source.get("understanding_status") == "failed"
            ]
            reason = "; ".join(
                _text(source.get("understanding_failure")) for source in failed_sources
                if _text(source.get("understanding_failure"))
            ) or "代表源理解失败，不使用历史或其他来源替代"
            decision = "failed"
            stage = "understanding_failed"
        else:
            qualification = card.get("qualification", {})
            reason = "; ".join(filter(None, [
                _text(qualification.get("traffic_reason")),
                _text(qualification.get("persona_reason")),
            ]))
            decision = "signal"
            stage = "signal_only"
        output.append({
            "candidate_id": identity,
            "decision": decision,
            "review_stage": stage,
            "title": _text(card.get("event_name") or card.get("title")),
            "hook": "",
            "structure": "",
            "selection_reason": reason,
            "unique_judgment": "",
            "evidence_source_ids": [],
            "decision_basis": {},
        })
    return output


def cards_json(cards: list[dict[str, Any]]) -> str:
    return json.dumps(cards, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

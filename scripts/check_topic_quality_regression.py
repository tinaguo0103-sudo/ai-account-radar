#!/usr/bin/env python3
"""Regression checks for human-readable 今日Top10 topic quality."""
from __future__ import annotations

import csv
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TODAY10 = ROOT / "output" / "today_10_topics.csv"
DEBUG = ROOT / "output" / "debug_today10_generation.csv"

FORBIDDEN_VISIBLE_TERMS = [
    "自查表", "少做一小时", "这类更新", "可执行动作", "业务动作", "业务验收清单",
    "别只看发布信息", "先看任务怎么验收", "该先判断", "最该重排",
]
VISIBLE_FIELDS = [
    "我的选题标题", "可发布标题", "标题备选", "推荐理由", "我的蹭热点角度",
    "选题标题", "内部切入角度",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"missing {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def contains_any(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if term in (text or "")]


def intish(value: str) -> int:
    try:
        return int(float(value or 0))
    except ValueError:
        return 0


def main() -> int:
    rows = read_csv(TODAY10)
    debug_rows = read_csv(DEBUG)
    failures: list[str] = []

    for idx, row in enumerate(rows, start=1):
        visible_text = "\n".join(row.get(field, "") for field in VISIBLE_FIELDS)
        hits = contains_any(visible_text, FORBIDDEN_VISIBLE_TERMS)
        if hits:
            failures.append(f"row {idx}: visible fields contain forbidden template terms: {','.join(hits)}")
        if row.get("推荐动作") in {"暂存观察", "不做"} or row.get("今日建议级别") in {"暂存观察", "不建议制作"}:
            if row.get("可发布标题", "").strip() or row.get("标题备选", "").strip():
                failures.append(f"row {idx}: {row.get('今日建议级别')} still has publishable title/options")
        if row.get("AI味风险") == "低" and hits:
            failures.append(f"row {idx}: AI味风险低 but template terms present")
        if intish(row.get("标题质量分", "")) >= 85 and not (row.get("可发布标题") or row.get("来源内容") or "")[:4]:
            failures.append(f"row {idx}: high title score without concrete source/title")
        if row.get("来源类型") == "对标视频" and "抖音" in row.get("内容可信度", ""):
            deep_terms = ["口播全文", "评论区", "镜头结构", "完整视频"]
            if contains_any(visible_text, deep_terms):
                failures.append(f"row {idx}: douyin shallow item overclaims deep video analysis")
        if row.get("来源类型") == "公众号文章" and row.get("内容可信度") != "全文":
            if row.get("推荐动作") in {"进入Brief", "本周做"}:
                failures.append(f"row {idx}: non-full article promoted as deep work")

        title_blob = "\n".join([row.get("来源内容", ""), row.get("我的选题标题", ""), row.get("内部切入角度", ""), row.get("可发布标题", "")])
        known_bad_pairs = [
            ("MiniCPM", "AI假人带货"),
            ("AccountingLLM", "AI假人带货"),
            ("Arena", "Claude自助数据分析"),
            ("Google Colab", "Claude Code"),
            ("Suno", "AI视频模型更新后"),
            ("Gemini Live", "AI视频模型更新后"),
        ]
        for source_term, wrong_term in known_bad_pairs:
            if source_term in row.get("来源内容", "") and wrong_term in title_blob:
                failures.append(f"row {idx}: source {source_term} mapped to wrong term {wrong_term}")

    top_count = sum(1 for row in rows if row.get("今日建议级别") == "今日最值得做")
    if top_count > 3:
        failures.append(f"今日最值得做 count > 3: {top_count}")

    for idx, row in enumerate(debug_rows, start=1):
        if row.get("是否超过解析文本支撑范围") == "是" and row.get("今日建议级别") == "今日最值得做":
            failures.append(f"debug row {idx}: unsupported item marked 今日最值得做")
        if row.get("模板词命中情况") not in {"", "无"} and row.get("AI味风险") == "低":
            failures.append(f"debug row {idx}: template hit but AI味风险低")

    if failures:
        print("Topic quality regression failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"Topic quality regression passed: {len(rows)} top rows, {len(debug_rows)} debug rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

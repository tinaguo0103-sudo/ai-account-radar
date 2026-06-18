#!/usr/bin/env python3
"""Regression checks for human-readable daily topic candidate quality."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TODAY10 = ROOT / "output" / "today_10_topics.csv"
DEBUG = ROOT / "output" / "debug_today10_generation.csv"
LATEST_TODAY10 = ROOT / "output" / "latest" / "today_10_topics.csv"
LATEST_DEBUG = ROOT / "output" / "latest" / "debug_today10_generation.csv"
LATEST_SKILL_REPORT = ROOT / "output" / "latest" / "editorial_skill_report.json"

FORBIDDEN_VISIBLE_TERMS = [
    "自查表", "少做一小时", "这类更新", "可执行动作", "业务动作", "业务验收清单",
    "别只看发布信息", "先看任务怎么验收", "该先判断", "最该重排",
    "适合拆成一次真实任务边界测试", "适合拆成一次AI视频交付测试",
    "不该只看工具名", "只有在能说清具体产品层",
    "这条视频", "这条内容", "对标视频真正", "博主", "老师", "带着它的", "玛卡巴卡",
]
VISIBLE_FIELDS = [
    "我的选题标题", "可发布标题", "标题备选", "推荐理由", "我的蹭热点角度",
    "选题标题", "内部切入角度",
]
ALLOWED_LEVELS = {"今日最值得做", "可选候选", "暂存观察", "不建议制作"}


def compact_text(value: str) -> str:
    import re

    return re.sub(r"[\s\W_]+", "", (value or "").lower())


def same_as_source(title: str, row: dict[str, str]) -> bool:
    normalized = compact_text(title)
    if not normalized:
        return False
    for field in ["原始来源标题", "来源内容", "来源标题"]:
        source = compact_text(row.get(field, ""))
        if source and source == normalized:
            return True
    return False


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


def skill_report_for(today10_path: Path) -> dict[str, str]:
    candidates = [
        today10_path.with_name("editorial_skill_report.json"),
        LATEST_SKILL_REPORT,
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        return {str(k): str(v) for k, v in payload.items() if isinstance(v, (str, int, float, bool))}
    return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="", help="Path to today candidate CSV. Defaults to output/latest/today_10_topics.csv, then legacy output/today_10_topics.csv.")
    parser.add_argument("--debug", default="", help="Path to debug CSV. Defaults to output/latest/debug_today10_generation.csv, then legacy output/debug_today10_generation.csv.")
    args = parser.parse_args()
    today10_path = Path(args.input) if args.input else (LATEST_TODAY10 if LATEST_TODAY10.exists() else TODAY10)
    debug_path = Path(args.debug) if args.debug else (LATEST_DEBUG if LATEST_DEBUG.exists() else DEBUG)
    rows = read_csv(today10_path)
    debug_rows = read_csv(debug_path)
    skill_report = skill_report_for(today10_path)
    skill_mode = skill_report.get("engine") == "codex"
    failures: list[str] = []
    warnings: list[str] = []
    debug_by_fp = {row.get("内容指纹", ""): row for row in debug_rows if row.get("内容指纹", "")}

    for idx, row in enumerate(rows, start=1):
        if row.get("今日建议级别") not in ALLOWED_LEVELS:
            failures.append(f"row {idx}: non-standard 今日建议级别: {row.get('今日建议级别')!r}")
        visible_text = "\n".join(row.get(field, "") for field in VISIBLE_FIELDS)
        hits = contains_any(visible_text, FORBIDDEN_VISIBLE_TERMS)
        if hits:
            failures.append(f"row {idx}: visible fields contain forbidden template terms: {','.join(hits)}")
        if same_as_source(row.get("可发布标题", ""), row):
            failures.append(f"row {idx}: publishable title equals original source title")
        if row.get("今日建议级别") in {"今日最值得做", "可选候选"} and not row.get("可发布标题", "").strip():
            failures.append(f"row {idx}: {row.get('今日建议级别')} has no rewritten publishable title")
        if row.get("推荐动作") in {"暂存观察", "不做"} or row.get("今日建议级别") in {"暂存观察", "不建议制作"}:
            if row.get("可发布标题", "").strip() or row.get("标题备选", "").strip():
                failures.append(f"row {idx}: {row.get('今日建议级别')} still has publishable title/options")
        if row.get("AI味风险") == "低" and hits:
            failures.append(f"row {idx}: AI味风险低 but template terms present")
        if row.get("今日建议级别") == "今日最值得做":
            if row.get("AI味风险") == "高":
                failures.append(f"row {idx}: 今日最值得做 has high AI risk")
            if not row.get("可发布标题", "").strip():
                failures.append(f"row {idx}: 今日最值得做 has no publishable title")
            if intish(row.get("标题质量分", "")) < 72 or intish(row.get("编辑判断分", "")) < 78:
                failures.append(f"row {idx}: 今日最值得做 has low judgement/title score")
        if row.get("今日建议级别") == "不建议制作" and not skill_mode:
            failures.append(f"row {idx}: 不建议制作 should not enter 今日候选池")
        if intish(row.get("标题质量分", "")) >= 85 and not (row.get("可发布标题") or row.get("来源内容") or "")[:4]:
            failures.append(f"row {idx}: high title score without concrete source/title")
        if row.get("来源类型") == "对标视频" and "抖音" in row.get("内容可信度", ""):
            deep_terms = ["口播全文", "评论区", "镜头结构", "完整视频", "分镜流程", "交付清单"]
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
        fp = row.get("内容指纹", "")
        debug = debug_by_fp.get(fp)
        if debug and not skill_mode:
            for field in ["今日建议级别", "推荐动作", "是否建议进入制作", "编辑判断分", "标题质量分", "AI味风险", "可发布标题", "标题备选", "不建议做的原因", "主编判断"]:
                if (row.get(field, "") or "") != (debug.get(field, "") or ""):
                    failures.append(f"row {idx}: field mismatch with debug for {field}")

    top_count = sum(1 for row in rows if row.get("今日建议级别") == "今日最值得做")
    if top_count > 3:
        failures.append(f"今日最值得做 count > 3: {top_count}")
    watch_count = sum(1 for row in rows if row.get("今日建议级别") == "暂存观察")
    selected_sources = {row.get("来源内容", "") for row in rows}
    selected_fps = {row.get("内容指纹", "") for row in rows}
    selected_debug_final_titles = {
        row.get("最终选题标题", "")
        for row in debug_rows
        if row.get("是否进入候选池") == "是" and row.get("最终选题标题", "")
    }
    better_unselected = [
        row for row in debug_rows
        if row.get("是否进入候选池") != "是"
        and row.get("内容指纹", "") not in selected_fps
        and row.get("原始来源标题", "") not in selected_sources
        and row.get("最终选题标题", "") not in selected_debug_final_titles
        and row.get("是否建议进入制作") == "是"
        and row.get("AI味风险") == "低"
        and intish(row.get("编辑判断分", "")) >= 78
        and intish(row.get("标题质量分", "")) >= 72
    ]
    if not skill_mode and watch_count > 5 and better_unselected:
        failures.append(f"候选池 has {watch_count} 暂存观察 while {len(better_unselected)} better production-ready candidates are unselected")

    weakest_selected_watch = min(
        [intish(row.get("编辑判断分", "")) for row in rows if row.get("今日建议级别") == "暂存观察"],
        default=101,
    )
    if not skill_mode:
        for row in better_unselected:
            if intish(row.get("编辑判断分", "")) > weakest_selected_watch:
                warnings.append(f"unselected better candidate: {row.get('原始来源标题', '')[:80]}")
                if row.get("内容指纹", "") not in selected_fps and row.get("原始来源标题", "") not in selected_sources:
                    failures.append(f"better candidate unselected while weaker watch item selected: {row.get('原始来源标题', '')[:80]}")
                    break

    if not skill_mode:
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
    for warning in warnings:
        print(f"WARNING: {warning}")
    engine_note = "codex skill" if skill_mode else "deterministic/debug"
    print(f"Topic quality regression passed: {len(rows)} candidate rows, {len(debug_rows)} debug rows, mode={engine_note}")
    print(f"checked today10={today10_path}")
    print(f"checked debug={debug_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
LATEST_WRITE_TODAY10 = ROOT / "output" / "latest_write" / "today_10_topics.csv"
LATEST_WRITE_DEBUG = ROOT / "output" / "latest_write" / "debug_today10_generation.csv"
LATEST_WRITE_SKILL_REPORT = ROOT / "output" / "latest_write" / "editorial_skill_report.json"

FORBIDDEN_VISIBLE_TERMS = [
    "自查表", "少做一小时", "这类更新", "可执行动作", "业务动作", "业务验收清单",
    "别只看发布信息", "先看任务怎么验收", "该先判断", "最该重排",
    "适合拆成一次真实任务边界测试", "适合拆成一次AI视频交付测试",
    "不该只看工具名", "只有在能说清具体产品层",
    "这条视频", "这条内容", "对标视频真正", "博主", "老师", "带着它的", "玛卡巴卡",
    "用户当前", "用户自己的", "用户作为", "适合用户", "帮助用户", "用户可以", "用户会", "用户要",
]
FORBIDDEN_TITLE_TERMS = [
    "字段表",
    "交付QA",
    "验收记录的目录",
    "素材风险清单",
    "选题复核点",
    "要证明的是",
    "先交出",
    "写进执行台",
    "别急着夸",
    "不稀奇",
    "我最怕",
    "听起来很美",
    "别再给Agent起名字",
]
VISIBLE_FIELDS = [
    "选题命题", "我要做的实验", "热点触发点", "我的工作流痛点", "我的选题标题", "可发布标题", "标题备选", "title_permission", "主编筛选", "主编自由稿", "标题工作坊", "标题自审", "点击钩子", "观众为什么会点", "我的真实矛盾", "选题判断", "原始钩子", "我的切入", "我准备怎么讲", "可展示证据",
    "推荐理由", "我的蹭热点角度",
    "选题标题", "内部切入角度", "旧流程痛点", "AI介入点", "验证方式", "可沉淀资产",
]
ALLOWED_LEVELS = {"今日最值得做", "可选候选", "暂存观察", "不建议制作"}
EXPERIMENT_ACTION_TERMS = [
    "测试", "验证", "改造", "压缩", "录成", "接进", "变成", "写回", "沉淀",
    "做成", "复用", "拆成", "跑一轮", "对比", "进入", "重写", "少掉",
    "选择", "选", "记录", "导出", "输出", "标出", "检查", "统计", "回填",
]
PROPOSITION_OVERLOAD_TERMS = ["旧流程", "AI介入", "验证方式", "需要补", "还缺", "我要证明", "可沉淀"]
WEAK_VALIDATION_PHRASES = [
    "检查是否可用",
    "看是否可用",
    "判断是否可用",
    "对比旧流程、新流程、人工修正点",
    "能否沉淀到",
    "验证是否成立",
    "检查能不能",
]
GENERIC_ASSET_PACKS = [
    "Workflow SOP / 字段规则 / Brief 模板 / 飞书任务检查表",
    "Workflow SOP/字段规则/Brief 模板/飞书任务检查表",
    "导演工作流 SOP / 分镜验收表 / 成片 QA 清单",
    "内容资产流 SOP / 发布前后素材清单 / 复盘模板",
    "项目验收清单 / 复盘模板 / 异常处理记录",
]
GENERIC_ASSET_TERMS = ["通用", "资产包", "模板包", "方法论", "闭环", "待补具体资产"]
GENERIC_ASSET_VALUES = {
    "主编Skill",
    "输入字段",
    "输出字段",
    "飞书字段",
    "品牌规则",
    "视觉规则",
    "字体规则",
    "案例规则",
    "失败样例",
    "人工确认点",
    "状态",
    "输入一条候选内容",
    "再跑一条候选检查",
    "按五段任务表",
    "检查结果能不能写回飞书任务单",
    "跑完后写回飞书任务单",
    "就进入封面Skill",
}
ASSET_NOISE_PHRASES = ["输入一条", "再跑一条", "按五段", "能不能", "是否", "如果", "若", "检查结果", "跑完后", "就进入", "不进入", "就判定"]


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


def validation_is_executable(text: str) -> bool:
    value = text or ""
    if not value:
        return False
    if any(phrase in value for phrase in WEAK_VALIDATION_PHRASES):
        return False
    action_hits = contains_any(value, EXPERIMENT_ACTION_TERMS)
    has_concrete_marker = any(marker in value for marker in ["1", "2", "3", "一次", "一条", "一个", "分钟", "截图", "字段", "表", "记录", "输出", "导出", "通过", "失败", "少于", "大于", "小于"])
    return bool(action_hits and has_concrete_marker)


def asset_is_specific(text: str) -> bool:
    value = " ".join((text or "").split())
    if not value:
        return False
    if value in GENERIC_ASSET_PACKS:
        return False
    if any(term in value for term in GENERIC_ASSET_TERMS):
        return False
    assets = [part.strip() for part in value.replace("、", "/").split("/") if part.strip()]
    if not assets:
        return False
    concrete_assets = [
        asset for asset in assets
        if asset not in GENERIC_ASSET_VALUES
        and not any(phrase in asset for phrase in ASSET_NOISE_PHRASES)
    ]
    return any(any(key in asset for key in ["表", "清单", "规则", "Skill", "记录", "模板", "检查", "截图", "案例库", "流程图", "QA", "字段", "对比"]) for asset in concrete_assets)


def contains_unqualified_any(text: str, terms: list[str]) -> list[str]:
    """Find terms unless the sentence is explicitly saying the evidence is missing."""
    value = text or ""
    hits: list[str] = []
    negations = ["不能声称", "不能直接声称", "不能说", "不能证明", "不能假装", "不能展示", "不能当成", "没有", "未拿到", "没拿到", "缺少", "不含", "不是"]
    for term in terms:
        start = value.find(term)
        if start < 0:
            continue
        window = value[max(0, start - 32): start + len(term)]
        if any(neg in window for neg in negations):
            continue
        hits.append(term)
    return hits


def intish(value: str) -> int:
    try:
        return int(float(value or 0))
    except ValueError:
        return 0


def skill_report_for(today10_path: Path) -> dict[str, str]:
    candidates = [
        today10_path.with_name("editorial_skill_report.json"),
        LATEST_WRITE_SKILL_REPORT,
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
    today10_path = Path(args.input) if args.input else (LATEST_WRITE_TODAY10 if LATEST_WRITE_TODAY10.exists() else (LATEST_TODAY10 if LATEST_TODAY10.exists() else TODAY10))
    debug_path = Path(args.debug) if args.debug else (LATEST_WRITE_DEBUG if LATEST_WRITE_DEBUG.exists() else (LATEST_DEBUG if LATEST_DEBUG.exists() else DEBUG))
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
        title_hits = contains_any(row.get("可发布标题", ""), FORBIDDEN_TITLE_TERMS)
        if title_hits:
            failures.append(f"row {idx}: publishable title still uses internal/AI-ish terms: {','.join(title_hits)}")
        if same_as_source(row.get("可发布标题", ""), row):
            failures.append(f"row {idx}: publishable title equals original source title")
        if row.get("今日建议级别") in {"今日最值得做", "可选候选"} and not row.get("可发布标题", "").strip():
            if row.get("title_permission") == "可发布标题":
                failures.append(f"row {idx}: {row.get('今日建议级别')} has no rewritten publishable title despite title_permission=可发布标题")
        if row.get("今日建议级别") in {"今日最值得做", "可选候选"}:
            if not row.get("选题命题", "").strip():
                failures.append(f"row {idx}: {row.get('今日建议级别')} missing 选题命题")
            if len(row.get("选题命题", "").strip()) > 90:
                failures.append(f"row {idx}: 选题命题 too long (>90 chars)")
            overload = contains_any(row.get("选题命题", ""), PROPOSITION_OVERLOAD_TERMS)
            if overload:
                failures.append(f"row {idx}: 选题命题 contains full-card/decomposition terms: {','.join(overload)}")
            if row.get("选题标题", "").strip() and row.get("选题命题", "").strip() and row.get("选题标题", "").strip() != row.get("选题命题", "").strip():
                failures.append(f"row {idx}: 选题标题 should mirror 选题命题, not publishable title")
            for field in ["我要做的实验", "热点触发点", "我的工作流痛点", "旧流程痛点", "AI介入点", "验证方式", "可沉淀资产"]:
                if not row.get(field, "").strip():
                    failures.append(f"row {idx}: {row.get('今日建议级别')} missing workflow-experiment field {field}")
            if row.get("我要做的实验") and not contains_any(row.get("我要做的实验", ""), EXPERIMENT_ACTION_TERMS):
                failures.append(f"row {idx}: 我要做的实验 lacks a concrete experiment action")
            if row.get("验证方式") and not validation_is_executable(row.get("验证方式", "")):
                failures.append(f"row {idx}: 验证方式 is not an executable minimal experiment")
            if row.get("可沉淀资产") and not asset_is_specific(row.get("可沉淀资产", "")):
                failures.append(f"row {idx}: 可沉淀资产 is too generic")
            for field in ["主编筛选", "主编自由稿"]:
                if not row.get(field, "").strip():
                    failures.append(f"row {idx}: {row.get('今日建议级别')} missing gate editorial field {field}")
            for field in ["点击钩子", "观众为什么会点"]:
                if not row.get(field, "").strip():
                    failures.append(f"row {idx}: {row.get('今日建议级别')} missing click-hook field {field}")
            for field in ["我的真实矛盾", "选题判断", "原始钩子", "我的切入", "我准备怎么讲", "可展示证据"]:
                if not row.get(field, "").strip():
                    failures.append(f"row {idx}: {row.get('今日建议级别')} missing proposal-card field {field}")
            tension = row.get("我的真实矛盾", "")
            if any(term in tension for term in ["来源摘要", "栏目", "我会把"]):
                failures.append(f"row {idx}: 我的真实矛盾 still looks like classification, not lived conflict")
        if row.get("推荐动作") in {"暂存观察", "不做"} or row.get("今日建议级别") in {"暂存观察", "不建议制作"}:
            if row.get("可发布标题", "").strip() or row.get("标题备选", "").strip():
                failures.append(f"row {idx}: {row.get('今日建议级别')} still has publishable title/options")
        if row.get("title_permission") != "可发布标题":
            if row.get("可发布标题", "").strip() or row.get("标题备选", "").strip():
                failures.append(f"row {idx}: title_permission={row.get('title_permission')!r} still has publishable title/options")
        if row.get("今日建议级别") in {"暂存观察", "不建议制作"} and row.get("title_permission") == "可发布标题":
            failures.append(f"row {idx}: {row.get('今日建议级别')} cannot have title_permission=可发布标题")
        if row.get("今日建议级别") == "今日最值得做":
            if row.get("证据强度") == "弱":
                failures.append(f"row {idx}: 今日最值得做 has weak evidence")
            if row.get("场景依据") == "仅热点观察":
                failures.append(f"row {idx}: 今日最值得做 is only hotspot observation")
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
            if contains_unqualified_any(visible_text, deep_terms):
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
    prop_prefix_counts: dict[str, int] = {}
    for row in rows:
        prop = row.get("选题命题", "").strip()
        if prop:
            prefix = prop[:4]
            prop_prefix_counts[prefix] = prop_prefix_counts.get(prefix, 0) + 1
    repeated_prefixes = [prefix for prefix, count in prop_prefix_counts.items() if count > 2 and prefix in {"我准备", "我想用", "用这次", "把这个", "我会把"}]
    if repeated_prefixes:
        failures.append(f"选题命题 repeated mechanical prefix too often: {','.join(repeated_prefixes)}")
    visible_assets = [
        row.get("可沉淀资产", "").strip()
        for row in rows
        if row.get("今日建议级别") in {"今日最值得做", "可选候选"} and row.get("可沉淀资产", "").strip()
    ]
    repeated_assets = sorted({asset for asset in visible_assets if visible_assets.count(asset) > 2})
    if repeated_assets:
        failures.append(f"可沉淀资产 repeated too often across visible candidates: {repeated_assets[:3]}")
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

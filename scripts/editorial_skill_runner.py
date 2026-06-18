#!/usr/bin/env python3
"""Run the global ai-account-editorial-director Skill on topic candidates.

The collection pipeline still handles source capture, normalization, dedupe, and
rough candidate generation. This runner is the editorial layer: by default it
loads the global Skill and its persona/case reference, asks the locally
authenticated Codex CLI to make the batch judgement, and writes the Skill output
contract back to the candidate CSV.

`--engine deterministic` is kept only as an explicit emergency fallback for
offline debugging. It is not the default path.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from local_env import load_local_env


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = Path.home() / ".codex" / "skills" / "ai-account-editorial-director"
SKILL_MD = SKILL_DIR / "SKILL.md"
SKILL_REFERENCE = SKILL_DIR / "references" / "persona-and-cases.md"

EXTRA_FIELDS = [
    "候选状态",
    "推荐等级",
    "对应方向",
    "一句话Brief",
    "我的场景拆解",
    "我的思考点",
    "重点体现",
    "可调用案例",
    "内容核心冲突",
    "视频呈现方式",
    "证据强度",
    "Skill编辑层",
    "Skill参考文件",
]

SKILL_FIELDS = [
    "候选状态",
    "推荐等级",
    "可发布标题",
    "标题备选",
    "对应方向",
    "一句话Brief",
    "我的场景拆解",
    "我的思考点",
    "重点体现",
    "可调用案例",
    "内容核心冲突",
    "视频呈现方式",
    "证据强度",
    "推荐动作",
    "不建议做的原因",
    "推荐理由",
    "主编判断",
    "今日建议级别",
    "是否建议进入制作",
    "编辑判断分",
    "标题质量分",
    "AI味风险",
]

CANDIDATE_CONTEXT_FIELDS = [
    "我的选题标题",
    "可发布标题",
    "内部切入角度",
    "来源内容",
    "来源类型",
    "原始来源标题",
    "来源链接",
    "对应栏目",
    "热点切入方式",
    "业务场景",
    "旧流程痛点",
    "AI介入点",
    "可展示结果",
    "可沉淀资产",
    "推荐理由",
    "推荐动作",
    "推荐分",
    "内容可信度",
    "是否有足够内容支撑",
    "真实用户问题",
    "为什么今天值得做",
    "我能讲出的独特角度",
    "我的账号为什么能讲",
    "是否只是资讯搬运",
    "不建议做的原因",
    "人设匹配分",
    "编辑判断分",
    "标题质量分",
    "AI味风险",
    "今日建议级别",
    "相关来源",
    "事件锚点",
    "业务变化判断",
    "候选来源方式",
    "内容指纹",
]

DIRECTION_ALIASES = {
    "AI汽车与品牌增长": "汽车与内容营销",
    "AI导演工作流与视频交付": "AI导演工作流",
    "内容团队选题到Brief流程": "真实工作流改造",
    "Agent任务验收": "真实工作流改造",
}

CASE_RULES = [
    (
        ("分镜", "镜头", "AI视频", "短剧", "成片", "导演", "Runway", "Kling", "Luma", "Seedance", "视频模型"),
        "Neurovia AI全球宣传片导演工作流 / Austin AIGC商业视频交付Skill",
    ),
    (
        ("封面", "首图", "卡片", "小红书", "长文", "图文", "视觉物料", "公众号"),
        "Social Media Cover封面自动化Skill / 公众号长文转小红书图文卡片",
    ),
    (
        ("飞书", "选题", "Brief", "内容收件箱", "信息雷达", "AIHOT", "候选池"),
        "从全网AI热点到飞书选题台",
    ),
    (
        ("Agent", "Claude", "Codex", "MCP", "自动化", "项目", "验收", "生产环境", "工作流"),
        "从全网AI热点到飞书选题台 / RunBY AI CMO Agent",
    ),
    (
        ("PPT", "方案", "汇报", "页面", "商业表达"),
        "RunBY AI CMO Agent / MuseIn产品化与出海传播判断",
    ),
    (
        ("汽车", "车企", "车主", "高管IP", "发布会", "品牌", "营销", "传播", "信任"),
        "电车奥利奥与车企内容营销场景 / RunBY AI CMO Agent",
    ),
    (
        ("产品化", "出海", "GTM", "社区", "模板", "MuseIn"),
        "MuseIn产品化与出海传播判断",
    ),
]


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def atomic_write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", newline="", delete=False, dir=str(path.parent)) as handle:
        tmp = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def intish(value: Any) -> int:
    try:
        return int(float(str(value or 0)))
    except ValueError:
        return 0


def blob(row: dict[str, str]) -> str:
    return "\n".join(str(row.get(key, "")) for key in [
        "来源内容", "可发布标题", "内部切入角度", "我的蹭热点角度", "业务场景",
        "旧流程痛点", "AI介入点", "可展示结果", "可沉淀资产", "推荐理由",
    ])


def normalize_direction(value: str) -> str:
    value = (value or "").strip()
    value = DIRECTION_ALIASES.get(value, value)
    if value in {"AI业务定调", "真实工作流改造", "AI导演工作流", "汽车与内容营销", "AI项目复盘"}:
        return value
    return "真实工作流改造"


def grade(row: dict[str, str]) -> str:
    score = intish(row.get("编辑判断分") or row.get("推荐分"))
    level = row.get("今日建议级别", "")
    if level == "今日最值得做" or score >= 90:
        return "S"
    if level == "可选候选" or score >= 78:
        return "A"
    if level == "暂存观察" or score >= 68:
        return "B"
    return "C"


def evidence_strength(row: dict[str, str]) -> str:
    credibility = row.get("内容可信度", "")
    support = row.get("是否有足够内容支撑", "")
    if credibility == "全文" or support == "足够":
        return "强"
    if credibility in {"AIHOT摘要", "摘要", "摘要可用", "抖音浅层", "抖音转写"} or support in {"摘要可用", "浅层"}:
        return "中"
    return "弱"


def callable_case(row: dict[str, str]) -> str:
    text = blob(row)
    for terms, case in CASE_RULES:
        if any(term.lower() in text.lower() for term in terms):
            return case
    direction = normalize_direction(row.get("对应栏目", ""))
    fallback = {
        "AI业务定调": "RunBY AI CMO Agent / MuseIn产品化与出海传播判断",
        "真实工作流改造": "从全网AI热点到飞书选题台",
        "AI导演工作流": "Neurovia AI全球宣传片导演工作流",
        "汽车与内容营销": "电车奥利奥与车企内容营销场景",
        "AI项目复盘": "Austin AIGC商业视频交付Skill",
    }
    return fallback.get(direction, "从全网AI热点到飞书选题台")


def scene_breakdown(row: dict[str, str]) -> str:
    scene = row.get("业务场景") or normalize_direction(row.get("对应栏目", ""))
    pain = row.get("旧流程痛点", "")
    intervention = row.get("AI介入点", "")
    result = row.get("可展示结果", "")
    if pain and intervention and result:
        return f"我会把它接到「{scene}」：旧流程卡在{pain}；AI介入点是{intervention}；最后要展示{result}。"
    angle = row.get("我的蹭热点角度") or row.get("我能讲出的独特角度") or row.get("内部切入角度")
    return f"我会把它接到「{scene}」里讲，不停留在来源事件本身，而是拆它如何进入我的内容生产、交付或业务判断。{angle}".strip()


def thinking_point(row: dict[str, str]) -> str:
    direction = normalize_direction(row.get("对应栏目", ""))
    source = row.get("来源内容", "")
    if direction == "AI导演工作流":
        return "我不只看它能不能生成，而是看它能不能进入分镜、资产、镜头、返修和验收这条导演式交付链。"
    if direction == "汽车与内容营销":
        return "我会把它放进品牌传播、车主运营或内容资产流里，看 AI 改的是哪段人力密集流程。"
    if direction == "AI业务定调":
        return "我会先判断这个变化是否真的改变业务现场，而不是把它当成一条 AI 新闻复述。"
    if direction == "AI项目复盘":
        return "我会用项目复盘方式讲它：需求、执行、异常、验收和资产沉淀哪一步被改变。"
    if "抖音" in row.get("来源类型", "") or "对标视频" in row.get("来源类型", ""):
        return "我会吸收它的选题承诺和结构，但标题和表达要转成自己的生产现场，不露出对标账号。"
    if source:
        return "我会从来源里抽出一个真实流程问题，再用自己的项目经验判断它值不值得推进。"
    return "我会先问：它能不能改造一个真实流程，能不能展示结果，能不能沉淀资产。"


def key_emphasis(row: dict[str, str]) -> str:
    asset = row.get("可沉淀资产", "")
    result = row.get("可展示结果", "")
    case = callable_case(row)
    if asset and result:
        return f"重点体现：不是讲来源多热，而是展示{result}，并沉淀成{asset}。可调用案例：{case}。"
    if asset:
        return f"重点体现：把这条内容变成可复用资产：{asset}。可调用案例：{case}。"
    return f"重点体现：用真实案例证明 AI 进入流程后的变化，而不是停在工具介绍。可调用案例：{case}。"


def core_conflict(row: dict[str, str]) -> str:
    direction = normalize_direction(row.get("对应栏目", ""))
    if direction == "AI导演工作流":
        return "漂亮生成片段 vs 可交付成片；工具演示 vs 导演式执行工作流。"
    if direction == "汽车与内容营销":
        return "传统人力密集传播流程 vs AI Native 内容资产流。"
    if direction == "AI业务定调":
        return "AI资讯复述 vs 业务现场判断。"
    if direction == "AI项目复盘":
        return "功能能跑 vs 项目可验收、可复用、可交付。"
    return "旧流程靠人肉搬运 vs AI把判断、流程、资产和结果重新编排。"


def presentation(row: dict[str, str]) -> str:
    direction = normalize_direction(row.get("对应栏目", ""))
    if direction == "AI导演工作流":
        return "口播 + 分镜/镜头/成片对比 + 返修或验收画面"
    if direction == "汽车与内容营销":
        return "口播 + 发布前后内容资产流流程图 + 车企/品牌场景拆解"
    if direction == "AI业务定调":
        return "口播短评 + 业务影响三段式 + 自己系统里的验证点"
    if direction == "AI项目复盘":
        return "项目复盘 + 飞书/流程图/验收字段展示"
    return "口播 + 屏幕录制 + 旧流程/新流程对比图"


def one_sentence_brief(row: dict[str, str]) -> str:
    title = row.get("可发布标题") or row.get("我的选题标题") or row.get("来源内容", "")
    scene = row.get("业务场景") or normalize_direction(row.get("对应栏目", ""))
    asset = row.get("可沉淀资产", "")
    if asset:
        return f"用「{scene}」这个真实场景，把 {title} 拆成一个能沉淀为「{asset}」的流程判断。"
    return f"用「{scene}」这个真实场景，判断 {title} 能不能变成我的业务现场选题。"


def enrich(row: dict[str, str]) -> dict[str, str]:
    direction = normalize_direction(row.get("对应栏目", ""))
    out = dict(row)
    out["候选状态"] = row.get("今日建议级别") or row.get("是否建议进入制作") or "暂存观察"
    out["推荐等级"] = grade(row)
    out["对应方向"] = direction
    out["一句话Brief"] = row.get("一句话Brief") or one_sentence_brief(out)
    out["我的场景拆解"] = row.get("我的场景拆解") or scene_breakdown(out)
    out["我的思考点"] = row.get("我的思考点") or thinking_point(out)
    out["重点体现"] = row.get("重点体现") or key_emphasis(out)
    out["可调用案例"] = row.get("可调用案例") or callable_case(out)
    out["内容核心冲突"] = row.get("内容核心冲突") or core_conflict(out)
    out["视频呈现方式"] = row.get("视频呈现方式") or presentation(out)
    out["证据强度"] = row.get("证据强度") or evidence_strength(out)
    out["Skill编辑层"] = "ai-account-editorial-director"
    out["Skill参考文件"] = str(SKILL_REFERENCE)
    return out


def fieldnames_for(rows: list[dict[str, str]], original: list[str]) -> list[str]:
    names = list(original)
    for field in EXTRA_FIELDS:
        if field not in names:
            names.append(field)
    for row in rows:
        for key in row:
            if key not in names:
                names.append(key)
    return names


def compact_candidate(row: dict[str, str], index: int) -> dict[str, str | int]:
    payload: dict[str, str | int] = {"index": index}
    for field in CANDIDATE_CONTEXT_FIELDS:
        value = row.get(field, "")
        if value:
            payload[field] = value[:1800]
    return payload


def load_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing required Skill file: {path}")
    return path.read_text(encoding="utf-8")


def codex_output_schema() -> dict[str, Any]:
    row_properties: dict[str, Any] = {"index": {"type": "integer", "minimum": 0}}
    for field in SKILL_FIELDS:
        row_properties[field] = {"type": "string"}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "engine": {"type": "string"},
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": row_properties,
                    "required": ["index", *SKILL_FIELDS],
                },
            },
            "batch_notes": {"type": "string"},
        },
        "required": ["engine", "rows", "batch_notes"],
    }


def build_codex_prompt(rows: list[dict[str, str]]) -> str:
    skill_text = load_text(SKILL_MD)
    reference_text = load_text(SKILL_REFERENCE)
    candidates = [compact_candidate(row, idx) for idx, row in enumerate(rows)]
    return f"""你现在必须使用全局 Skill `ai-account-editorial-director` 做主编判断。

这是一次生产管线里的批量选题筛选，不是泛泛润色。请直接基于下面的 Skill 和案例库判断候选是否适合用户账号。

重要边界：
- 不要生成完整成稿。
- 不要模仿或暴露对标博主名字；吸收热点、话题、结构和角度，转成用户自己的语言。
- 不要为了凑数量强行推荐。
- 抖音浅层内容可以进入候选，也可以成为今日最值得做；但只能基于标题、文案、封面、公开元数据推断，不能声称看过口播、评论或镜头结构。
- 暂存观察不要生成可发布标题和标题备选。
- 标题和字段必须落到用户自己的真实场景：AI账号系统、飞书执行台、AI导演工作流、商业视频交付、封面Skill、公众号长文转小红书卡片、RunBY、MuseIn、车企/内容营销等。
- 输出必须严格符合 JSON Schema；不要输出 Markdown。

请重写/覆盖这些字段：
{json.dumps(SKILL_FIELDS, ensure_ascii=False)}

候选状态只能是：今日最值得做、可选候选、暂存观察、不建议制作。
推荐等级只能是：S、A、B、C。
对应方向只能是：AI业务定调、真实工作流改造、AI导演工作流、汽车与内容营销、AI项目复盘。
证据强度只能是：强、中、弱。
今日最值得做最多 3 条。

<SKILL.md>
{skill_text}
</SKILL.md>

<persona-and-cases.md>
{reference_text}
</persona-and-cases.md>

<candidate_rows_json>
{json.dumps(candidates, ensure_ascii=False, indent=2)}
</candidate_rows_json>
"""


def run_codex_skill(rows: list[dict[str, str]], model: str, timeout: int) -> tuple[list[dict[str, str]], dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="editorial-skill-") as tmpdir:
        tmp = Path(tmpdir)
        schema_path = tmp / "schema.json"
        output_path = tmp / "codex_output.json"
        schema_path.write_text(json.dumps(codex_output_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
        command = [
            "codex",
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "-C",
            str(ROOT),
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
        ]
        if model:
            command.extend(["--model", model])
        command.append("-")
        proc = subprocess.run(
            command,
            input=build_codex_prompt(rows),
            text=True,
            capture_output=True,
            timeout=timeout,
            cwd=str(ROOT),
        )
        if proc.returncode != 0:
            raise RuntimeError(
                "Codex Skill execution failed "
                f"(code={proc.returncode}). stderr={proc.stderr[-2000:]} stdout={proc.stdout[-1000:]}"
            )
        if not output_path.exists():
            raise RuntimeError(f"Codex Skill execution did not produce output file. stdout={proc.stdout[-2000:]}")
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    by_index: dict[int, dict[str, str]] = {}
    for item in payload.get("rows", []):
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        by_index[idx] = {field: str(item.get(field, "") or "") for field in SKILL_FIELDS}
    enriched: list[dict[str, str]] = []
    for idx, row in enumerate(rows):
        out = dict(row)
        judgement = by_index.get(idx)
        if not judgement:
            raise RuntimeError(f"Codex Skill output missing row index {idx}")
        out.update(judgement)
        out["Skill编辑层"] = "ai-account-editorial-director"
        out["Skill参考文件"] = str(SKILL_REFERENCE)
        enriched.append(out)
    return enriched, {
        "codex_rows": len(by_index),
        "batch_notes": payload.get("batch_notes", ""),
        "model": model or "codex-default",
    }


def write_report(
    path: Path,
    rows: list[dict[str, str]],
    input_path: Path,
    output_path: Path,
    engine: str,
    engine_meta: dict[str, Any] | None = None,
) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.get("候选状态", "")] = counts.get(row.get("候选状态", ""), 0) + 1
    payload = {
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "skill": "ai-account-editorial-director",
        "skill_dir": str(SKILL_DIR),
        "skill_reference": str(SKILL_REFERENCE),
        "input": str(input_path),
        "output": str(output_path),
        "engine": engine,
        "engine_meta": engine_meta or {},
        "rows": len(rows),
        "candidate_status_counts": counts,
        "fields_added": EXTRA_FIELDS,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ai-account-editorial-director on a candidate CSV.")
    parser.add_argument("--input", required=True, help="Candidate CSV from content_sampler.py.")
    parser.add_argument("--output", required=True, help="Enriched candidate CSV.")
    parser.add_argument("--report", default="", help="Optional JSON report path.")
    parser.add_argument(
        "--engine",
        choices=["codex", "deterministic"],
        default=os.getenv("EDITORIAL_SKILL_ENGINE", "codex"),
        help="Default codex uses the global Skill through Codex CLI. deterministic is an explicit offline fallback.",
    )
    parser.add_argument("--codex-model", default=os.getenv("EDITORIAL_SKILL_CODEX_MODEL", ""), help="Optional Codex model override.")
    parser.add_argument("--timeout", type=int, default=int(os.getenv("EDITORIAL_SKILL_TIMEOUT", "900")), help="Codex execution timeout in seconds.")
    parser.add_argument(
        "--allow-deterministic-fallback",
        action="store_true",
        help="If Codex execution fails, fall back to deterministic field filling instead of failing.",
    )
    args = parser.parse_args()

    load_local_env()
    input_path = Path(args.input)
    output_path = Path(args.output)
    rows, original_fields = read_csv(input_path)
    engine = args.engine
    engine_meta: dict[str, Any] = {}
    try:
        if args.engine == "codex":
            enriched, engine_meta = run_codex_skill(rows, args.codex_model, args.timeout)
        else:
            enriched = [enrich(row) for row in rows]
            engine_meta = {"mode": "explicit_deterministic"}
    except Exception as exc:
        if not args.allow_deterministic_fallback:
            raise
        engine = "deterministic"
        enriched = [enrich(row) for row in rows]
        engine_meta = {"fallback_after_error": str(exc)}
    fields = fieldnames_for(enriched, original_fields)
    if input_path.resolve() == output_path.resolve():
        atomic_write_csv(output_path, enriched, fields)
    else:
        write_csv(output_path, enriched, fields)
    report_path = Path(args.report) if args.report else output_path.with_suffix(".editorial_skill_report.json")
    write_report(report_path, enriched, input_path, output_path, engine, engine_meta)
    print(json.dumps({
        "ok": True,
        "rows": len(enriched),
        "engine": engine,
        "engine_meta": engine_meta,
        "input": str(input_path),
        "output": str(output_path),
        "report": str(report_path),
        "skill_reference": str(SKILL_REFERENCE),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

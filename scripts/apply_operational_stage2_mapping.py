#!/usr/bin/env python3
"""Create operational-only Stage 2 payloads from locked editorial decisions."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def mapping(decision: dict, direction: str) -> dict:
    title = decision["selected_visible_title"]
    selected = decision["locked_decision"] == "select"
    experiment = (
        f"用一条隔离测试材料制作《{title}》的最小内容样例，记录输入、动作、输出和失败边界。"
        if selected else f"暂不制作；先补齐“{decision['state_or_gap']}”所列证据，再重新判断。"
    )
    fields = {
        "原始标题钩子": decision["source_title_hook"],
        "Austin改写理由": decision["source_hook_usage"],
        "标题体感风险": "无" if selected else "观察项不作为可发布成品",
        "点击钩子": decision["audience_hook"],
        "观众为什么会点": decision["why_i_would_choose"],
        "我的真实矛盾": decision["natural_austin_angle"],
        "我要做的实验": experiment,
        "热点触发点": decision["source_title_hook"],
        "我的工作流痛点": decision["public_decision_summary"],
        "原始钩子": decision["source_title_hook"],
        "可展示证据": decision["research_evidence_ids"],
        "热点钩子": decision["audience_hook"],
        "普通人会怎么讲": decision["rejected_common_take"],
        "场景依据": "精确来源与研究 dossier",
        "真实/相邻案例": "",
        "我的改造动作": experiment,
        "需要补的证据": decision["state_or_gap"],
        "关联母场景": "",
        "借用方式": "只借来源事实和公开市场入口",
        "不能声称的部分": decision["why_i_would_not_choose"],
        "我的真实/相邻场景": "",
        "对应方向": direction,
        "一句话Brief": decision["public_decision_summary"],
        "我的场景拆解": decision["proposed_content_structure"],
        "旧流程痛点": decision["rejected_common_take"],
        "AI介入点": "仅在隔离测试中辅助整理证据和内容结构，不替代主编判断。",
        "验证方式": "检查精确来源、研究 evidence IDs、公开命题和内容结构是否一致；记录通过或阻断原因。",
        "可沉淀资产": "来源研究 dossier、主编 decision trace 与内容结构卡",
        "我的思考点": decision["why_i_would_choose"],
        "重点体现": decision["natural_austin_angle"],
        "可调用案例": "",
        "内容核心冲突": decision["audience_hook"],
        "视频呈现方式": "口播 + 精确来源画面/页面 + 证据与判断分层展示",
        "证据强度": decision["research_confidence"],
        "不建议做的原因": decision["why_i_would_not_choose"],
        "编辑判断分": "locked_by_stage1",
        "标题质量分": "validated_by_stage1",
        "AI味风险": "0",
    }
    return fields


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--directions", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out_dir = Path(args.out_dir)
    directions = json.loads(Path(args.directions).read_text(encoding="utf-8"))
    results = []
    for batch_dir in sorted((out_dir / "stage2").glob("batch_*")):
        payload = json.loads((batch_dir / "input.json").read_text(encoding="utf-8"))
        rows = []
        for item in payload["rows"]:
            decision = item["locked_editorial_decision"]
            candidate_id = item["source_facts"]["candidate_id"]
            fields = mapping(decision, directions[candidate_id])
            rows.append({
                "index": item["index"], "editorial_decision_id": decision["editorial_decision_id"],
                "editorial_decision_hash": decision["editorial_decision_hash"], "global_rank_id": decision["global_rank_id"],
                "global_rank_hash": decision["global_rank_hash"], "field_mapping_json": json.dumps(fields, ensure_ascii=False, sort_keys=True),
                **fields,
            })
        output = {"engine": "current_codex_task", "rows": rows, "batch_notes": "Operational-only mapping from locked decisions."}
        (batch_dir / "output.pending.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        completed = subprocess.run([sys.executable, str(root / "scripts" / "topic_editorial_state_machine.py"), "validate-stage2", "--out-dir", str(out_dir), "--batch-id", batch_dir.name], text=True, capture_output=True)
        results.append({"batch_id": batch_dir.name, "returncode": completed.returncode, "stdout": completed.stdout[-500:], "stderr": completed.stderr[-500:]})
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    expected_batches = len(list((out_dir / "stage2").glob("batch_*")))
    return 0 if results and len(results) == expected_batches and all(item["returncode"] == 0 for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

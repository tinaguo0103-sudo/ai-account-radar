#!/usr/bin/env python3
"""Build lossless AR-020D card manifests from finalized replay rows."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import feishu_topic_decision_card as cards


def record_from_row(row: dict[str, str], index: int) -> dict:
    record_id = row.get("candidate_id") or row.get("内容指纹") or f"candidate-{index:03d}"
    return {
        "record_id": record_id,
        "fields": {
            "选题标题": row.get("选题命题", ""),
            "原始来源标题": row.get("原始来源标题", ""),
            "原始发布文案": row.get("原始发布文案", ""),
            "来源链接": row.get("来源链接", ""),
            "研究摘要": row.get("研究摘要", ""),
            "受众钩子": row.get("受众钩子", ""),
            "研究置信度": row.get("研究置信度", ""),
            "内容结构": row.get("内容结构", ""),
            "我的切入": row.get("我的切入") or row.get("locked_natural_austin_angle", ""),
            "需要补的证据": row.get("需要补的证据", ""),
            "推荐动作": row.get("推荐动作", ""),
            "title_permission": row.get("title_permission", ""),
            "是否建议进入制作": row.get("是否建议进入制作", ""),
            "状态": "待判断",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    with Path(args.rows).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    actionable = [row for row in rows if row.get("推荐动作") == "生成脚本包"]
    records = [record_from_row(row, index) for index, row in enumerate(actionable)]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = cards.build_card_pages(records, args.run_id, page_size=5)
    (output_dir / "card_page_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    fixtures = {}
    for count in (0, 1, 3, 7, 12):
        source = records[:count]
        if len(source) < count:
            source = [
                {"record_id": f"fixture-{idx:02d}", "fields": {**records[idx % len(records)]["fields"], "选题标题": f"fixture-{idx:02d}"}}
                for idx in range(count)
            ] if records else []
        fixture = cards.build_card_pages(source, f"{args.run_id}_fixture_{count}", page_size=5)
        fixtures[str(count)] = {
            "candidate_count": fixture["candidate_count"],
            "candidate_ids": fixture["candidate_ids"],
            "page_candidate_ids": [page["candidate_ids"] for page in fixture["pages"]],
            "bijection_ok": fixture["candidate_ids"] == [item for page in fixture["pages"] for item in page["candidate_ids"]],
        }
    (output_dir / "pagination_fixtures.json").write_text(
        json.dumps(fixtures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"records": len(records), "pages": len(manifest["pages"]), "fixtures": fixtures}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

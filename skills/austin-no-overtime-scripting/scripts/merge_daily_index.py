#!/usr/bin/env python3
"""Create an index.md for Austin full execution packages."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def pick(pattern: str, text: str, fallback: str = "") -> str:
    match = re.search(pattern, text, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, help="Directory containing topic package folders.")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    rows = []
    for doc_path in sorted(run_dir.glob("*/full_script_execution_package.md")):
        text = doc_path.read_text(encoding="utf-8")
        rows.append({
            "title": pick(r"^#\s+(.+)$", text, doc_path.parent.name),
            "template": pick(r"^- 推荐模板：(.+)$", text),
            "qa": pick(r"^- 结果：(.+)$", text),
            "status": pick(r"^- 生产判断：(.+)$", text),
            "path": doc_path.parent.name,
            "needs_fact_check": "否" if "发布前核验：无额外事实核验点" in text else "是",
        })

    lines = ["# Austin不加班脚本Skill｜当日执行包索引", "", f"输出目录：`{run_dir}`", "", "| 选题 | 状态 | 模板 | QA | 需事实核验 | 文档 |", "|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['title']} | {row['status']} | {row['template']} | {row['qa']} | {row['needs_fact_check']} | `{row['path']}/full_script_execution_package.md` |")
    index_path = run_dir / "index.md"
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "index": str(index_path), "topics": len(rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

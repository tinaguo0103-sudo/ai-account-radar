#!/usr/bin/env python3
"""Bind a current-task global order to locked Stage 1 decisions and validate."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--order", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out_dir = Path(args.out_dir)
    payload = json.loads((out_dir / "global_ranking" / "input.json").read_text(encoding="utf-8"))
    decisions = {int(row["index"]): row for row in payload["decisions"]}
    order = json.loads(Path(args.order).read_text(encoding="utf-8"))
    if set(order) != set(decisions) or len(order) != len(set(order)):
        raise RuntimeError("Current-task order must be a strict bijection")
    rows = []
    for position, index in enumerate(order, start=1):
        decision = decisions[index]
        rows.append({
            "index": index,
            "editorial_decision_id": decision["editorial_decision_id"],
            "editorial_decision_hash": decision["editorial_decision_hash"],
            "input_global_rank_hash": decision["global_rank_hash"],
            "global_daily_level": decision["locked_daily_level"],
            "final_recommendation_status": decision["locked_recommendation_status"],
            "global_rank_position": str(position),
            "global_tradeoff_reason": f"全日第 {position} 位；保持 Stage1 eligibility，仅表达相对优先级，不截断候选。",
        })
    output = {"engine": "current_codex_task", "ranking_rows": rows, "global_ranking_notes": "Dynamic lossless 0..N ordering; no cap, truncation, or eligibility rewrite."}
    (out_dir / "global_ranking" / "output.pending.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return subprocess.run([sys.executable, str(root / "scripts" / "topic_editorial_state_machine.py"), "validate-ranking", "--out-dir", str(out_dir)]).returncode


if __name__ == "__main__":
    raise SystemExit(main())

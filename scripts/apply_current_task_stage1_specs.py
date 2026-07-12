#!/usr/bin/env python3
"""Bind current-task Stage 1 judgments to prepared batch inputs and validate."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--specs", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out_dir = Path(args.out_dir)
    specs = json.loads(Path(args.specs).read_text(encoding="utf-8"))
    results = []
    for batch_dir in sorted((out_dir / "stage1").glob("batch_*")):
        payload = json.loads((batch_dir / "input.json").read_text(encoding="utf-8"))
        decisions = []
        for row in payload["rows"]:
            authored = dict(specs[row["candidate_id"]])
            authored.update({
                "index": row["index"],
                "research_dossier_hash": row["research"]["dossier_hash"],
                "research_evidence_ids": ",".join(
                    item["evidence_id"] for item in row["research"]["results"] if item.get("evidence_id")
                ) or row["source"]["content_evidence"][0]["evidence_id"],
                "hook_evidence_ids": ",".join(row["hook_analysis"]["hook_evidence_ids"]),
                "audience_hook": row["hook_analysis"]["audience_hook"],
                "source_read": row["research"]["research_summary"],
            })
            decisions.append(authored)
        output = {"engine": "current_codex_task", "editorial_decisions": decisions, "batch_notes": "Pre-validation current-task judgments; final evidence derives from locked rows."}
        (batch_dir / "output.pending.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        completed = subprocess.run([
            sys.executable, str(root / "scripts" / "topic_editorial_state_machine.py"), "validate-stage1",
            "--out-dir", str(out_dir), "--batch-id", batch_dir.name,
        ], text=True, capture_output=True)
        results.append({"batch_id": batch_dir.name, "returncode": completed.returncode, "stderr": completed.stderr[-500:]})
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0 if len(results) == 7 and all(item["returncode"] == 0 for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

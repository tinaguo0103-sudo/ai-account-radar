#!/usr/bin/env python3
"""Bind current-task research specs to fresh source hashes and validate them."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import topic_research_dossier_builder as builder


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--specs", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out_dir = Path(args.out_dir)
    specs = json.loads(Path(args.specs).read_text(encoding="utf-8"))
    state = json.loads((out_dir / "editorial_state_machine.json").read_text(encoding="utf-8"))
    researchable = {
        candidate_id
        for candidate_id, candidate in state["stages"]["source_open"]["candidates"].items()
        if candidate["status"] == "completed"
    }
    if set(specs) != researchable:
        missing = sorted(researchable - set(specs))
        unexpected = sorted(set(specs) - researchable)
        raise RuntimeError(
            f"Research specs must cover every successfully opened candidate exactly once; "
            f"missing={missing}, unexpected={unexpected}"
        )
    results = []
    for candidate_id, authored in specs.items():
        source = json.loads((out_dir / "source_open" / candidate_id / "validated.json").read_text(encoding="utf-8"))
        source_id = source["content_evidence"][0]["evidence_id"]
        authored_text = json.dumps(authored, ensure_ascii=False).replace("$SOURCE", source_id)
        spec = {**json.loads(authored_text), "source_content_hash": source["captured_content_hash"], "status": "completed"}
        dossier = builder.build_dossier(spec)
        target = out_dir / "research" / candidate_id / "output.pending.json"
        target.write_text(json.dumps(dossier, ensure_ascii=False, indent=2), encoding="utf-8")
        completed = subprocess.run([
            sys.executable, str(root / "scripts" / "topic_editorial_state_machine.py"),
            "validate-research", "--out-dir", str(out_dir), "--candidate-id", candidate_id,
        ], text=True, capture_output=True)
        results.append({"candidate_id": candidate_id, "returncode": completed.returncode, "stderr": completed.stderr[-500:]})
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0 if len(results) == len(researchable) and all(item["returncode"] == 0 for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

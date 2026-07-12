#!/usr/bin/env python3
"""Open every prepared Douyin candidate through the dedicated CDP adapter."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--cdp", default="http://127.0.0.1:9333")
    parser.add_argument("--wait-ms", type=int, default=8000)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out_dir = Path(args.out_dir)
    candidates = json.loads((out_dir / "shortlist_candidates.json").read_text(encoding="utf-8"))
    results = []
    for candidate in candidates:
        if candidate["primary_adapter"] != "douyin_cdp_exact_video_v1":
            continue
        candidate_dir = out_dir / "source_open" / candidate["candidate_id"]
        capture_dir = candidate_dir / "cdp_capture"
        command = [
            "node", str(root / "scripts" / "douyin_cdp_exact_video_open.mjs"),
            "--cdp", args.cdp, "--url", candidate["exact_url"],
            "--expected-title", candidate["csv_title"], "--out-dir", str(capture_dir),
            "--wait-ms", str(args.wait_ms),
        ]
        completed = subprocess.run(command, text=True, capture_output=True)
        source_path = capture_dir / "source_open.json"
        if source_path.exists():
            (candidate_dir / "output.pending.json").write_bytes(source_path.read_bytes())
            validate = subprocess.run([
                sys.executable, str(root / "scripts" / "topic_editorial_state_machine.py"),
                "validate-source-open", "--out-dir", str(out_dir), "--candidate-id", candidate["candidate_id"],
            ], text=True, capture_output=True)
            results.append({"candidate_id": candidate["candidate_id"], "open_rc": completed.returncode, "validate_rc": validate.returncode})
        else:
            results.append({"candidate_id": candidate["candidate_id"], "open_rc": completed.returncode, "validate_rc": None})
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0 if results and all(item["validate_rc"] == 0 for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Open every prepared Douyin candidate through the dedicated CDP adapter."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


MAX_SOURCE_ATTEMPTS = 2


def should_attempt(record: dict, validated: dict | None) -> bool:
    if validated and validated.get("open_status") == "opened":
        return False
    return int(record.get("source_attempt_count", 0) or 0) < MAX_SOURCE_ATTEMPTS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--cdp", default="http://127.0.0.1:9333")
    parser.add_argument("--wait-ms", type=int, default=8000)
    parser.add_argument("--max-attempts-per-run", type=int, default=1)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out_dir = Path(args.out_dir)
    candidates = json.loads((out_dir / "shortlist_candidates.json").read_text(encoding="utf-8"))
    state = json.loads((out_dir / "editorial_state_machine.json").read_text(encoding="utf-8"))
    results = []
    attempted = 0
    for candidate in candidates:
        if candidate["primary_adapter"] != "douyin_cdp_exact_video_v1":
            continue
        candidate_dir = out_dir / "source_open" / candidate["candidate_id"]
        validated_path = candidate_dir / "validated.json"
        validated = json.loads(validated_path.read_text(encoding="utf-8")) if validated_path.exists() else None
        record = state["stages"]["source_open"]["candidates"][candidate["candidate_id"]]
        if not should_attempt(record, validated):
            results.append({
                "candidate_id": candidate["candidate_id"],
                "open_rc": 0 if validated and validated.get("open_status") == "opened" else None,
                "validate_rc": 0 if validated and validated.get("open_status") == "opened" else 1,
                "resumed": bool(validated and validated.get("open_status") == "opened"),
                "bounded_failure": not bool(validated and validated.get("open_status") == "opened"),
            })
            continue
        if attempted >= args.max_attempts_per_run:
            continue
        lock_path = candidate_dir / ".source_open_retry.lock"
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            results.append({"candidate_id": candidate["candidate_id"], "locked": True})
            continue
        os.close(lock_fd)
        attempted += 1
        capture_dir = candidate_dir / "cdp_capture"
        command = [
            "node", str(root / "scripts" / "douyin_cdp_exact_video_open.mjs"),
            "--cdp", args.cdp, "--url", candidate["exact_url"],
            "--expected-title", candidate["csv_title"], "--out-dir", str(capture_dir),
            "--wait-ms", str(args.wait_ms),
        ]
        try:
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
        finally:
            lock_path.unlink(missing_ok=True)
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0 if results and all(item["validate_rc"] == 0 for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

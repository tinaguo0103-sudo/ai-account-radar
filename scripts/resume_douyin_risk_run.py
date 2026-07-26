#!/usr/bin/env python3
"""Resume only checkpoint-pending Douyin accounts for one exact run."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_RE = re.compile(r"^run_\d{8}_\d{6}(?:_[A-Za-z0-9_-]+)?$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-db", required=True)
    args = parser.parse_args()
    if not RUN_RE.fullmatch(args.run_id):
        raise SystemExit("wrong_run")
    run_root = ROOT / "output" / "runs" / args.run_id
    out_dir = run_root / "sources" / "douyin"
    config = out_dir / "source_plan_config.json"
    if not config.is_file() or not out_dir.is_dir():
        raise SystemExit("douyin_resume_artifacts_missing")
    env = dict(os.environ, AI_ACCOUNT_RADAR_RUN_ID=args.run_id)
    command = [
        "node", str(ROOT / "scripts" / "douyin_cdp_source_watch_probe.mjs"),
        "--cdp", "http://127.0.0.1:9333",
        "--out-dir", str(out_dir),
        "--config", str(config),
        "--source-db", str(Path(args.source_db).resolve()),
        "--account-limit", "0",
    ]
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())

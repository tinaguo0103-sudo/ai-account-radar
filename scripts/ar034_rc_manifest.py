#!/usr/bin/env python3
"""Verify post-commit AR-034 RC evidence identity without modifying the RC tree."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def verify_manifest(payload: dict[str, Any], repo: Path) -> dict[str, Any]:
    declared = payload.get("rc_head")
    if not isinstance(declared, str) or not FULL_SHA.fullmatch(declared):
        return {"ok": False, "reason": "rc_head_malformed", "rc_head": declared}
    try:
        actual = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, text=True, capture_output=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return {"ok": False, "reason": "git_head_read_failed", "rc_head": declared}
    if declared != actual:
        return {"ok": False, "reason": "rc_head_mismatch", "rc_head": declared, "actual_head": actual}
    return {"ok": True, "reason": "", "rc_head": declared, "actual_head": actual, "post_commit_evidence": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repo", default=".")
    args = parser.parse_args()
    try:
        payload = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        result = {"ok": False, "reason": "manifest_unreadable"}
    else:
        result = verify_manifest(payload, Path(args.repo).resolve()) if isinstance(payload, dict) else {"ok": False, "reason": "manifest_non_object"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())

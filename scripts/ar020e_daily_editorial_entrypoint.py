#!/usr/bin/env python3
"""Read-only readiness gate for the AR-020E production outer Codex task."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "ai-account-editorial-director" / "SKILL.md"
PROTOCOL_PATH = ROOT / "config" / "ar020e_outer_task_protocol.md"
STATE_MACHINE_PATH = ROOT / "scripts" / "topic_editorial_state_machine.py"
FORBIDDEN_PROTOCOL_TEXT = (
    "codex exec",
    "--engine deterministic",
    "--allow-deterministic-fallback",
    "Gate -> Workflow Experiment Card -> Title Packaging",
    "今日最值得做最多 3",
)
STAGES = (
    "prepare-source-open", "validate-source-open", "prepare-research", "validate-research",
    "prepare-stage1", "validate-stage1", "prepare-ranking", "validate-ranking",
    "prepare-stage2", "validate-stage2", "finalize",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def csv_count(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def check_readiness(run_id: str, input_path: Path | None) -> dict[str, Any]:
    failures: list[str] = []
    if not run_id or not run_id.startswith("run_"):
        failures.append("invalid_or_missing_run_id")
    for path, label in ((SKILL_PATH, "repo_skill"), (PROTOCOL_PATH, "outer_protocol"), (STATE_MACHINE_PATH, "state_machine")):
        if not path.is_file():
            failures.append(f"missing_{label}")
    protocol = PROTOCOL_PATH.read_text(encoding="utf-8") if PROTOCOL_PATH.is_file() else ""
    forbidden_hits = [term for term in FORBIDDEN_PROTOCOL_TEXT if term in protocol]
    if forbidden_hits:
        failures.append("forbidden_protocol_text:" + ",".join(forbidden_hits))
    row_count = 0
    if input_path is not None:
        if not input_path.is_file():
            failures.append("missing_input_csv")
        else:
            row_count = csv_count(input_path)
            if row_count == 0:
                failures.append("empty_input_csv")
            if run_id and run_id not in str(input_path):
                failures.append("input_run_id_mismatch")
    return {
        "ok": not failures,
        "check_only": True,
        "execution_surface": "current_codex_task",
        "run_id": run_id,
        "input": str(input_path) if input_path else "",
        "input_rows": row_count,
        "repo_skill_path": str(SKILL_PATH),
        "repo_skill_sha256": sha256_file(SKILL_PATH) if SKILL_PATH.is_file() else "",
        "protocol_path": str(PROTOCOL_PATH),
        "state_machine_path": str(STATE_MACHINE_PATH),
        "required_stages": list(STAGES),
        "dynamic_ranking": "0..N_no_cap",
        "strict_fail_closed": True,
        "nested_model_execution": False,
        "writes_feishu": False,
        "sends_topic_card": False,
        "triggers_collection": False,
        "triggers_script_generation": False,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check AR-020E outer-task readiness without business I/O.")
    parser.add_argument("--check-only", action="store_true", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--input")
    args = parser.parse_args()
    result = check_readiness(args.run_id, Path(args.input).resolve() if args.input else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

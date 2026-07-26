#!/usr/bin/env python3
"""CLI for the repository-owned source-control authority."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from source_control import DEFAULT_DB, SourceControl


def read_json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("snapshot")
    commands.add_parser("plan")
    commands.add_parser("runtime-config")
    migrate = commands.add_parser("import-json")
    migrate.add_argument("--input", required=True)
    apply = commands.add_parser("apply")
    apply.add_argument("--command-id", required=True)
    apply.add_argument("--expected-revision", type=int, required=True)
    apply.add_argument("--operations", required=True)
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--command-id", required=True)
    rollback.add_argument("--expected-revision", type=int, required=True)
    rollback.add_argument("--target-revision", type=int, required=True)
    result = commands.add_parser("command")
    result.add_argument("--command-id", required=True)
    events = commands.add_parser("record-events")
    events.add_argument("--run-id", required=True)
    events.add_argument("--input", required=True)
    args = parser.parse_args()
    service = SourceControl(args.db)
    if args.action == "snapshot":
        output = service.get_source_snapshot()
    elif args.action == "plan":
        output = service.build_collection_plan()
    elif args.action == "runtime-config":
        output = service.export_runtime_config()
    elif args.action == "import-json":
        output = service.import_accounts(read_json(args.input))
    elif args.action == "apply":
        output = service.apply_config_command(args.command_id, args.expected_revision, read_json(args.operations))
    elif args.action == "rollback":
        output = service.rollback_to_revision(args.command_id, args.expected_revision, args.target_revision)
    elif args.action == "command":
        output = service.get_command_result(args.command_id)
    else:
        output = service.record_run_outcomes(args.run_id, read_json(args.input))
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

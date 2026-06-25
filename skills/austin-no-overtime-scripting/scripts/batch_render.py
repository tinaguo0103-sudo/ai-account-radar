#!/usr/bin/env python3
"""Render v0.2 Austin scripting packages from JSON or CSV input."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from austin_scripting import load_records, render_records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Input JSON or CSV.")
    parser.add_argument("--output-root", required=True, help="Output root directory.")
    parser.add_argument("--run-date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    records = load_records(Path(args.input))
    summaries = render_records(records, Path(args.output_root), run_date=args.run_date, limit=args.limit)
    print(json.dumps({
        "ok": True,
        "input_records": len(records),
        "rendered": len(summaries),
        "summaries": summaries,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

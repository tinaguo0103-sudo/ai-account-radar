#!/usr/bin/env python3
"""Build hash-stable AR-020D research dossiers from reviewed evidence."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from topic_research_contract import hash_json


def build_dossier(spec: dict[str, Any]) -> dict[str, Any]:
    dossier = {
        "protocol": "ar020d_research_grounded_v1",
        "status": spec.get("status", "completed"),
        "source_content_hash": spec["source_content_hash"],
        "source_type": spec.get("source_type", "evergreen"),
        "queries": list(spec.get("queries") or []),
        "results": list(spec.get("results") or []),
        "external_corroboration_state": spec.get("external_corroboration_state", "opened_support"),
        "confidence": spec.get("confidence", "medium"),
        "corroboration_gap": spec.get("corroboration_gap", ""),
        "research_summary": spec["research_summary"],
        "hook_analysis": dict(spec["hook_analysis"]),
        "claim_evidence": list(spec.get("claim_evidence") or []),
        "completed_at": spec.get("completed_at") or datetime.now(timezone.utc).isoformat(),
    }
    dossier["dossier_hash"] = hash_json(dossier)
    return dossier


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, help="Reviewed research spec JSON")
    parser.add_argument("--output", required=True, help="output.pending.json path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_dossier(spec), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

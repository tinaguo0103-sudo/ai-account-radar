#!/usr/bin/env python3
"""One-shot exact adapter for run_20260727_080141 historical artifacts."""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from trend_hotspot_cards import build_hotspot_cards

ROOT = Path(__file__).resolve().parents[1]
EXACT_RUN = "run_20260727_080141"
EXACT_DATE = "2026-07-27"


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def remap_result(
    source_path: str,
    output_path: Path,
    identity_map: dict[str, str],
    *,
    collection_key: str,
    identity_key: str,
) -> Path:
    payload = json.loads(Path(source_path).read_text(encoding="utf-8"))
    for row in payload.get(collection_key, []):
        identity = str(row.get(identity_key) or "")
        mapped = identity_map.get(identity)
        if not mapped:
            raise SystemExit("historical_result_identity_conflict")
        row[identity_key] = mapped
    output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--daily-log", type=Path, required=True)
    parser.add_argument("--workflow-db", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--editorial-result-file")
    parser.add_argument("--scripts-result-file")
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    if run_dir.name != EXACT_RUN:
        raise SystemExit("historical_wrong_run")
    log = json.loads(args.daily_log.read_text(encoding="utf-8"))
    if (
        log.get("run_id") != EXACT_RUN
        or log.get("collection_status") not in {"completed", "completed_with_failures"}
        or log.get("downstream_usable") is not True
        or Path(str(log.get("run_output_dir") or "")).resolve() != run_dir
    ):
        raise SystemExit("historical_checkpoint_invalid")
    content = rows(run_dir / "content_items.csv")
    topics = rows(run_dir / "today_10_topics.csv")
    identities: set[str] = set()
    converted = []
    for row in content:
        legacy = str(row.get("内容指纹") or "").strip()
        if not legacy or legacy in identities:
            raise SystemExit("historical_content_identity_conflict")
        identities.add(legacy)
        converted.append({
            "item_id": f"legacy:{legacy}",
            "external_id": f"legacy:{legacy}",
            "source": str(row.get("平台") or row.get("来源类型") or ""),
            "account": str(row.get("账号名/公众号名") or ""),
            "title": str(row.get("内容标题") or ""),
            "summary": str(row.get("正文/字幕/简介片段") or "")[:360],
            "body": str(row.get("正文/字幕/简介片段") or ""),
            "source_url": str(row.get("内容链接") or ""),
            "published_at": str(row.get("发布时间") or ""),
        })
    candidates = []
    for row in topics:
        legacy = str(row.get("内容指纹") or "").strip()
        if legacy in identities:
            candidates.append({
                "candidate_id": f"legacy:{legacy}",
                "item_id": f"legacy:{legacy}",
                "title": str(row.get("内容标题") or row.get("来源标题") or ""),
                "summary": str(row.get("来源内容") or ""),
                "source_url": next(
                    (
                        item["source_url"] for item in converted
                        if item["item_id"] == f"legacy:{legacy}"
                    ),
                    "",
                ),
            })
    hotspot_cards = build_hotspot_cards(candidates, items=converted, run_id=EXACT_RUN)
    identity_map = {
        legacy_id: card["candidate_id"]
        for card in hotspot_cards
        for legacy_id in card.get("legacy_candidate_ids", [])
    }
    fixture = {
        "run_id": EXACT_RUN, "business_date": EXACT_DATE,
        "status": log["collection_status"], "content_items": converted,
        "candidates": candidates, "source_runs": log.get("source_outcomes", []),
    }
    with tempfile.TemporaryDirectory(prefix="web010_historical_adapter_") as tmp:
        path = Path(tmp) / "collection.json"
        path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
        command = [
            sys.executable, str(ROOT / "scripts/run_daily_workflow.py"),
            "--run-id", EXACT_RUN, "--business-date", EXACT_DATE,
            "--workflow-db", str(args.workflow_db.resolve()),
            "--artifact-root", str(args.artifact_root.resolve()),
            "--collection-fixture", str(path), "--video-mode", "disabled",
        ]
        if args.editorial_result_file:
            mapped_editorial = remap_result(
                args.editorial_result_file,
                Path(tmp) / "editorial.json",
                identity_map,
                collection_key="topics",
                identity_key="candidate_id",
            )
            command.extend(["--editorial-result-file", str(mapped_editorial)])
        result = subprocess.run(command, text=True)
        if result.returncode != 0 or not args.scripts_result_file:
            return result.returncode
        mapped_scripts = remap_result(
            args.scripts_result_file,
            Path(tmp) / "scripts.json",
            identity_map,
            collection_key="scripts",
            identity_key="topic_id",
        )
        payload = json.loads(mapped_scripts.read_text(encoding="utf-8"))
        for script in payload.get("scripts", []):
            handoff_path = args.artifact_root.resolve() / EXACT_RUN / "workflow_handoff.json"
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            if handoff.get("action") != "scripts_required":
                return 1
            submission = Path(tmp) / f"submission_{script['topic_id'].replace(':', '_')}.json"
            submission.write_text(json.dumps({
                "packet_id": handoff["topic_input"]["packet_id"],
                "script": script,
            }, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(
                command + ["--script-item-file", str(submission)],
                text=True,
            )
            if result.returncode != 0:
                return result.returncode
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

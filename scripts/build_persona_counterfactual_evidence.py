#!/usr/bin/env python3
"""Prepare or validate independently executed current-task persona pairs."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import persona_counterfactual_audit as audit


def stable_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def load_stage1_rows(out_dir: Path) -> tuple[list[dict], list[dict]]:
    inputs, decisions = [], []
    for batch_dir in sorted((out_dir / "stage1").glob("batch_*")):
        payload = json.loads((batch_dir / "input.json").read_text(encoding="utf-8"))
        output = json.loads((batch_dir / "output.pending.json").read_text(encoding="utf-8"))
        by_index = {row["index"]: row for row in output["editorial_decisions"]}
        for row in payload["rows"]:
            inputs.append(row)
            decisions.append(by_index[row["index"]])
    return inputs, decisions


def prepare(out_dir: Path, candidate_ids: set[str]) -> dict:
    inputs, _decisions = load_stage1_rows(out_dir)
    prepared = []
    for source_row in inputs:
        candidate_id = source_row["candidate_id"]
        if candidate_id not in candidate_ids:
            continue
        shared = {key: source_row[key] for key in ("source", "research", "hook_analysis")}
        pair_dir = out_dir / "persona_counterfactual" / candidate_id
        pair_dir.mkdir(parents=True, exist_ok=True)
        variants = {
            "with_persona": {**shared, "persona_facts": source_row["persona_facts"], "judgment_and_style_examples": source_row["judgment_and_style_examples"]},
            "without_persona": {**shared, "persona_facts": None, "judgment_and_style_examples": []},
        }
        for name, payload in variants.items():
            envelope = {
                "protocol": "ar020d_independent_current_task_counterfactual_v1",
                "candidate_id": candidate_id,
                "variant": name,
                "execution_surface": "current_codex_task",
                "input": payload,
                "input_hash": stable_hash(payload),
            }
            (pair_dir / f"{name}.input.json").write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
        prepared.append(candidate_id)
    return {"prepared": prepared, "count": len(prepared)}


def validate(out_dir: Path) -> dict:
    pairs = []
    for pair_dir in sorted((out_dir / "persona_counterfactual").iterdir()):
        if not pair_dir.is_dir():
            continue
        outputs = {}
        execution_ids = set()
        for variant in ("with_persona", "without_persona"):
            input_envelope = json.loads((pair_dir / f"{variant}.input.json").read_text(encoding="utf-8"))
            output_envelope = json.loads((pair_dir / f"{variant}.output.json").read_text(encoding="utf-8"))
            if input_envelope.get("execution_surface") != "current_codex_task" or output_envelope.get("execution_surface") != "current_codex_task":
                raise RuntimeError("Persona pair lacks current-task provenance")
            if output_envelope.get("input_hash") != input_envelope.get("input_hash"):
                raise RuntimeError("Persona pair input/output hash mismatch")
            execution_id = str(output_envelope.get("independent_execution_id") or "")
            if not execution_id or execution_id in execution_ids:
                raise RuntimeError("Persona pair outputs were not independently executed")
            execution_ids.add(execution_id)
            outputs[variant] = {"candidate_id": pair_dir.name, **input_envelope["input"], **output_envelope["editorial_decision"]}
        comparison = audit.compare_pair(outputs["with_persona"], outputs["without_persona"])
        (pair_dir / "computed_diff.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
        pairs.append(outputs)
    retrievals = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((out_dir / "persona_retrieval").glob("*.json"))]
    with (out_dir / "skill_replay_rows.csv").open(encoding="utf-8-sig", newline="") as handle:
        metric_rows = list(csv.DictReader(handle))
    result = audit.write_report(out_dir / "persona_counterfactual", pairs, metric_rows, retrievals)
    if len(pairs) < 6 or not result["all_facts_stable"] or not result["all_eligibility_stable"]:
        raise RuntimeError("Persona counterfactual evidence failed")
    if not all(item["persona_changes_expression_only"] for item in result["comparisons"]):
        raise RuntimeError("Persona pair does not demonstrate expression-only change")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--candidate-id", action="append", default=[])
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    result = prepare(out_dir, set(args.candidate_id)) if args.prepare else validate(out_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

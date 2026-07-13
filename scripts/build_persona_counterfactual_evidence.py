#!/usr/bin/env python3
"""Build auditable paired persona/no-persona evidence from current-task outputs."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import persona_counterfactual_audit as audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--controls", required=True)
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    controls = json.loads(Path(args.controls).read_text(encoding="utf-8"))
    pairs = []
    rows = []
    for batch_dir in sorted((out_dir / "stage1").glob("batch_*")):
        input_payload = json.loads((batch_dir / "input.json").read_text(encoding="utf-8"))
        output_payload = json.loads((batch_dir / "output.pending.json").read_text(encoding="utf-8"))
        by_index = {row["index"]: row for row in output_payload["editorial_decisions"]}
        for source_row in input_payload["rows"]:
            decision = by_index[source_row["index"]]
            candidate_id = source_row["candidate_id"]
            rows.append({"candidate_id": candidate_id, **decision})
            if candidate_id not in controls:
                continue
            shared = {key: source_row[key] for key in ("source", "research", "hook_analysis")}
            with_persona_input = {**shared, "persona_facts": source_row["persona_facts"], "judgment_and_style_examples": source_row["judgment_and_style_examples"]}
            without_persona_input = {**shared, "persona_facts": None, "judgment_and_style_examples": []}
            control = {**decision, **controls[candidate_id]}
            with_output = {"candidate_id": candidate_id, **shared, **decision}
            without_output = {"candidate_id": candidate_id, **shared, **control}
            pair_dir = out_dir / "persona_counterfactual" / candidate_id
            pair_dir.mkdir(parents=True, exist_ok=True)
            for name, value in [
                ("with_persona.input.json", with_persona_input), ("without_persona.input.json", without_persona_input),
                ("with_persona.output.json", with_output), ("without_persona.output.json", without_output),
            ]:
                (pair_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            pairs.append({"with_persona": with_output, "without_persona": without_output})
    retrievals = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((out_dir / "persona_retrieval").glob("*.json"))]
    final_rows_path = out_dir / "skill_replay_rows.csv"
    metric_rows = rows
    if final_rows_path.is_file():
        with final_rows_path.open(encoding="utf-8-sig", newline="") as handle:
            metric_rows = list(csv.DictReader(handle))
    result = audit.write_report(out_dir / "persona_counterfactual", pairs, metric_rows, retrievals)
    if len(pairs) != 6 or not result["all_facts_stable"] or not result["all_eligibility_stable"]:
        raise RuntimeError("Persona counterfactual evidence failed")
    if result["leakage"]["all_candidates_same_retrieval"]:
        raise RuntimeError("Persona retrieval collapsed to one universal example set")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

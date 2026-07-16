from __future__ import annotations

import csv
import hashlib
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

import exact_candidate_input as exact_input
import topic_editorial_state_machine as machine


FIELDS = [
    "来源链接", "内容指纹", "来源类型", "来源内容", "原始来源标题",
    "原始发布文案", "原始来源账号", "平台",
]


class ExactCandidateInputTests(unittest.TestCase):
    def fixture(self, count: int = 3):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        run_id = "run_20260716_080311"
        path = root / "output" / "runs" / run_id / "today_10_topics.csv"
        path.parent.mkdir(parents=True)
        rows = [
            {
                "来源链接": f"https://example.com/{index}",
                "内容指纹": f"fp-{index}",
                "来源类型": "test",
                "来源内容": f"source-{index}",
                "原始来源标题": f"title-{index}",
                "原始发布文案": f"caption-{index}",
                "原始来源账号": f"author-{index}",
                "平台": "web",
            }
            for index in range(count)
        ]
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return temp, root, run_id, path, rows

    def load(self, count: int = 3):
        temp, root, run_id, path, rows = self.fixture(count)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        loaded, manifest = exact_input.load_exact_input(
            path, run_id=run_id, expected_sha256=digest, project_root=root
        )
        return temp, root, run_id, path, rows, loaded, manifest

    def prepared_state(self, count: int = 3):
        temp, root, run_id, path, rows = self.fixture(count)
        out_dir = root / "state"
        args = Namespace(
            out_dir=str(out_dir),
            content_csv=[],
            since="2026-07-01",
            batch_size=3,
            max_skill_candidates=19,
            task_id="ar033b-test",
            persona_docx=str(root / "persona.docx"),
            exact_input_csv=str(path),
            exact_input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            run_id=run_id,
        )
        persona_manifest = {
            "authority_sha256": "a" * 64,
            "manifest_hash": "b" * 64,
        }
        with mock.patch.object(machine.persona_builder, "build_bundle", return_value=persona_manifest):
            machine.prepare_source_open(args)
        return temp, root, run_id, path, rows, out_dir

    def write_eligible_source_files(self, out_dir: Path, indices: list[int] | None = None):
        state = machine.load_state(out_dir)
        candidates = machine.candidate_rows_from_state(state)
        selected = candidates if indices is None else [candidates[index] for index in indices]
        eligible = [{**candidate, "eligible_index": offset} for offset, candidate in enumerate(selected)]
        machine.write_json(out_dir / "eligible_candidates.json", eligible)
        for candidate in selected:
            source = {
                "platform": candidate["platform"],
                "exact_title": candidate["csv_title"],
                "caption_body": candidate["original_publication_copy"],
                "final_url": candidate["exact_url"],
                "exact_url": candidate["exact_url"],
                "independent_title_verified": True,
            }
            machine.write_json(
                out_dir / "source_open" / candidate["candidate_id"] / "validated.json",
                source,
            )
            machine.write_json(
                out_dir / "research" / candidate["candidate_id"] / "validated.json",
                {
                    "source": source,
                    "dossier_hash": "d" * 64,
                },
            )
        return state, eligible

    def legacy_pool_sentinels(self):
        error = AssertionError("exact_mode_resampling_or_pool_rebuild_called")
        return (
            mock.patch.object(machine, "pool_from_state", side_effect=error),
            mock.patch.object(machine, "shortlist", side_effect=error),
            mock.patch.object(machine.deterministic_replay, "load_items", side_effect=error),
            mock.patch.object(machine.replay, "build_pre_skill_pool", side_effect=error),
        )

    def test_zero_one_and_n_contract(self) -> None:
        temp, root, run_id, path, _ = self.fixture(0)
        try:
            with self.assertRaisesRegex(exact_input.ExactInputError, "empty_exact_input"):
                exact_input.load_exact_input(
                    path,
                    run_id=run_id,
                    expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    project_root=root,
                )
        finally:
            temp.cleanup()
        for count in (1, 9):
            temp, _, _, _, _, _, manifest = self.load(count)
            try:
                self.assertEqual(manifest["row_count"], count)
                self.assertEqual(len(manifest["ordered_candidates"]), count)
            finally:
                temp.cleanup()

    def test_wrong_path_hash_run_and_required_fields_fail(self) -> None:
        temp, root, run_id, path, _ = self.fixture()
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            wrong = root / "copy.csv"
            wrong.write_bytes(path.read_bytes())
            cases = [
                (wrong, run_id, digest, "non_exact_input_path"),
                (path, "run_20260715_080311", digest, "non_exact_input_path"),
                (path, run_id, "0" * 64, "exact_input_sha256_mismatch"),
            ]
            for input_path, candidate_run, candidate_hash, reason in cases:
                with self.subTest(reason=reason), self.assertRaisesRegex(exact_input.ExactInputError, reason):
                    exact_input.load_exact_input(
                        input_path,
                        run_id=candidate_run,
                        expected_sha256=candidate_hash,
                        project_root=root,
                    )
            with path.open(encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            rows[0]["来源链接"] = ""
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(exact_input.ExactInputError, "empty_exact_input_field"):
                exact_input.load_exact_input(
                    path,
                    run_id=run_id,
                    expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    project_root=root,
                )
        finally:
            temp.cleanup()

    def test_duplicate_and_candidate_mutations_fail(self) -> None:
        temp, root, run_id, path, rows, _, manifest = self.load()
        try:
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows([rows[0], rows[0]])
            with self.assertRaisesRegex(exact_input.ExactInputError, "duplicate_exact_input"):
                exact_input.load_exact_input(
                    path,
                    run_id=run_id,
                    expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    project_root=root,
                )
            candidates = [
                {
                    "index": identity["index"],
                    "exact_url": identity["exact_url"],
                    "content_fingerprint": identity["content_fingerprint"],
                    "candidate_fingerprint": identity["candidate_fingerprint"],
                    "csv_title": identity["original_source_title"],
                    "original_publication_copy": identity["original_publication_copy"],
                    "source_account": identity["source_account"],
                    "source_type": identity["source_type"],
                    "platform": identity["platform"],
                }
                for identity in manifest["ordered_candidates"]
            ]
            exact_input.validate_candidate_lineage(candidates, manifest)
            mutations = [
                candidates[:-1],
                candidates + [dict(candidates[-1])],
                [candidates[1], candidates[0], candidates[2]],
                [{**candidates[0], "exact_url": "https://example.com/substitute"}, *candidates[1:]],
                [{**candidates[0], "csv_title": candidates[0]["original_publication_copy"]}, *candidates[1:]],
                [{**candidates[0], "original_publication_copy": candidates[0]["csv_title"]}, *candidates[1:]],
            ]
            for mutated in mutations:
                with self.assertRaises(exact_input.ExactInputError):
                    exact_input.validate_candidate_lineage(mutated, manifest)
        finally:
            temp.cleanup()

    def test_authorized_hash_rejects_missing_extra_reorder_and_substitution(self) -> None:
        temp, root, run_id, path, rows = self.fixture()
        try:
            authorized_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            mutations = [
                rows[:-1],
                rows + [{**rows[-1], "来源链接": "https://example.com/extra", "内容指纹": "fp-extra"}],
                [rows[1], rows[0], rows[2]],
                [{**rows[0], "来源链接": "https://example.com/substitute"}, *rows[1:]],
                [{**rows[0], "原始来源标题": rows[0]["原始发布文案"]}, *rows[1:]],
                [{**rows[0], "原始发布文案": rows[0]["原始来源标题"]}, *rows[1:]],
            ]
            for mutated in mutations:
                with path.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=FIELDS)
                    writer.writeheader()
                    writer.writerows(mutated)
                with self.assertRaisesRegex(exact_input.ExactInputError, "exact_input_sha256_mismatch"):
                    exact_input.load_exact_input(
                        path,
                        run_id=run_id,
                        expected_sha256=authorized_hash,
                        project_root=root,
                    )
        finally:
            temp.cleanup()

    def test_check_only_uses_exact_rows_without_shortlist_or_outputs(self) -> None:
        temp, _, run_id, path, _, _, manifest = self.load(9)
        try:
            args = Namespace(
                exact_input_csv=str(path),
                exact_input_sha256=manifest["input_file_sha256"],
                run_id=run_id,
            )
            with mock.patch.object(machine, "shortlist", side_effect=AssertionError("resampling called")):
                result = machine.check_exact_input(args)
            self.assertTrue(result["ok"])
            self.assertTrue(result["check_only"])
            self.assertEqual(result["row_count"], 9)
            self.assertEqual(result["ordered_candidate_fingerprints"], manifest["ordered_candidate_fingerprints"])
            self.assertFalse(result["source_fetch_started"])
            self.assertFalse(result["writes_feishu"])
            self.assertFalse(result["creates_output_artifacts"])
            self.assertFalse((path.parent / "editorial_state_machine.json").exists())
        finally:
            temp.cleanup()

    def test_prepare_argument_modes_are_explicit_and_legacy_is_unchanged(self) -> None:
        incomplete = Namespace(
            exact_input_csv="/tmp/today_10_topics.csv",
            exact_input_sha256="",
            run_id="run_20260716_080311",
        )
        with self.assertRaisesRegex(exact_input.ExactInputError, "incomplete_exact_input_arguments"):
            machine.prepare_input_candidates(incomplete)
        legacy = Namespace(exact_input_csv="", exact_input_sha256="", run_id="")
        expected = ([Path("/tmp/content.csv")], ["item"], {"pre_skill_pool": ["row"], "candidates": ["row"]})
        with mock.patch.object(machine, "shortlist", return_value=expected) as shortlist:
            csv_paths, items, pre, pool, manifest = machine.prepare_input_candidates(legacy)
        shortlist.assert_called_once_with(legacy)
        self.assertEqual(csv_paths, expected[0])
        self.assertEqual(items, expected[1])
        self.assertEqual(pre, expected[2])
        self.assertEqual(pool, ["row"])
        self.assertIsNone(manifest)

    def test_post_prepare_source_file_mutations_all_fail_closed(self) -> None:
        mutations = {
            "append": lambda path, rows, root: path.write_bytes(path.read_bytes() + b"\n"),
            "content": lambda path, rows, root: self.rewrite(path, [{**rows[0], "来源内容": "changed"}, *rows[1:]]),
            "reorder": lambda path, rows, root: self.rewrite(path, [rows[1], rows[0], *rows[2:]]),
            "url": lambda path, rows, root: self.rewrite(path, [{**rows[0], "来源链接": "https://example.com/substitute"}, *rows[1:]]),
            "title": lambda path, rows, root: self.rewrite(path, [{**rows[0], "原始来源标题": "changed"}, *rows[1:]]),
            "publication": lambda path, rows, root: self.rewrite(path, [{**rows[0], "原始发布文案": "changed"}, *rows[1:]]),
            "truncate": lambda path, rows, root: path.write_bytes(b""),
            "replace": lambda path, rows, root: self.rewrite(path, [rows[-1]]),
            "symlink": self.swap_with_symlink,
        }
        for name, mutate in mutations.items():
            temp, root, _, path, rows, out_dir = self.prepared_state()
            try:
                mutate(path, rows, root)
                with self.subTest(name=name), self.assertRaises(exact_input.ExactInputError):
                    machine.load_state(out_dir)
            finally:
                temp.cleanup()

    def rewrite(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def swap_with_symlink(self, path: Path, rows: list[dict[str, str]], root: Path) -> None:
        replacement = root / "replacement.csv"
        replacement.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(replacement)

    def test_prepared_candidate_manifest_and_state_mutations_fail(self) -> None:
        cases = ("candidate", "manifest", "state", "source_manifest")
        for case in cases:
            temp, _, _, _, _, out_dir = self.prepared_state()
            try:
                if case == "candidate":
                    path = out_dir / "shortlist_candidates.json"
                    value = machine.read_json(path)
                    value[0]["exact_url"] = "https://example.com/substitute"
                elif case == "manifest":
                    path = out_dir / "exact_candidate_input_manifest.json"
                    value = machine.read_json(path)
                    value["ordered_candidates"][0]["exact_url"] = "https://example.com/substitute"
                elif case == "state":
                    path = out_dir / "editorial_state_machine.json"
                    value = machine.read_json(path)
                    value["run_date"] = "2026-07-15"
                else:
                    path = out_dir / "local_source_manifest.json"
                    value = machine.read_json(path)
                    value["source_traces"] = []
                machine.write_json(path, value)
                with self.subTest(case=case), self.assertRaises(exact_input.ExactInputError):
                    machine.load_state(out_dir)
            finally:
                temp.cleanup()

    def test_candidate_local_failure_does_not_replace_or_reorder_survivors(self) -> None:
        temp, _, _, _, _, out_dir = self.prepared_state()
        try:
            state, _ = self.write_eligible_source_files(out_dir, [0, 2])
            patches = self.legacy_pool_sentinels()
            with patches[0] as pool, patches[1] as shortlist, patches[2] as load_items, patches[3] as build_pool:
                rows = machine.eligible_source_rows(out_dir, state)
            self.assertEqual([row["内容指纹"] for row in rows], ["fp-0", "fp-2"])
            for patched in (pool, shortlist, load_items, build_pool):
                patched.assert_not_called()
        finally:
            temp.cleanup()

    def test_all_public_downstream_paths_never_call_legacy_pool(self) -> None:
        self.assert_validate_stage1_uses_exact_pool()
        self.assert_prepare_and_validate_stage2_use_exact_pool()
        self.assert_finalize_uses_exact_pool()

    def assert_validate_stage1_uses_exact_pool(self) -> None:
        temp, _, _, _, _, out_dir = self.prepared_state()
        try:
            state, eligible = self.write_eligible_source_files(out_dir)
            input_payload = {"rows": []}
            machine.write_json(out_dir / "stage1" / "batch_001" / "input.json", input_payload)
            machine.write_json(out_dir / "stage1" / "batch_001" / "output.pending.json", {})
            state["stages"]["prepare_stage1"]["status"] = "completed"
            state["stages"]["stage1"] = machine.stage_record("prepared", batches={
                "batch_001": machine.stage_record(
                    "prepared",
                    input_hash=machine.hash_json(input_payload),
                    start_index=0,
                    row_count=len(eligible),
                )
            })
            machine.save_state(out_dir, state)
            reached = RuntimeError("validate_stage1_reached")
            patches = self.legacy_pool_sentinels()
            with patches[0] as pool, patches[1] as shortlist, patches[2] as load_items, patches[3] as build_pool, \
                    mock.patch.object(machine.runner, "validate_stage1_payload", side_effect=reached):
                with self.assertRaisesRegex(RuntimeError, "validate_stage1_reached"):
                    machine.validate_stage1(Namespace(out_dir=str(out_dir), batch_id="batch_001"))
            for patched in (pool, shortlist, load_items, build_pool):
                patched.assert_not_called()
        finally:
            temp.cleanup()

    def ranked_fixture(self):
        temp, root, run_id, path, rows, out_dir = self.prepared_state()
        state, eligible = self.write_eligible_source_files(out_dir)
        ranked = [{"index": index, "locked_decision": "select"} for index in range(len(eligible))]
        machine.write_json(out_dir / "global_ranking" / "ranked_decisions.json", ranked)
        state["stages"]["global_ranking"].update({
            "status": "completed",
            "ranking_bijection_ok": True,
            "row_count": len(ranked),
            "recommended_count": len(ranked),
        })
        machine.save_state(out_dir, state)
        return temp, out_dir

    def assert_prepare_and_validate_stage2_use_exact_pool(self) -> None:
        temp, out_dir = self.ranked_fixture()
        try:
            patches = self.legacy_pool_sentinels()
            with patches[0] as pool, patches[1] as shortlist, patches[2] as load_items, patches[3] as build_pool:
                result = machine.prepare_stage2(Namespace(out_dir=str(out_dir)))
            self.assertEqual(result["rows"], 3)
            for patched in (pool, shortlist, load_items, build_pool):
                patched.assert_not_called()
            machine.write_json(out_dir / "stage2" / "batch_000" / "output.pending.json", {})
            reached = RuntimeError("validate_stage2_reached")
            patches = self.legacy_pool_sentinels()
            with patches[0] as pool, patches[1] as shortlist, patches[2] as load_items, patches[3] as build_pool, \
                    mock.patch.object(machine.runner, "apply_stage2_payload", side_effect=reached):
                with self.assertRaisesRegex(RuntimeError, "validate_stage2_reached"):
                    machine.validate_stage2(Namespace(out_dir=str(out_dir), batch_id="batch_000"))
            for patched in (pool, shortlist, load_items, build_pool):
                patched.assert_not_called()
        finally:
            temp.cleanup()

    def assert_finalize_uses_exact_pool(self) -> None:
        temp, out_dir = self.ranked_fixture()
        try:
            state = machine.load_state(out_dir)
            state["stages"]["stage2"] = machine.stage_record("completed", batches={
                "batch_000": machine.stage_record("completed", output_hash="a" * 64)
            })
            machine.save_state(out_dir, state)
            machine.write_csv(out_dir / "stage2" / "batch_000" / "skill_rows.csv", [{"内容指纹": "fp-0"}])
            reached = RuntimeError("finalize_reached")
            patches = self.legacy_pool_sentinels()
            with patches[0] as pool, patches[1] as shortlist, patches[2] as load_items, patches[3] as build_pool, \
                    mock.patch.object(machine.replay, "aggregate_replay_outputs", side_effect=reached):
                with self.assertRaisesRegex(RuntimeError, "finalize_reached"):
                    machine.finalize(Namespace(out_dir=str(out_dir)))
            for patched in (pool, shortlist, load_items, build_pool):
                patched.assert_not_called()
        finally:
            temp.cleanup()

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

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import source_ingestion_lineage as lineage
import ar034_recovery_check as recovery_check


RUN_ID = "run_20260716_080311"
STEP_NAME = "fetch daily Douyin homepage title/caption samples through Chrome CDP"


class AR034LegacyDouyinLineageTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path, Path]:
        root = root.resolve()
        daily = root / "output/logs/daily_pipeline_2026-07-16.json"
        probe = root / "output/spikes/douyin_cdp_source_watch_probe/cdp_probe_results.json"
        manual = probe.with_name("content_items_manual.jsonl")
        daily.parent.mkdir(parents=True); probe.parent.mkdir(parents=True)
        rows = [
            {"账号名/公众号名": "good-a", "内容指纹": "fp-a"},
            {"账号名/公众号名": "good-b", "内容指纹": "fp-b"},
        ]
        manual.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
        payload = {
            "status": "completed_with_failures",
            "coverage": {
                "planned_accounts": 3, "attempted_accounts": 3, "successful_accounts": 2,
                "failed_account_count": 1,
                "failed_accounts": [{"account_name": "bad", "artifact_count": 0}],
                "per_account_artifact_counts": {"good-a": 1, "good-b": 1, "bad": 0},
                "invariants": {
                    "attempted_equals_planned": True,
                    "success_plus_failed_equals_attempted": True,
                    "account_lineage_unique_and_complete": True,
                },
            },
            "item_lineage": {"ok": True},
            "resolver": {"manual_jsonl": str(manual), "homepage_card_items": 2},
        }
        probe.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        command = [
            "node", str(root / "scripts/douyin_cdp_source_watch_probe.mjs"),
            "--cdp", "http://127.0.0.1:9333", "--account-limit", "0",
            "--video-limit", "3", "--retries", "2",
        ]
        daily.write_text(json.dumps({
            "run_id": RUN_ID, "generated_at": "2026-07-16T08:08:10",
            "steps": [{
                "name": STEP_NAME, "command": command, "returncode": 0,
                "optional_returncode": 3, "optional_failed": True,
                "started_at": "2026-07-16T08:03:13", "stdout": "truncated",
            }],
        }, ensure_ascii=False), encoding="utf-8")
        timestamp = 1784160459
        os.utime(probe, (timestamp, timestamp)); os.utime(manual, (timestamp, timestamp))
        return daily.resolve(), probe.resolve(), manual.resolve()

    def validate(self, paths: tuple[Path, Path, Path]) -> dict:
        root = paths[0].parent.parent.parent
        return lineage.validate_legacy_partial_source_artifact(*paths, expected_run_id=RUN_ID, expected_root=root)

    def test_explicit_legacy_positive_and_native_strict_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.fixture(Path(tmp)); report = self.validate(paths)
            self.assertTrue(report["legacy_attestation_verified"])
            self.assertEqual(report["ordered_fingerprints"], ["fp-a", "fp-b"])
            with self.assertRaises(lineage.LineageError):
                lineage.validate_partial_source_artifact(json.loads(paths[1].read_text()), paths[2])

    def test_native_envelope_cannot_downgrade_to_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.fixture(Path(tmp)); probe = json.loads(paths[1].read_text())
            probe["run_id"] = RUN_ID; paths[1].write_text(json.dumps(probe))
            with self.assertRaisesRegex(lineage.LineageError, "rejected_for_native"):
                self.validate(paths)

    def test_daily_and_command_mutations_fail(self) -> None:
        mutations = {
            "run": lambda value: value.update(run_id="run_20260716_090000"),
            "duplicate_step": lambda value: value["steps"].append(dict(value["steps"][0])),
            "wrong_step": lambda value: value["steps"][0].update(name="other"),
            "wrong_script": lambda value: value["steps"][0]["command"].__setitem__(1, "/tmp/probe.mjs"),
            "positive_cap": lambda value: value["steps"][0]["command"].__setitem__(6, "3"),
            "equals_alias": lambda value: value["steps"][0].update(command=value["steps"][0]["command"][:4] + ["--account-limit=0"] + value["steps"][0]["command"][7:]),
            "duplicate_arg": lambda value: value["steps"][0]["command"].extend(["--retries", "2"]),
            "wrong_terminal": lambda value: value["steps"][0].update(optional_failed=False),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                paths = self.fixture(Path(tmp)); value = json.loads(paths[0].read_text()); mutate(value)
                paths[0].write_text(json.dumps(value))
                with self.assertRaises(lineage.LineageError): self.validate(paths)

    def test_lineage_content_and_resolver_mutations_fail(self) -> None:
        mutations = {
            "resolver_path": lambda probe, rows: probe["resolver"].update(manual_jsonl="/tmp/manual.jsonl"),
            "resolver_count": lambda probe, rows: probe["resolver"].update(homepage_card_items=3),
            "failed_leak": lambda probe, rows: rows[0].update({"账号名/公众号名": "bad"}),
            "duplicate_fp": lambda probe, rows: rows[1].update({"内容指纹": "fp-a"}),
            "unknown_account": lambda probe, rows: rows[0].update({"账号名/公众号名": "unknown"}),
            "item_lineage": lambda probe, rows: probe["item_lineage"].update(ok=False),
            "count_drift": lambda probe, rows: probe["coverage"]["per_account_artifact_counts"].update({"good-a": 2}),
            "duplicate_failed_account": lambda probe, rows: probe["coverage"].update(
                failed_account_count=2,
                failed_accounts=[{"account_name": "bad", "artifact_count": 0}, {"account_name": "bad", "artifact_count": 0}],
                successful_accounts=1,
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                paths = self.fixture(Path(tmp)); probe = json.loads(paths[1].read_text())
                rows = [json.loads(line) for line in paths[2].read_text().splitlines()]
                mutate(probe, rows); paths[1].write_text(json.dumps(probe))
                paths[2].write_text("\n".join(json.dumps(row) for row in rows) + "\n")
                timestamp = 1784160459; os.utime(paths[1], (timestamp, timestamp)); os.utime(paths[2], (timestamp, timestamp))
                with self.assertRaises(lineage.LineageError): self.validate(paths)

    def test_path_and_time_identity_mutations_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.fixture(Path(tmp)); os.utime(paths[2], (1, 1))
            with self.assertRaisesRegex(lineage.LineageError, "outside_run"): self.validate(paths)
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.fixture(Path(tmp)); target = paths[2].with_suffix(".target")
            paths[2].rename(target); paths[2].symlink_to(target)
            with self.assertRaisesRegex(lineage.LineageError, "path_mismatch"): self.validate(paths)

    def test_owner_mode_and_hard_link_identity_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.fixture(Path(tmp)); paths[2].chmod(0o666)
            with self.assertRaisesRegex(lineage.LineageError, "identity_unsafe"): self.validate(paths)
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.fixture(Path(tmp)); os.link(paths[2], paths[2].with_suffix(".hardlink"))
            with self.assertRaisesRegex(lineage.LineageError, "identity_unsafe"): self.validate(paths)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / "owned.json"; path.write_text("{}")
            fake = SimpleNamespace(st_mode=0o100600, st_nlink=1, st_uid=os.getuid() + 1)
            with mock.patch.object(Path, "lstat", return_value=fake), self.assertRaisesRegex(lineage.LineageError, "identity_unsafe"):
                lineage.safe_legacy_artifact(path, path, "legacy_owner")

    def test_prewrite_revalidation_detects_post_check_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.fixture(Path(tmp)); attested = self.validate(paths)
            rows = [json.loads(line) for line in paths[2].read_text().splitlines()]
            rows.reverse(); paths[2].write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            timestamp = 1784160459; os.utime(paths[2], (timestamp, timestamp))
            with self.assertRaisesRegex(lineage.LineageError, "attestation_drift"):
                lineage.revalidate_legacy_before_external_write(
                    *paths, expected_run_id=RUN_ID, expected_root=paths[0].parent.parent.parent,
                    attested_report=attested,
                )

    def test_prewrite_revalidation_rejects_untrusted_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.fixture(Path(tmp))
            with self.assertRaisesRegex(lineage.LineageError, "report_invalid"):
                lineage.revalidate_legacy_before_external_write(
                    *paths, expected_run_id=RUN_ID, expected_root=paths[0].parent.parent.parent,
                    attested_report={},
                )

    def test_public_cli_rejects_arbitrary_fixture_root_with_typed_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.fixture(Path(tmp)); run_dir = Path(tmp) / "run"; run_dir.mkdir()
            for name in ("content.csv", "topics.csv"):
                (run_dir / name).write_text("来源类型\n", encoding="utf-8")
            command = [
                sys.executable, str(Path(__file__).with_name("ar034_recovery_check.py")),
                "--probe-result", str(paths[1]), "--douyin-manual", str(paths[2]),
                "--incident-content-items", str(run_dir / "content.csv"),
                "--incident-today-candidates", str(run_dir / "topics.csv"), "--check-only",
                "--legacy-daily-log", str(paths[0]), "--expected-source-run-id", RUN_ID,
            ]
            completed = subprocess.run(command, text=True, capture_output=True)
            payload = json.loads(completed.stdout)
            self.assertEqual(completed.returncode, 4); self.assertFalse(payload["ok"]); self.assertEqual(completed.stderr, "")

    def test_exact_external_schema_mutations_fail(self) -> None:
        mutations = {
            "steps_string": lambda daily, probe, rows: daily.update(steps="bad"),
            "command_item": lambda daily, probe, rows: daily["steps"][0]["command"].__setitem__(0, 1),
            "returncode_string": lambda daily, probe, rows: daily["steps"][0].update(returncode="not-an-int"),
            "returncode_bool": lambda daily, probe, rows: daily["steps"][0].update(returncode=False),
            "optional_bool": lambda daily, probe, rows: daily["steps"][0].update(optional_returncode=True),
            "timestamp_wrong_zone": lambda daily, probe, rows: daily["steps"][0].update(started_at="2026-07-16T08:03:13+00:00"),
            "resolver_list": lambda daily, probe, rows: probe.update(resolver=[]),
            "coverage_list": lambda daily, probe, rows: probe.update(coverage=[]),
            "failures_object": lambda daily, probe, rows: probe["coverage"].update(failed_accounts={}),
            "counts_list": lambda daily, probe, rows: probe["coverage"].update(per_account_artifact_counts=[]),
            "invariant_int": lambda daily, probe, rows: probe["coverage"]["invariants"].update(attempted_equals_planned=1),
            "item_lineage_string": lambda daily, probe, rows: probe.update(item_lineage="yes"),
            "manual_fingerprint_int": lambda daily, probe, rows: rows[0].update({"内容指纹": 1}),
            "manual_account_list": lambda daily, probe, rows: rows[0].update({"账号名/公众号名": []}),
            "manual_run_bool": lambda daily, probe, rows: rows[0].update({"运行批次": True}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                paths = self.fixture(Path(tmp)); daily = json.loads(paths[0].read_text()); probe = json.loads(paths[1].read_text())
                rows = [json.loads(line) for line in paths[2].read_text().splitlines()]
                mutate(daily, probe, rows); paths[0].write_text(json.dumps(daily)); paths[1].write_text(json.dumps(probe))
                paths[2].write_text("\n".join(json.dumps(row) for row in rows) + "\n")
                timestamp = 1784160459; os.utime(paths[1], (timestamp, timestamp)); os.utime(paths[2], (timestamp, timestamp))
                with self.assertRaises(lineage.LineageError): self.validate(paths)

    def test_public_cli_malformed_terminal_is_single_typed_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.fixture(Path(tmp)); daily = json.loads(paths[0].read_text())
            daily["steps"][0]["returncode"] = "not-an-int"; paths[0].write_text(json.dumps(daily))
            run_dir = Path(tmp) / "run"; run_dir.mkdir()
            for name in ("content.csv", "topics.csv"): (run_dir / name).write_text("来源类型\n")
            argv = [
                "ar034_recovery_check.py", "--probe-result", str(paths[1]), "--douyin-manual", str(paths[2]),
                "--incident-content-items", str(run_dir / "content.csv"), "--incident-today-candidates", str(run_dir / "topics.csv"),
                "--check-only", "--legacy-daily-log", str(paths[0]), "--expected-source-run-id", RUN_ID,
            ]
            output = io.StringIO()
            with mock.patch.object(sys, "argv", argv), mock.patch.object(recovery_check, "CONFIGURED_PRODUCTION_ROOT", paths[0].parent.parent.parent), contextlib.redirect_stdout(output):
                exit_code = recovery_check.main()
            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 4); self.assertFalse(payload["ok"])
            self.assertIn("legacy_returncode_type_invalid", payload["error"])

    def test_configured_root_identity_mutations_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve(); real = base / "real"; real.mkdir(); (real / "child").mkdir(); link = base / "link"; link.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(lineage.LineageError, "root_symlink"):
                lineage.verify_legacy_production_root(link)
            with self.assertRaisesRegex(lineage.LineageError, "root_alias"):
                lineage.verify_legacy_production_root(link / "child")
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp).resolve() / "file"; file_path.write_text("x")
            with self.assertRaises(lineage.LineageError): lineage.verify_legacy_production_root(file_path)
            with self.assertRaisesRegex(lineage.LineageError, "unavailable"):
                lineage.verify_legacy_production_root(file_path.with_name("missing"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); actual = root.stat()
            wrong_owner = SimpleNamespace(st_mode=actual.st_mode, st_uid=os.getuid() + 1, st_dev=actual.st_dev, st_ino=actual.st_ino)
            with mock.patch.object(lineage.os, "fstat", return_value=wrong_owner), self.assertRaisesRegex(lineage.LineageError, "wrong_owner"):
                lineage.verify_legacy_production_root(root)
            changed = SimpleNamespace(st_mode=actual.st_mode, st_uid=actual.st_uid, st_dev=actual.st_dev, st_ino=actual.st_ino + 1)
            with mock.patch.object(lineage.os, "lstat", side_effect=[actual, changed]), self.assertRaisesRegex(lineage.LineageError, "identity_swap"):
                lineage.verify_legacy_production_root(root)
            identity = lineage.verify_legacy_production_root(root); identity["inode"] += 1
            with self.assertRaisesRegex(lineage.LineageError, "identity_drift"):
                lineage.verify_legacy_production_root(root, identity)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import copy
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
from unittest.mock import patch

import run_daily_workflow
from daily_workflow import DailyWorkflow
from run_daily_workflow import terminal_refresh


RUN_ID = "run_20260817_080000"
BUSINESS_DATE = "2026-08-17"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def script(topic_id: str, suffix: str) -> dict[str, str]:
    return {
        "topic_id": topic_id,
        "title": f"{suffix} title {topic_id}",
        "hook": f"{suffix} hook",
        "structure": f"{suffix} structure",
        "body": f"{suffix} body {topic_id}",
    }


def candidate(index: int) -> dict:
    topic_id = f"trend:refresh-{index}"
    source_id = f"source:refresh-{index}"
    return {
        "candidate_id": topic_id,
        "run_id": RUN_ID,
        "item_id": topic_id,
        "title": f"Refresh topic {index}",
        "source_url": f"https://example.test/{index}",
        "sources": [{
            "source_id": source_id,
            "url": f"https://example.test/{index}",
            "title": f"Refresh source {index}",
            "source_role": "independent_view",
        }],
    }


def editorial(topics: list[dict]) -> dict:
    return {"run_id": RUN_ID, "topics": topics}


class TerminalRunRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "workflow.sqlite3"
        self.artifact_root = self.root / "runs"
        self.workflow = DailyWorkflow(self.db_path)
        self.workflow.begin(RUN_ID, BUSINESS_DATE)
        self.candidates = [candidate(index) for index in range(4)]
        collection = {
            "run_id": RUN_ID,
            "business_date": BUSINESS_DATE,
            "content_items": [
                {"item_id": row["item_id"], "title": row["title"],
                 "source_url": row["source_url"]}
                for row in self.candidates
            ],
            "candidates": self.candidates,
            "hotspot_cards": self.candidates,
            "understanding_results": [],
            "item_failures": [],
        }
        old_topics = [
            {
                "candidate_id": row["candidate_id"],
                "decision": "select",
                "selection_reason": f"old reason {index}",
                "evidence_source_ids": [row["sources"][0]["source_id"]],
                "standalone_eligibility": {
                    "decision": "select", "reason": f"old standalone {index}",
                },
            }
            for index, row in enumerate(self.candidates)
        ]
        self.old_editorial = editorial(old_topics)
        self.new_editorial = editorial([
            {
                "candidate_id": row["candidate_id"],
                "decision": "select",
                "selection_reason": f"new reason {index}",
                "evidence_source_ids": [row["sources"][0]["source_id"]],
                "standalone_eligibility": {
                    "decision": "select", "reason": f"new standalone {index}",
                },
            }
            for index, row in enumerate(self.candidates)
        ])
        self.old_scripts = {
            "run_id": RUN_ID,
            "scripts": [script(row["candidate_id"], "old") for row in self.candidates],
            "failures": [],
        }
        self.workflow.commit_stage(
            RUN_ID, "collection_enrichment", collection, "completed",
        )
        self.workflow.commit_stage(
            RUN_ID, "editorial", self.old_editorial, "completed",
        )
        self.workflow.commit_stage(
            RUN_ID, "scripts", self.old_scripts, "completed",
        )
        self.workflow.complete(RUN_ID, "completed", f"terminal:{RUN_ID}")
        self.workflow.mark_publish(RUN_ID, "applied")
        handoff = self.artifact_root / RUN_ID / "workflow_handoff.json"
        write_json(handoff, {
            "schema_version": 1, "run_id": RUN_ID,
            "business_date": BUSINESS_DATE, "action": "completed",
        })
        self.script_path = self.artifact_root / RUN_ID / "scripts" / "old.md"
        self.script_path.parent.mkdir(parents=True, exist_ok=True)
        self.script_path.write_text("old artifact", encoding="utf-8")
        self.args = SimpleNamespace(
            run_id=RUN_ID,
            business_date=BUSINESS_DATE,
            workflow_db=self.db_path,
            artifact_root=self.artifact_root,
            editorial_result_file=self.root / "new_editorial.json",
        )
        write_json(self.args.editorial_result_file, self.new_editorial)

    def tearDown(self) -> None:
        self.workflow.db.close()
        self.temp.cleanup()

    def artifact_snapshot(self) -> dict[str, bytes]:
        return {
            str(path.relative_to(self.artifact_root)): path.read_bytes()
            for path in self.artifact_root.rglob("*")
            if path.is_file()
        }

    def cli_argv(
        self, run_id: str = RUN_ID, business_date: str = BUSINESS_DATE,
    ) -> list[str]:
        return [
            str(Path(__file__).with_name("run_daily_workflow.py")),
            "--terminal-refresh",
            "--run-id", run_id,
            "--business-date", business_date,
            "--workflow-db", str(self.db_path),
            "--artifact-root", str(self.artifact_root),
            "--editorial-result-file", str(self.args.editorial_result_file),
            "--video-mode", "disabled",
        ]

    def run_public_cli(
        self, run_id: str = RUN_ID, business_date: str = BUSINESS_DATE,
    ) -> subprocess.CompletedProcess[str]:
        self.workflow.db.close()
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, *self.cli_argv(run_id, business_date)],
            cwd=Path(__file__).resolve().parent.parent,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.workflow = DailyWorkflow(self.db_path)
        return result

    def assert_typed_cli_error(
        self, result: subprocess.CompletedProcess[str], error: str,
    ) -> None:
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        self.assertNotIn("Traceback", result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload, {
            "ok": False,
            "action": "terminal_refresh_failed",
            "error": error,
        })

    def test_refresh_backs_up_old_state_and_returns_scripts_required(self) -> None:
        old_db = self.db_path.read_bytes()
        result = terminal_refresh(self.args, self.workflow)
        self.assertEqual(result["refresh_action"], "applied")
        self.assertEqual(result["selected_count"], 4)
        backup = Path(result["backup_path"])
        manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
        roles = {entry["role"] for entry in manifest["files"]}
        self.assertTrue({"terminal_metadata", "stage_snapshot", "workflow_handoff", "script_artifact"} <= roles)
        self.assertEqual((backup / "files" / "stages" / "editorial.json").is_file(), True)
        self.assertEqual((backup / "files" / "stages" / "scripts.json").is_file(), True)
        self.assertEqual(self.script_path.exists(), False)
        state = self.workflow.read_run(RUN_ID)
        self.assertEqual(state["run"]["status"], "waiting")
        self.assertEqual(state["run"]["publish_status"], "not_ready")
        self.assertIsNone(self.workflow.stage(RUN_ID, "scripts"))
        stored_editorial = self.workflow.stage(RUN_ID, "editorial")["payload"]
        self.assertEqual(
            [row["candidate_id"] for row in stored_editorial["topics"]],
            [row["candidate_id"] for row in self.new_editorial["topics"]],
        )
        self.assertTrue(all(
            row["decision"] == "select" for row in stored_editorial["topics"]
        ))
        self.assertNotEqual(self.db_path.read_bytes(), old_db)

        replay = terminal_refresh(self.args, self.workflow)
        self.assertEqual(replay["refresh_action"], "noop")
        self.assertEqual(len(list((self.artifact_root / RUN_ID / "revision_backups").glob("revision_*"))), 1)

    def test_invalid_eleven_selects_fail_before_backup_or_mutation(self) -> None:
        extra = [candidate(index) for index in range(4, 11)]
        invalid = copy.deepcopy(self.new_editorial)
        invalid["topics"].extend([
            {
                "candidate_id": row["candidate_id"],
                "decision": "select",
                "selection_reason": f"extra reason {index}",
                "evidence_source_ids": [row["sources"][0]["source_id"]],
                "standalone_eligibility": {
                    "decision": "select", "reason": f"extra standalone {index}",
                },
            }
            for index, row in enumerate(extra, start=4)
        ])
        # The full-pool validator rejects the unknown identities before any backup.
        write_json(self.args.editorial_result_file, invalid)
        before = self.db_path.read_bytes()
        with self.assertRaisesRegex(Exception, "editorial_result_coverage_incomplete"):
            terminal_refresh(self.args, self.workflow)
        self.assertEqual(self.db_path.read_bytes(), before)
        self.assertFalse((self.artifact_root / RUN_ID / "revision_backups").exists())

    def test_refresh_transaction_failure_restores_old_artifacts(self) -> None:
        before = self.script_path.read_text(encoding="utf-8")
        before_db = self.db_path.read_bytes()
        self.workflow.db.close()
        output = io.StringIO()
        with patch.object(
            DailyWorkflow,
            "refresh_terminal_run",
            side_effect=RuntimeError("injected transaction failure"),
        ), patch.object(sys, "argv", self.cli_argv()), contextlib.redirect_stdout(output):
            exit_code = run_daily_workflow.main()
        self.workflow = DailyWorkflow(self.db_path)
        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(output.getvalue()), {
            "ok": False,
            "action": "terminal_refresh_failed",
            "error": "terminal_refresh_transaction_failed",
        })
        self.assertEqual(self.script_path.read_text(encoding="utf-8"), before)
        self.assertEqual(self.db_path.read_bytes(), before_db)
        state = self.workflow.read_run(RUN_ID)
        self.assertEqual(state["run"]["status"], "completed")
        self.assertEqual(state["run"]["publish_status"], "applied")
        self.assertIsNotNone(self.workflow.stage(RUN_ID, "scripts"))
        self.assertTrue((self.artifact_root / RUN_ID / "revision_backups").is_dir())

    def test_wrong_date_is_typed_and_does_not_mark_recoverable(self) -> None:
        before_db = self.db_path.read_bytes()
        before_artifacts = self.artifact_snapshot()
        result = self.run_public_cli(business_date="2026-08-18")
        self.assert_typed_cli_error(result, "wrong_business_date")
        self.assertEqual(self.db_path.read_bytes(), before_db)
        self.assertEqual(self.artifact_snapshot(), before_artifacts)

    def test_missing_run_is_typed_without_mutation(self) -> None:
        before_db = self.db_path.read_bytes()
        before_artifacts = self.artifact_snapshot()
        result = self.run_public_cli(run_id="run_20260817_080001")
        self.assert_typed_cli_error(result, "terminal_refresh_run_missing")
        self.assertEqual(self.db_path.read_bytes(), before_db)
        self.assertEqual(self.artifact_snapshot(), before_artifacts)

    def test_backup_filesystem_failure_is_typed_without_mutation(self) -> None:
        blocker = self.artifact_root / RUN_ID / "revision_backups"
        blocker.write_text("not a directory", encoding="utf-8")
        before_db = self.db_path.read_bytes()
        before_artifacts = self.artifact_snapshot()
        result = self.run_public_cli()
        self.assert_typed_cli_error(result, "terminal_refresh_backup_failed")
        self.assertEqual(self.db_path.read_bytes(), before_db)
        self.assertEqual(self.artifact_snapshot(), before_artifacts)

    def test_non_terminal_run_is_rejected_without_backup(self) -> None:
        pending_db = self.root / "pending.sqlite3"
        pending = DailyWorkflow(pending_db)
        pending.begin(RUN_ID, BUSINESS_DATE)
        args = copy.copy(self.args)
        args.workflow_db = pending_db
        with self.assertRaisesRegex(Exception, "terminal_refresh_run_not_terminal"):
            terminal_refresh(args, pending)
        self.assertFalse((self.root / "runs" / RUN_ID / "revision_backups").exists())
        pending.db.close()


if __name__ == "__main__":
    unittest.main()

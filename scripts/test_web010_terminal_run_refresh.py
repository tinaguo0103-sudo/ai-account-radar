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
from daily_workflow import DailyWorkflow, canonical
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
    return {"run_id": RUN_ID, "business_date": BUSINESS_DATE, "topics": topics}


class TerminalRunRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "workflow.sqlite3"
        self.artifact_root = self.root / "runs"
        self.workflow = DailyWorkflow(self.db_path)
        self.workflow.begin(RUN_ID, BUSINESS_DATE)
        self.candidates = [candidate(index) for index in range(8)]
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
        stored_old_editorial = {
            **self.old_editorial,
            "topics": run_daily_workflow.complete_editorial_ledger(
                self.candidates, self.old_editorial["topics"],
            ),
        }
        self.workflow.commit_stage(
            RUN_ID, "editorial", stored_old_editorial, "completed",
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
        self,
        run_id: str = RUN_ID,
        business_date: str = BUSINESS_DATE,
        *,
        scripts_only: bool = False,
    ) -> list[str]:
        refresh_flag = (
            "--scripts-only-terminal-refresh"
            if scripts_only else "--terminal-refresh"
        )
        return [
            str(Path(__file__).with_name("run_daily_workflow.py")),
            refresh_flag,
            "--run-id", run_id,
            "--business-date", business_date,
            "--workflow-db", str(self.db_path),
            "--artifact-root", str(self.artifact_root),
            "--editorial-result-file", str(self.args.editorial_result_file),
            "--video-mode", "disabled",
        ]

    def run_public_cli(
        self,
        run_id: str = RUN_ID,
        business_date: str = BUSINESS_DATE,
        *,
        scripts_only: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        self.workflow.db.close()
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, *self.cli_argv(
                run_id, business_date, scripts_only=scripts_only,
            )],
            cwd=Path(__file__).resolve().parent.parent,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.workflow = DailyWorkflow(self.db_path)
        return result

    def scripts_only_args(self) -> SimpleNamespace:
        args = copy.copy(self.args)
        args.scripts_only_terminal_refresh = True
        return args

    def seed_revision_001(self) -> Path:
        return run_daily_workflow._write_refresh_backup(
            self.args,
            self.workflow,
            self.workflow.stage(RUN_ID, "editorial")["payload"],
        )

    def run_public_script_item(self, index: int) -> tuple[int, dict, int]:
        collection = self.workflow.stage(RUN_ID, "collection_enrichment")["payload"]
        editorial_value = self.workflow.stage(RUN_ID, "editorial")["payload"]
        handoff = run_daily_workflow.build_scripts_handoff(
            RUN_ID, BUSINESS_DATE, collection, editorial_value,
        )
        topic = handoff["selected_topics"][index]
        checkpoint = self.workflow.stage(RUN_ID, "scripts")
        completed_count = len(
            (checkpoint or {"payload": {"completed_items": []}})["payload"].get(
                "completed_items", [],
            )
        )
        contract = run_daily_workflow.script_runtime.load_writer_contract()
        packet = run_daily_workflow.script_runtime.topic_packet(
            RUN_ID, BUSINESS_DATE, topic, index, len(handoff["selected_topics"]),
            completed_count, contract,
        )
        item_file = self.root / f"script_item_{index}.json"
        write_json(item_file, {
            "packet_id": packet["topic_input"]["packet_id"],
            "script": script(topic["topic_id"], "frozen-qa"),
        })
        self.workflow.db.close()
        output = io.StringIO()
        posts: list[tuple[Path, str]] = []
        argv = [
            str(Path(__file__).with_name("run_daily_workflow.py")),
            "--run-id", RUN_ID,
            "--business-date", BUSINESS_DATE,
            "--workflow-db", str(self.db_path),
            "--artifact-root", str(self.artifact_root),
            "--script-item-file", str(item_file),
            "--video-mode", "disabled",
        ]

        def fake_publish(db_path: Path, run_id: str) -> None:
            posts.append((Path(db_path), run_id))

        with patch.object(run_daily_workflow, "publish_terminal", side_effect=fake_publish), \
                patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
            exit_code = run_daily_workflow.main()
        self.workflow = DailyWorkflow(self.db_path)
        return exit_code, json.loads(output.getvalue().splitlines()[-1]), len(posts)

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
        self.assertEqual(result["selected_count"], 8)
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

    def test_default_identical_refresh_remains_noop_with_existing_revision(self) -> None:
        self.args.editorial_result_file = self.root / "same_editorial.json"
        write_json(self.args.editorial_result_file, self.old_editorial)
        revision_001 = self.seed_revision_001()
        before_revision = (revision_001 / "manifest.json").read_bytes()
        result = terminal_refresh(self.args, self.workflow)
        self.assertEqual(result["refresh_action"], "noop")
        self.assertEqual(result["backup_path"], str(revision_001))
        self.assertEqual((revision_001 / "manifest.json").read_bytes(), before_revision)
        self.assertEqual(
            sorted(path.name for path in (self.artifact_root / RUN_ID / "revision_backups").iterdir()),
            ["revision_001"],
        )

    def test_scripts_only_creates_revision_002_and_duplicate_resumes(self) -> None:
        self.args.editorial_result_file = self.root / "same_editorial.json"
        write_json(self.args.editorial_result_file, self.old_editorial)
        revision_001 = self.seed_revision_001()
        collection_before = canonical(
            self.workflow.stage(RUN_ID, "collection_enrichment")["payload"],
        )
        editorial_before = canonical(
            self.workflow.stage(RUN_ID, "editorial"),
        )
        args = self.scripts_only_args()
        first = terminal_refresh(args, self.workflow)
        self.assertEqual(first["refresh_action"], "applied")
        self.assertEqual(first["refresh_mode"], "scripts_only")
        revision_002 = Path(first["backup_path"])
        self.assertEqual(revision_002.name, "revision_002")
        manifest = json.loads((revision_002 / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["refresh_mode"], "scripts_only")
        self.assertFalse(manifest["secret_material_included"])
        state = self.workflow.read_run(RUN_ID)
        self.assertEqual(state["run"]["status"], "waiting")
        self.assertEqual(state["run"]["publish_status"], "not_ready")
        self.assertIsNone(self.workflow.stage(RUN_ID, "scripts"))
        self.assertEqual(
            canonical(self.workflow.stage(RUN_ID, "collection_enrichment")["payload"]),
            collection_before,
        )
        self.assertEqual(canonical(self.workflow.stage(RUN_ID, "editorial")), editorial_before)
        self.assertEqual(
            sorted(path.name for path in (self.artifact_root / RUN_ID / "revision_backups").iterdir()),
            ["revision_001", "revision_002"],
        )
        duplicate = terminal_refresh(args, self.workflow)
        self.assertEqual(duplicate["refresh_action"], "resume")
        self.assertEqual(duplicate["backup_path"], str(revision_002))
        self.assertEqual(
            sorted(path.name for path in (self.artifact_root / RUN_ID / "revision_backups").iterdir()),
            ["revision_001", "revision_002"],
        )

    def test_scripts_only_public_eight_topics_publisher_once_and_replay_noop(self) -> None:
        self.args.editorial_result_file = self.root / "same_editorial.json"
        write_json(self.args.editorial_result_file, self.old_editorial)
        self.seed_revision_001()
        args = self.scripts_only_args()
        first = terminal_refresh(args, self.workflow)
        self.assertEqual(first["refresh_action"], "applied")
        posts = 0
        for index in range(8):
            exit_code, summary, post_count = self.run_public_script_item(index)
            self.assertEqual(exit_code, 0)
            posts += post_count
            self.assertEqual(post_count, 0 if index < 7 else 1)
            if index < 7:
                self.assertEqual(summary["action"], "scripts_required")
        self.assertEqual(posts, 1)
        state = self.workflow.read_run(RUN_ID)
        self.assertEqual(state["run"]["status"], "completed")
        self.assertEqual(state["run"]["publish_status"], "applied")
        scripts_stage = self.workflow.stage(RUN_ID, "scripts")
        self.assertEqual(len(scripts_stage["payload"]["scripts"]), 8)
        self.assertEqual(len(list((self.artifact_root / RUN_ID / "scripts").glob("*.md"))), 8)

        explicit_replay = terminal_refresh(args, self.workflow)
        self.assertEqual(explicit_replay["refresh_action"], "noop")
        self.assertEqual(
            len(list((self.artifact_root / RUN_ID / "revision_backups").glob("revision_*"))),
            2,
        )
        public_explicit = self.run_public_cli(scripts_only=True)
        public_summary = json.loads(public_explicit.stdout.splitlines()[-1])
        self.assertEqual(public_summary["action"], "noop")
        self.assertEqual(public_summary["status"], "completed")
        self.assertEqual(public_summary["publish_status"], "applied")
        handoff = json.loads(
            (self.artifact_root / RUN_ID / "workflow_handoff.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(handoff["action"], "noop")
        self.assertEqual(handoff["status"], "completed")
        self.assertEqual(handoff["publish_status"], "applied")
        replay_output = io.StringIO()
        replay_argv = [
            str(Path(__file__).with_name("run_daily_workflow.py")),
            "--run-id", RUN_ID,
            "--business-date", BUSINESS_DATE,
            "--workflow-db", str(self.db_path),
            "--artifact-root", str(self.artifact_root),
            "--video-mode", "disabled",
        ]
        replay_posts: list[tuple[Path, str]] = []
        with patch.object(run_daily_workflow, "publish_terminal", side_effect=lambda db, run: replay_posts.append((Path(db), run))), \
                patch.object(sys, "argv", replay_argv), contextlib.redirect_stdout(replay_output):
            self.assertEqual(run_daily_workflow.main(), 0)
        self.workflow = DailyWorkflow(self.db_path)
        self.assertEqual(json.loads(replay_output.getvalue().splitlines()[-1])["action"], "noop")
        self.assertEqual(replay_posts, [])

    def test_scripts_only_rejects_editorial_and_selected_identity_changes(self) -> None:
        changed = copy.deepcopy(self.old_editorial)
        changed["topics"][0]["selection_reason"] = "changed semantic reason"
        changed_path = self.root / "changed_editorial.json"
        write_json(changed_path, changed)
        args = self.scripts_only_args()
        args.editorial_result_file = changed_path
        before_db = self.db_path.read_bytes()
        with self.assertRaisesRegex(Exception, "terminal_refresh_editorial_changed"):
            terminal_refresh(args, self.workflow)
        self.assertEqual(self.db_path.read_bytes(), before_db)

        selected_change = copy.deepcopy(self.old_editorial)
        selected_change["topics"][0]["decision"] = "observe"
        selected_change["topics"][0]["standalone_eligibility"] = {
            "decision": "observe", "reason": "changed selected identity",
        }
        selected_change["topics"][0]["selection_reason"] = "candidate-local observe reason"
        selected_path = self.root / "selected_change.json"
        write_json(selected_path, selected_change)
        args.editorial_result_file = selected_path
        with self.assertRaisesRegex(Exception, "terminal_refresh_selected_identity_changed"):
            terminal_refresh(args, self.workflow)
        self.assertEqual(self.db_path.read_bytes(), before_db)

    def test_scripts_only_requires_valid_prior_revision_authority(self) -> None:
        self.args.editorial_result_file = self.root / "same_editorial.json"
        write_json(self.args.editorial_result_file, self.old_editorial)
        args = self.scripts_only_args()
        with self.assertRaisesRegex(Exception, "terminal_refresh_revision_authority_missing"):
            terminal_refresh(args, self.workflow)
        revision_root = self.artifact_root / RUN_ID / "revision_backups" / "revision_001"
        revision_root.mkdir(parents=True)
        (revision_root / "manifest.json").write_text("{bad", encoding="utf-8")
        with self.assertRaisesRegex(Exception, "terminal_refresh_revision_authority_invalid"):
            terminal_refresh(args, self.workflow)

    def test_scripts_only_rejects_corrupt_revision_snapshot(self) -> None:
        self.args.editorial_result_file = self.root / "same_editorial.json"
        write_json(self.args.editorial_result_file, self.old_editorial)
        revision_root = self.seed_revision_001()
        snapshot = revision_root / "files" / "stages" / "scripts.json"
        snapshot.write_text("{bad", encoding="utf-8")
        with self.assertRaisesRegex(Exception, "terminal_refresh_revision_authority_invalid"):
            terminal_refresh(self.scripts_only_args(), self.workflow)

    def test_scripts_only_backup_and_transaction_fail_without_mutation(self) -> None:
        self.args.editorial_result_file = self.root / "same_editorial.json"
        write_json(self.args.editorial_result_file, self.old_editorial)
        self.seed_revision_001()
        args = self.scripts_only_args()
        before_db = self.db_path.read_bytes()
        before_artifact = self.script_path.read_text(encoding="utf-8")
        with patch.object(
            run_daily_workflow.tempfile, "mkdtemp", side_effect=OSError("backup unavailable"),
        ), self.assertRaisesRegex(Exception, "terminal_refresh_backup_failed"):
            terminal_refresh(args, self.workflow)
        self.assertEqual(self.db_path.read_bytes(), before_db)
        self.assertEqual(self.script_path.read_text(encoding="utf-8"), before_artifact)

        with patch.object(
            DailyWorkflow,
            "refresh_terminal_run",
            side_effect=RuntimeError("injected scripts-only transaction failure"),
        ), self.assertRaisesRegex(Exception, "terminal_refresh_transaction_failed"):
            terminal_refresh(args, self.workflow)
        self.assertEqual(self.db_path.read_bytes(), before_db)
        self.assertEqual(self.script_path.read_text(encoding="utf-8"), before_artifact)

    def test_scripts_only_lock_occupied_is_typed(self) -> None:
        self.args.editorial_result_file = self.root / "same_editorial.json"
        write_json(self.args.editorial_result_file, self.old_editorial)
        self.seed_revision_001()
        lock = run_daily_workflow.WorkflowExecutionLock(self.db_path)
        self.assertTrue(lock.acquire())
        try:
            result = self.run_public_cli(scripts_only=True)
        finally:
            lock.release()
        self.assert_typed_cli_error(result, "terminal_refresh_lock_occupied")

    def test_invalid_eleven_selects_fail_before_backup_or_mutation(self) -> None:
        extra = [candidate(index) for index in range(8, 11)]
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
            for index, row in enumerate(extra, start=8)
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

    def test_scripts_only_rejects_unpublished_terminal_run(self) -> None:
        self.args.editorial_result_file = self.root / "same_editorial.json"
        write_json(self.args.editorial_result_file, self.old_editorial)
        self.workflow.complete(RUN_ID, "completed", "terminal:unpublished")
        before_db = self.db_path.read_bytes()
        with self.assertRaisesRegex(Exception, "terminal_refresh_run_not_published"):
            terminal_refresh(self.scripts_only_args(), self.workflow)
        self.assertEqual(self.db_path.read_bytes(), before_db)
        self.assertFalse((self.artifact_root / RUN_ID / "revision_backups").exists())


if __name__ == "__main__":
    unittest.main()

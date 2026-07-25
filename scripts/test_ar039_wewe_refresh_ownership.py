from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import start_wewe_rss
import wewe_runtime_contract as contract
import daily_pipeline


ROOT = Path(__file__).resolve().parents[1]


def completed(payload, returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=json.dumps([payload]), stderr="")


class WeWeRefreshOwnershipTests(unittest.TestCase):
    def image_payload(self):
        return {"Config": {"Labels": dict(contract.REQUIRED_LABELS), "Env": ["DATABASE_TYPE=sqlite"]}}

    def container_payload(self):
        return {"Config": {"Image": contract.IMAGE, "Labels": dict(contract.REQUIRED_LABELS), "Env": ["DATABASE_TYPE=sqlite"]}}

    def test_project_image_has_scheduler_disabled_contract(self):
        result = contract.verify_image(runner=lambda command: completed(self.image_payload()))
        self.assertTrue(result["ok"])
        self.assertEqual(result["internal_scheduler"], "disabled_at_build")

    def test_old_or_cron_configured_image_is_rejected(self):
        self.assertFalse(contract.verify_image("cooderl/wewe-rss-sqlite:latest")["ok"])
        payload = self.image_payload(); payload["Config"]["Env"].append("CRON_EXPRESSION=35 5,17 * * *")
        self.assertEqual(contract.verify_image(runner=lambda command: completed(payload))["status"], "internal_scheduler_configuration_present")

    def test_container_readback_requires_exact_image_and_labels(self):
        self.assertTrue(contract.verify_container("provider", runner=lambda command: completed(self.container_payload()))["ok"])
        payload = self.container_payload(); payload["Config"]["Labels"] = {}
        self.assertEqual(contract.verify_container("provider", runner=lambda command: completed(payload))["status"], "internal_scheduler_not_disabled")

    def test_create_command_has_owner_labels_and_no_cron(self):
        with mock.patch.object(start_wewe_rss, "run", return_value=subprocess.CompletedProcess([], 0, "id", "")) as run:
            start_wewe_rss.create_container("provider", contract.IMAGE, "http://127.0.0.1:4000", Path("/tmp/data"), "secret")
        command = run.call_args.args[0]
        self.assertNotIn("CRON_EXPRESSION", " ".join(command))
        self.assertIn("ai-account-radar.wewe-refresh-owner=project-signed-adapter-only", command)
        self.assertEqual(command[-1], contract.IMAGE)

    def test_upstream_patch_removes_only_internal_cron_surface(self):
        patch = (ROOT / "providers/wewe-rss-no-internal-cron/disable-internal-refresh-cron.patch").read_text(encoding="utf-8")
        self.assertIn("handleUpdateFeedsCron", patch)
        self.assertIn("@Cron", patch)
        self.assertNotIn("refreshArticles", patch)
        dockerfile = (ROOT / "providers/wewe-rss-no-internal-cron/Dockerfile").read_text(encoding="utf-8")
        self.assertIn(contract.UPSTREAM_COMMIT, dockerfile)
        self.assertIn("git apply --unidiff-zero --check", dockerfile)
        self.assertIn("corepack prepare pnpm@8.15.9 --activate", dockerfile)
        self.assertIn('test "$(pnpm --version)" = "8.15.9"', dockerfile)
        self.assertIn("COREPACK_ENABLE_PROJECT_SPEC=0", dockerfile)
        self.assertNotIn("npm i -g pnpm", dockerfile)

    def test_daily_path_no_longer_calls_archived_wewe_runtime(self):
        source = (ROOT / "scripts/daily_pipeline.py").read_text(encoding="utf-8")
        self.assertNotIn('"wewe_provider_refresh.py"', source)
        self.assertNotIn('"wewe_provider_health.py"', source)
        self.assertNotIn('"wewe_current_feed_reader.py"', source)
        self.assertIn('"wechat_public_fulltext_source.py"', source)

    def test_401_remains_wechat_source_local_with_machine_reason(self):
        step = {"name": "request fixed wewe-rss provider refresh", "returncode": 4, "stderr": "HTTP 401 / -2041"}
        daily_pipeline.isolate_source_failure(
            step, source="wechat", state="login_required", reason="provider_http_401_account_invalid",
        )
        self.assertEqual(step["returncode"], 0)
        self.assertTrue(step["source_local_failure"])
        self.assertEqual(step["source_outcome"], "login_required")
        self.assertEqual(step["source_rows"], 0)
        self.assertEqual(step["source_failure_reason"], "provider_http_401_account_invalid")


if __name__ == "__main__":
    unittest.main()

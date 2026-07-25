from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import run_daily_collection_job
import daily_pipeline

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "wechat_public_fulltext_source",
    ROOT / "scripts" / "wechat_public_fulltext_source.py",
)
assert SPEC and SPEC.loader
wechat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wechat)


class Item:
    source_type = "公众号文章"
    platform = "微信公众号"
    account_name = "数字生命卡兹克"
    content_title = "Exact article"
    content_url = "https://mp.weixin.qq.com/s?x=1"
    content_shape = "长文"
    body_or_transcript = "正文" * 300
    summary_or_description = "摘要"
    published_at = "2026-07-25T08:00:00"
    raw_payload_path = "/private/tmp/raw.md"
    fetch_method = "wechat_public_html_js_content"
    fetch_status = "success"
    failure_reason = ""
    content_fingerprint = "fp-exact"


class AR047SourceReliabilityTests(unittest.TestCase):
    def test_public_discovery_requires_exact_account_and_wechat_url(self) -> None:
        page = """
        <article class="article" data-account="数字生命卡兹克">
          <a class="article-title" href="http://mp.weixin.qq.com/s?x=1#rd">Exact article</a>
        </article>
        <article class="article" data-account="其他账号">
          <a class="article-title" href="https://mp.weixin.qq.com/s?x=2">Other</a>
        </article>
        <article class="article" data-account="数字生命卡兹克">
          <a class="article-title" href="https://example.com/not-wechat">Wrong host</a>
        </article>
        """
        rows = wechat.discover_articles(page, "数字生命卡兹克")
        self.assertEqual(2, len(rows))
        self.assertEqual("https://mp.weixin.qq.com/s?x=1", rows[0]["url"])
        self.assertEqual("https://example.com/not-wechat", rows[1]["url"])

    def test_public_source_is_idempotent_and_never_uses_wewe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            config.write_text(json.dumps({
                "sources": [{
                    "source_id": "wechat:kazike",
                    "account_name": "数字生命卡兹克",
                    "discovery_url": "https://discovery.invalid/",
                    "enabled": True,
                }]
            }), encoding="utf-8")
            page = """
            <article class="article" data-account="数字生命卡兹克">
              <a class="article-title" href="https://mp.weixin.qq.com/s?x=1">Exact article</a>
            </article>
            """
            argv = [
                "wechat_public_fulltext_source.py",
                "--config", str(config),
                "--run-id", "run_20260725_080000",
                "--out-dir", str(root / "out"),
                "--seen-ledger", str(root / "seen.json"),
            ]
            with mock.patch.object(wechat.sys, "argv", argv), \
                 mock.patch.object(wechat, "fetch_text", return_value=page), \
                 mock.patch.object(wechat, "resolve_wechat", return_value=[Item()]):
                self.assertEqual(0, wechat.main())
                first = json.loads((root / "out" / "result.json").read_text())
                self.assertEqual(1, first["rows"])
                self.assertFalse(first["legacy_wewe_used"])
                self.assertEqual(0, wechat.main())
                second = json.loads((root / "out" / "result.json").read_text())
                self.assertEqual(0, second["rows"])
                self.assertEqual("updated_no_new_items", second["outcomes"][0]["status"])

    def test_public_source_rejects_stale_article_without_substitute(self) -> None:
        stale = Item()
        stale.published_at = "2026-07-22T08:00:00"
        outcome, rows = self._collect(stale)
        self.assertEqual([], rows)
        self.assertEqual("no_current_day_article", outcome["status"])
        self.assertFalse(outcome["ok"])
        self.assertEqual(0, outcome["artifact_count"])
        self.assertEqual(0, outcome["substitute_count"])

    def test_public_source_has_one_terminal_outcome_for_each_failure(self) -> None:
        cases = [
            ("article_fulltext_too_short", {"body_or_transcript": "短" * 499}),
            ("article_account_mismatch", {"account_name": "其他账号"}),
            ("article_title_mismatch", {"content_title": "Other title"}),
        ]
        for expected, overrides in cases:
            with self.subTest(expected=expected):
                item = Item()
                for key, value in overrides.items():
                    setattr(item, key, value)
                outcome, rows = self._collect(item)
                self.assertEqual([], rows)
                self.assertEqual(expected, outcome["status"])
                self.assertFalse(outcome["ok"])
                self.assertNotEqual("updated_no_new_items", outcome["status"])

        source = {
            "source_id": "wechat:kazike",
            "account_name": "数字生命卡兹克",
            "discovery_url": "https://discovery.invalid/",
        }
        wrong_host = """
        <article class="article" data-account="数字生命卡兹克">
          <a class="article-title" href="https://example.com/a">Exact article</a>
        </article>
        """
        with mock.patch.object(wechat, "fetch_text", return_value=wrong_host):
            outcome, rows = wechat.collect_source(source, "run_20260725_080000", Path("/tmp/raw"), {"urls": {}}, 1)
        self.assertEqual("article_url_wrong_host", outcome["status"])
        self.assertFalse(outcome["ok"])
        self.assertEqual([], rows)

        with mock.patch.object(wechat, "fetch_text", side_effect=OSError("offline")):
            outcome, rows = wechat.collect_source(source, "run_20260725_080000", Path("/tmp/raw"), {"urls": {}}, 1)
        self.assertEqual("discovery_failed", outcome["status"])
        self.assertFalse(outcome["ok"])
        self.assertEqual([], rows)

    def _collect(self, item: Item) -> tuple[dict, list[dict]]:
        source = {
            "source_id": "wechat:kazike",
            "account_name": "数字生命卡兹克",
            "discovery_url": "https://discovery.invalid/",
        }
        page = """
        <article class="article" data-account="数字生命卡兹克">
          <a class="article-title" href="https://mp.weixin.qq.com/s?x=1">Exact article</a>
        </article>
        """
        with mock.patch.object(wechat, "fetch_text", return_value=page), \
             mock.patch.object(wechat, "resolve_wechat", return_value=[item]):
            return wechat.collect_source(source, "run_20260725_080000", Path("/tmp/raw"), {"urls": {}}, 1)

    def test_normal_entrypoint_has_no_wewe_runtime_call(self) -> None:
        pipeline = (ROOT / "scripts" / "daily_pipeline.py").read_text(encoding="utf-8")
        outer = (ROOT / "scripts" / "run_daily_collection_job.py").read_text(encoding="utf-8")
        self.assertNotIn("wewe_provider_refresh.py", pipeline)
        self.assertNotIn("wewe_provider_health.py", pipeline)
        self.assertNotIn("wewe_current_feed_reader.py", pipeline)
        self.assertIn("--fetch-wechat-public-fulltext", outer)
        self.assertNotIn("--fetch-wechat-fulltext-provider", outer)

    def test_daily_pipeline_preserves_wechat_terminal_failure(self) -> None:
        state, reason = daily_pipeline.wechat_terminal_failure({
            "status": "completed_with_failures",
            "outcomes": [{
                "status": "no_current_day_article",
                "ok": False,
                "reason": "",
                "rows": 0,
            }],
        })
        self.assertEqual("no_current_day_article", state)
        self.assertEqual("no_current_day_article", reason)

    def test_production_shaped_plan_excludes_two_wrong_platform_accounts(self) -> None:
        plan = run_daily_collection_job.scheduled_collection_plan(
            ROOT / "config" / "content_sources.yaml",
            0,
        )
        self.assertEqual(31, plan["planned_douyin_accounts"])
        self.assertEqual(29, plan["executable_douyin_accounts"])
        self.assertEqual(
            {"铁锤人", "歸藏 guizang.ai"},
            {row["account_name"] for row in plan["invalid_douyin_accounts"]},
        )


if __name__ == "__main__":
    unittest.main()

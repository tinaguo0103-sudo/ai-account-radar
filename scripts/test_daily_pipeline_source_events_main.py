from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from email.message import Message
from pathlib import Path
from unittest import mock

import content_sampler
import daily_pipeline
from source_assembly import SOURCE_IDS, SOURCE_REGISTRY
from source_control import SourceControl
from test_source_assembly import write_package


RUN_AT = "2026-10-06T12:34:56+00:00"
WECHAT_ID = "feishu_recvlm0QvTIDMe"
AIHOT_SELECTED_ID = "feishu_recvl4uBak1aBQ"
AIHOT_DAILY_ID = "feishu_recvl4uBakCutL"


class FixtureResponse:
    status = 200

    def __init__(self, body: dict[str, object]):
        self._body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.headers = Message()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


def source_account(
    record_id: str,
    display_name: str,
    platform: str,
    url: str,
    *,
    verified_identity: str = "",
) -> dict[str, object]:
    return {
        "record_id": record_id,
        "display_name": display_name,
        "platform": platform,
        "channel_id": f"qa-{record_id}",
        "homepage_url": url,
        "configured_identity": verified_identity or url,
        "verified_identity": verified_identity or url,
        "enabled": True,
        "participates_sampling": False,
        "priority": "medium",
        "fetch_method": "qa_fixture",
        "source_role": "system_hotspot_source",
    }


def manual_row(source_id: str, url: str) -> dict[str, object]:
    return {
        "来源类型": "公众号文章",
        "平台": "微信公众号",
        "账号名/公众号名": "QA fixture source",
        "内容标题": "Isolated source assembly fixture",
        "内容链接": url,
        "内容指纹": f"url:{url}",
        "发布时间": "2026-10-06T12:00:00+08:00",
        "正文/字幕/简介片段": "Synthetic text used only to exercise the normal local merge path.",
        "抓取方式": "wechat_public_html_js_content",
        "抓取状态": "success",
        "source_provenance": [{"source_id": source_id, "url": url}],
    }


class DailyPipelineSourceEventsMainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="daily-pipeline-source-events-")
        self.root = Path(self.temp.name)
        self.output = self.root / "output"
        self.source_db = self.root / "qa-private-source.sqlite3"
        self.wechat_url = "https://weixin.imaseo.com/"
        self.selected_url = "https://aihot.news/api/v1/items?mode=selected&window=7d&limit=30&by=published"
        self.daily_url = "https://aihot.news/api/v1/dailies/latest"
        self.service = SourceControl(self.source_db)
        package_accounts = []
        for source_id in sorted(SOURCE_IDS):
            metadata = SOURCE_REGISTRY[source_id]
            package_accounts.append(source_account(
                source_id.removeprefix("feishu_"), str(metadata["account"]),
                str(metadata["platform"]), f"https://{metadata['host']}/",
                verified_identity=f"verified:{source_id}",
            ))
        self.service.import_accounts(package_accounts + [
            source_account(WECHAT_ID.removeprefix("feishu_"), "数字生命卡兹克", "微信公众号", self.wechat_url),
            source_account(AIHOT_SELECTED_ID.removeprefix("feishu_"), "AIHOT精选", "AIHOT精选", self.selected_url),
            source_account(AIHOT_DAILY_ID.removeprefix("feishu_"), "AIHOT日报", "AIHOT日报", self.daily_url),
        ])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def sampler_http_fixture(self, request, **_kwargs):
        url = request.full_url
        if "/dailies/" in url:
            return FixtureResponse({"schemaVersion": 1, "report": {
                "date": "2026-10-06", "generatedAt": RUN_AT, "sections": [{
                    "label": "QA fixture", "items": [{
                        "id": "daily-fixture-1", "title": "Synthetic daily fixture",
                        "summary": "No external claim; isolated parser input.",
                        "source": {"name": "QA fixture"},
                        "links": {"original": "https://example.test/daily-fixture"},
                        "publishedAt": RUN_AT,
                    }],
                }],
            }})
        return FixtureResponse({"schemaVersion": 1, "items": [{
            "id": "selected-fixture-1", "title": "Synthetic selected fixture",
            "summary": "No external claim; isolated parser input.",
            "source": {"name": "QA fixture"},
            "links": {"original": "https://example.test/selected-fixture"},
            "publishedAt": RUN_AT,
        }], "page": {"nextCursor": None}})

    def run_main(self, *, wechat: bool, aihot: bool) -> tuple[int, str, str, str]:
        run_id = "run_20261006_123456_wechat" if wechat else "run_20261006_123456_aihot"
        package = write_package(
            self.root / f"package-{run_id}", revision=1, run_id=run_id,
        )
        argv = [
            "daily_pipeline.py", "--no-fetch-douyin", "--no-feishu-runtime",
            "--run-id", run_id, "--source-db", str(self.source_db), "--defer-editorial",
            "--source-package", str(package),
        ]
        if wechat:
            argv.append("--fetch-wechat-public-fulltext")
        if not aihot:
            argv.append("--no-fetch-aihot")

        def fixture_run_step(name: str, command: list[str], env=None):
            if command and command[0] == sys.executable and command[1].endswith("wechat_public_fulltext_source.py"):
                out_dir = Path(command[command.index("--out-dir") + 1])
                out_dir.mkdir(parents=True, exist_ok=True)
                wechat_item = manual_row(WECHAT_ID, "https://openai.com/news/release?a=1&b=2")
                (out_dir / "content_items_manual.jsonl").write_text(
                    json.dumps(wechat_item, ensure_ascii=False) + "\n", encoding="utf-8",
                )
                payload = {
                    "ok": True, "status": "completed", "rows": 1,
                    "outcomes": [{
                        "source_id": WECHAT_ID, "attempted_at": RUN_AT,
                        "status": "success", "outcome": "success",
                        "failure_class": "discarded-on-success", "artifact_count": 1,
                        "rows": 1,
                    }],
                }
                return {"name": name, "command": command, "returncode": 0,
                        "stdout": json.dumps(payload), "stderr": ""}

            if command and command[0] == sys.executable and command[1].endswith("content_sampler.py"):
                sampler_stdout = io.StringIO()
                sampler_stderr = io.StringIO()
                sampler_argv = [command[1], *command[2:]]
                with (
                    mock.patch.object(sys, "argv", sampler_argv),
                    mock.patch.object(content_sampler, "urlopen", side_effect=self.sampler_http_fixture),
                    redirect_stdout(sampler_stdout),
                    redirect_stderr(sampler_stderr),
                ):
                    code = content_sampler.main()
                return {"name": name, "command": command, "returncode": code,
                        "stdout": sampler_stdout.getvalue(), "stderr": sampler_stderr.getvalue()}

            # The normal path should stop at the requested outer editorial defer.
            # Reaching another subprocess is a fixture/test-contract failure.
            self.fail(f"unexpected external step in isolated test: {name}: {command}")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(daily_pipeline, "OUT", self.output),
            mock.patch.object(daily_pipeline, "LOG_DIR", self.output / "logs"),
            mock.patch.object(content_sampler, "OUT", self.output),
            mock.patch.object(content_sampler, "RUNS_DIR", self.output / "runs"),
            mock.patch.object(content_sampler, "DRY_RUNS_DIR", self.output / "dry_runs"),
            mock.patch.object(daily_pipeline, "load_local_env", return_value=None),
            mock.patch.object(daily_pipeline, "run_step", side_effect=fixture_run_step),
            mock.patch.object(sys, "argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = daily_pipeline.main()
        return code, stdout.getvalue(), stderr.getvalue(), run_id

    def recorded_events(self, run_id: str) -> list[dict[str, object]]:
        with sqlite3.connect(self.source_db) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                "SELECT run_id,source_id,attempted_at,outcome,failure_class,artifact_count,verified_identity,substitute_count "
                "FROM source_run_events WHERE run_id=? ORDER BY source_id", (run_id,),
            )]

    def assert_actual_merge_kept_provenance(self, run_id: str, expected_extra_source_id: str = "") -> None:
        merged_path = self.output / "runs" / run_id / "sources" / "current_run_rows.jsonl"
        rows = [json.loads(line) for line in merged_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        package_url = "https://openai.com/news/release?a=1&b=2"
        package_row = next(row for row in rows if row.get("内容链接") == package_url)
        self.assertEqual(1, sum(row.get("内容链接") == package_url for row in rows))
        package_sources = {"feishu_recvl4uBak96UM", "feishu_recvl4uBakM7LI"}
        if expected_extra_source_id:
            package_sources.add(expected_extra_source_id)
            self.assertEqual(package_sources, set(package_row["source_ids"]))
        else:
            self.assertEqual(
                package_sources,
                {source["source_id"] for source in package_row["source_provenance"]},
            )

    def test_normal_main_wechat_branch_records_real_event_merges_and_returns(self) -> None:
        code, stdout, stderr, run_id = self.run_main(wechat=True, aihot=False)
        self.assertEqual(code, 0, f"stderr={stderr}\nstdout={stdout}")
        events = self.recorded_events(run_id)
        self.assertEqual(
            {"feishu_recvl4uBak96UM", "feishu_recvl4uBakM7LI", WECHAT_ID},
            {row["source_id"] for row in events},
        )
        event = next(row for row in events if row["source_id"] == WECHAT_ID)
        self.assertEqual(WECHAT_ID, event["source_id"])
        self.assertEqual(RUN_AT, event["attempted_at"])
        self.assertEqual("success", event["outcome"])
        self.assertEqual("", event["failure_class"])
        self.assertEqual(1, event["artifact_count"])
        self.assertEqual(self.wechat_url, event["verified_identity"])
        self.assertEqual(0, event["substitute_count"])
        self.assert_actual_merge_kept_provenance(run_id, WECHAT_ID)

    def test_normal_main_aihot_branch_records_real_events_merges_and_returns(self) -> None:
        code, stdout, stderr, run_id = self.run_main(wechat=False, aihot=True)
        self.assertEqual(code, 0, f"stderr={stderr}\nstdout={stdout}")
        events = self.recorded_events(run_id)
        expected_ids = {
            "feishu_recvl4uBak96UM", "feishu_recvl4uBakM7LI",
            AIHOT_SELECTED_ID, AIHOT_DAILY_ID,
        }
        self.assertEqual(expected_ids, {row["source_id"] for row in events})
        for event in events:
            if event["source_id"] in {"feishu_recvl4uBak96UM", "feishu_recvl4uBakM7LI"}:
                continue
            self.assertEqual(run_id, event["run_id"])
            self.assertTrue(event["attempted_at"])
            self.assertEqual("success", event["outcome"])
            self.assertEqual("", event["failure_class"])
            self.assertEqual(1, event["artifact_count"])
            expected_identity = {
                AIHOT_SELECTED_ID: self.selected_url,
                AIHOT_DAILY_ID: self.daily_url,
            }[str(event["source_id"])]
            self.assertEqual(expected_identity, event["verified_identity"])
            self.assertEqual(0, event["substitute_count"])
        self.assert_actual_merge_kept_provenance(run_id)


if __name__ == "__main__":
    unittest.main()

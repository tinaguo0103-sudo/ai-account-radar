from __future__ import annotations

import copy
import csv
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import canonical_owner_projection as owners
import content_sampler
import daily_pipeline
import wewe_provider_health as health
import wewe_provider_refresh as refresh


RUN_ID = "run_20260723_080323"
HISTORICAL_RUN = "run_20260722_080000"


def make_item(index: int, *, url: str | None = None) -> content_sampler.ContentItem:
    source = "对标视频" if index < 88 else "AIHOT热点"
    return content_sampler.ContentItem(
        source_type=source,
        platform="抖音" if source == "对标视频" else "AIHOT",
        account_name=f"account-{index:03d}",
        title=f"title-{index:03d}",
        url=url if url is not None else f"https://example.test/item/{index:03d}",
        content_shape="video" if source == "对标视频" else "article",
        cover_text="",
        body_snippet=(f"business body {index} " * 30).strip(),
        published_at="2026-07-23 08:00:00",
        comment_questions="",
        ocr_text="",
        fetch_method="incident_sanitized_fixture",
        fetch_status="success",
        failure_reason="",
        fingerprint=f"raw-{index:03d}",
    )


def incident_shape() -> tuple[list[content_sampler.ContentItem], list[dict], list[str]]:
    owners_items = [make_item(index) for index in range(127)]
    aliases = [make_item(127 + index, url=owners_items[index].url) for index in range(15)]
    records: list[dict] = []
    for index, value in enumerate(owners_items):
        owner_fingerprint = f"owner-{index:03d}"
        owner_run = RUN_ID if index < 76 else HISTORICAL_RUN
        records.append({
            "record_id": f"rec-{index:03d}",
            "fields": {
                "内容指纹": owner_fingerprint,
                "链接": value.url,
                "标题": value.title,
                "来源类型": value.source_type,
                "来源名称": value.account_name,
                "作者/账号": value.account_name,
                "平台": value.platform,
                "发布时间": value.published_at,
                "运行批次": owner_run,
                "最近参与运行批次": owner_run,
            },
        })
    candidate_fingerprints = [f"raw-{index:03d}" for index in range(39)] + [f"raw-{index:03d}" for index in range(76, 87)]
    return [*owners_items, *aliases], records, candidate_fingerprints


class AR043BusinessFirstCollectionTests(unittest.TestCase):
    def writer_context(self, stored: list[dict], *, update_side_effect=None):
        def update(_token, _app, _table, record_id, fields):
            row = next(value for value in stored if value["record_id"] == record_id)
            row["fields"].update(copy.deepcopy(fields))
            if update_side_effect:
                update_side_effect(record_id, fields)

        return mock.patch.multiple(
            content_sampler,
            require_feishu_env=mock.DEFAULT,
            list_tables=mock.DEFAULT,
            configured_table_id=mock.DEFAULT,
            ensure_content_inbox_fields=mock.DEFAULT,
            all_records=mock.DEFAULT,
            update_record_fields=mock.DEFAULT,
            batch_create_records=mock.DEFAULT,
        ), {
            "require_feishu_env": "app",
            "list_tables": [],
            "configured_table_id": ("table", "env"),
            "ensure_content_inbox_fields": [],
            "all_records": lambda *_: copy.deepcopy(stored),
            "update_record_fields": update,
            "batch_create_records": lambda *_args: 0,
        }

    def run_writer(self, stored, items, candidates, **kwargs):
        patcher, values = self.writer_context(stored, **kwargs)
        with patcher as mocks, mock.patch.object(content_sampler.feishu, "tenant_token", return_value="token"), mock.patch.object(content_sampler.time, "sleep"):
            for name, value in values.items():
                mocks[name].side_effect = value if callable(value) else None
                if not callable(value):
                    mocks[name].return_value = value
            return content_sampler.write_content_ledger_to_feishu(
                items,
                RUN_ID,
                candidate_fingerprints=candidates,
            )

    def test_incident_matrix_normal_owner_plan_and_identical_rerun(self) -> None:
        items, stored, candidates = incident_shape()
        original_runs = {row["record_id"]: row["fields"]["运行批次"] for row in stored}
        original_ids = [row["record_id"] for row in stored]

        projection = owners.resolve_owner_projection(items, stored, RUN_ID, allow_new=True)
        self.assertEqual(142, projection.manifest["raw_planned_count"])
        self.assertEqual(127, projection.manifest["unique_owner_count"])
        self.assertEqual(15, projection.manifest["raw_alias_count"])
        self.assertEqual(51, projection.manifest["historical_participation_count"])
        self.assertEqual(50, len(owners.project_fingerprints(candidates, projection.manifest)))

        first = self.run_writer(stored, items, candidates)
        self.assertTrue(first["core_readback_green"])
        self.assertEqual(51, first["write_plan"]["existing_historical"])
        self.assertEqual(51, first["updated_existing"])
        self.assertEqual(0, first["created_records"])
        self.assertEqual(127, first["read_back_identity"]["matched_count"])
        self.assertEqual(15, first["write_plan"]["aliases"])
        self.assertEqual(50, first["candidate_projection"]["mapped_count"])
        self.assertEqual(0, first["candidate_projection"]["local_failure_count"])
        self.assertEqual(original_ids, [row["record_id"] for row in stored])
        self.assertEqual(original_runs, {row["record_id"]: row["fields"]["运行批次"] for row in stored})
        self.assertTrue(all(row["fields"]["最近参与运行批次"] == RUN_ID for row in stored))

        second = self.run_writer(stored, items, candidates)
        self.assertEqual(0, second["created_records"])
        self.assertEqual(0, second["updated_existing"])
        self.assertEqual(127, second["read_back_identity"]["matched_count"])
        self.assertEqual(50, second["candidate_projection"]["mapped_count"])
        self.assertEqual(original_ids, [row["record_id"] for row in stored])

    def test_after_commit_update_loss_reconciles_without_follow_up(self) -> None:
        items, stored, candidates = incident_shape()
        raised = {"done": False}

        def lose_response(_record_id, _fields):
            if not raised["done"]:
                raised["done"] = True
                raise TimeoutError("response lost after commit")

        result = self.run_writer(
            stored,
            items,
            candidates,
            update_side_effect=lose_response,
        )
        self.assertTrue(result["core_readback_green"])
        self.assertEqual(1, result["recovered_updates"])
        self.assertEqual(0, result["created_records"])
        self.assertNotIn("today_view", result)

    def test_wechat_failure_is_source_local_and_zero_candidates_only_blocks_downstream(self) -> None:
        steps = [{
            "name": "refresh WeChat",
            "returncode": 4,
            "source_local_failure": True,
            "source": "wechat",
            "source_rows": 0,
            "source_failure_reason": "provider unavailable",
        }]
        with mock.patch.object(daily_pipeline, "read_json", return_value={}):
            survivors = daily_pipeline.downstream_usability_report(steps, Path("/tmp/run"), 50)
            empty = daily_pipeline.downstream_usability_report(steps, Path("/tmp/run"), 0)
        self.assertFalse(survivors["full_collection_success"])
        self.assertTrue(survivors["downstream_usable"])
        self.assertFalse(empty["downstream_usable"])

    def test_project_mutex_probe_has_no_provider_or_secret_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            data = project / "data"
            database = data / "wewe-rss.db"
            data.mkdir()
            connection = __import__("sqlite3").connect(database)
            connection.executescript(
                "create table accounts(status integer);"
                "create table feeds(id text,status integer,sync_time integer,updated_at integer);"
                "create table articles(mp_id text,publish_time integer);"
                "insert into accounts values(1);"
                "insert into feeds values('feed',1,1,1);"
            )
            connection.commit()
            connection.close()
            lock = project / "output" / "state" / "wewe-refresh" / "refresh.lock"
            proof = refresh.check_only_plan(data, lock_path=lock, project_root=project)
            self.assertTrue(proof["lock_released"])
            self.assertEqual(0, proof["provider_request_count"])
            self.assertFalse(proof["secret_material_read"])
            self.assertFalse(lock.exists())


if __name__ == "__main__":
    unittest.main()

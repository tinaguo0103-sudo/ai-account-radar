from __future__ import annotations

import copy
import unittest
from pathlib import Path
from unittest import mock

import canonical_owner_projection as owners
import content_sampler


RUN_ID = "run_20260717_093104"


def item(index: int, source_type: str, *, fingerprint: str | None = None, url: str | None = None) -> content_sampler.ContentItem:
    platform = {"抖音视频": "抖音", "公众号文章": "微信公众号", "AI热点": "AIHOT"}[source_type]
    return content_sampler.ContentItem(
        source_type=source_type,
        platform=platform,
        account_name=f"account-{source_type}-{index}",
        title=f"title-{source_type}-{index}",
        url=url if url is not None else f"https://example.test/{source_type}/{index}",
        content_shape="text",
        cover_text="",
        body_snippet=f"body {index} " * 40,
        published_at="2026-07-17 08:00:00",
        comment_questions="",
        ocr_text="",
        fetch_method="fixture",
        fetch_status="success",
        failure_reason="",
        fingerprint=fingerprint or f"planned-{index:03d}",
    )


def record(value: content_sampler.ContentItem, *, fingerprint: str | None = None, record_id: str | None = None) -> dict:
    return {
        "record_id": record_id or f"rec-{fingerprint or value.fingerprint}",
        "fields": {
            "内容指纹": fingerprint or value.fingerprint,
            "链接": value.url,
            "标题": value.title,
            "来源类型": value.source_type,
            "来源名称": value.account_name,
            "作者/账号": value.account_name,
            "平台": value.platform,
            "发布时间": value.published_at,
            "运行批次": RUN_ID,
            "最近参与运行批次": RUN_ID,
        },
    }


def production_shape() -> tuple[list[content_sampler.ContentItem], list[dict]]:
    direct: list[content_sampler.ContentItem] = []
    index = 0
    for source_type, count in (("AI热点", 35), ("公众号文章", 17), ("抖音视频", 84)):
        for _ in range(count):
            direct.append(item(index, source_type))
            index += 1
    records = [record(value) for value in direct]
    aliases: list[content_sampler.ContentItem] = []
    # Twenty-two aliases share owners already present in the raw plan.
    for owner_index in range(21):
        alias = item(index, "AI热点", url=direct[owner_index].url)
        alias.title = f"drifted-title-{index}"
        aliases.append(alias)
        index += 1
    alias = item(index, "公众号文章", url=direct[35].url)
    alias.title = f"drifted-title-{index}"
    aliases.append(alias)
    index += 1
    # Four aliases point to same-run canonical owners outside the raw fingerprint set.
    for source_type in ("公众号文章", "抖音视频", "抖音视频", "抖音视频"):
        alias = item(index, source_type)
        aliases.append(alias)
        records.append(record(alias, fingerprint=f"owner-extra-{len(aliases) - 23}"))
        index += 1
    return [*direct, *aliases], records


class CanonicalOwnerProjectionTests(unittest.TestCase):
    def test_new_same_url_group_selects_one_owner_for_two_or_three_rows(self) -> None:
        first = item(1, "抖音视频", fingerprint="a", url="https://example.test/same")
        second = item(2, "抖音视频", fingerprint="b", url=first.url)
        third = item(3, "抖音视频", fingerprint="c", url=first.url)
        for planned in ([first, second], [first, second, third]):
            with self.subTest(count=len(planned)):
                projection = owners.resolve_owner_projection(planned, [], RUN_ID, allow_new=True)
                self.assertEqual(1, projection.manifest["unique_owner_count"])
                self.assertEqual(1, projection.manifest["new_owner_count"])
                self.assertEqual(len(planned) - 1, projection.manifest["new_owner_alias_count"])
                self.assertEqual(["a"], projection.manifest["ordered_owner_fingerprints"])

    def test_new_url_empty_complete_composite_deduplicates_but_incomplete_blocks(self) -> None:
        first = item(1, "公众号文章", fingerprint="a", url="")
        second = copy.deepcopy(first)
        second.fingerprint = "b"
        projection = owners.resolve_owner_projection([first, second], [], RUN_ID, allow_new=True)
        self.assertEqual(1, projection.manifest["unique_owner_count"])
        self.assertEqual(1, projection.manifest["new_owner_alias_count"])
        incomplete = copy.deepcopy(first)
        incomplete.title = ""
        with self.assertRaisesRegex(owners.OwnerProjectionError, "url_empty_owner_composite_incomplete"):
            owners.resolve_owner_projection([incomplete], [], RUN_ID, allow_new=True)

    def test_same_title_with_different_urls_remains_distinct(self) -> None:
        first = item(1, "抖音视频", fingerprint="a")
        second = item(2, "抖音视频", fingerprint="b")
        second.title = first.title
        projection = owners.resolve_owner_projection([first, second], [], RUN_ID, allow_new=True)
        self.assertEqual(2, projection.manifest["unique_owner_count"])
        self.assertEqual(2, projection.manifest["new_owner_count"])

    def test_existing_owner_maps_multiple_planned_rows_without_create(self) -> None:
        direct = item(1, "抖音视频", fingerprint="owner", url="https://example.test/same")
        alias_a = item(2, "抖音视频", fingerprint="a", url=direct.url)
        alias_b = item(3, "抖音视频", fingerprint="b", url=direct.url)
        projection = owners.resolve_owner_projection([alias_a, direct, alias_b], [record(direct)], RUN_ID, allow_new=True)
        self.assertEqual(1, projection.manifest["unique_owner_count"])
        self.assertEqual(0, projection.manifest["new_owner_count"])
        self.assertEqual("owner", projection.projected_items[0].fingerprint)
        self.assertEqual("direct", projection.manifest["owners"][0]["representative_kind"])

    def test_second_run_reuses_new_owner_deterministically(self) -> None:
        first = item(1, "抖音视频", fingerprint="a", url="https://example.test/same")
        second = item(2, "抖音视频", fingerprint="b", url=first.url)
        initial = owners.resolve_owner_projection([first, second], [], RUN_ID, allow_new=True)
        subsequent = owners.resolve_owner_projection([first, second], [record(first)], RUN_ID, allow_new=True)
        self.assertEqual(initial.manifest["ordered_owner_fingerprints"], subsequent.manifest["ordered_owner_fingerprints"])
        self.assertEqual(0, subsequent.manifest["new_owner_count"])

    def test_writer_queues_one_create_for_one_new_owner_key(self) -> None:
        first = item(1, "抖音视频", fingerprint="a", url="https://example.test/same")
        second = item(2, "抖音视频", fingerprint="b", url=first.url)
        stored: list[dict] = []
        create_payloads: list[list[dict]] = []

        def create(_token: str, _app: str, _table: str, rows: list[dict]) -> int:
            create_payloads.append(copy.deepcopy(rows))
            for index, fields in enumerate(rows):
                stored.append({"record_id": f"created-{index}", "fields": dict(fields)})
            return len(rows)

        with mock.patch.object(content_sampler, "require_feishu_env", return_value="app"), \
             mock.patch.object(content_sampler.feishu, "tenant_token", return_value="token"), \
             mock.patch.object(content_sampler, "list_tables", return_value=[]), \
             mock.patch.object(content_sampler, "resolve_table_id", return_value="table"), \
             mock.patch.object(content_sampler, "ensure_content_inbox_fields", return_value=[]), \
             mock.patch.object(content_sampler, "all_records", side_effect=lambda *_: copy.deepcopy(stored)), \
             mock.patch.object(content_sampler, "update_record_fields") as update, \
             mock.patch.object(content_sampler, "batch_create_records", side_effect=create), \
             mock.patch.object(content_sampler.time, "sleep"):
            result = content_sampler.write_content_ledger_to_feishu([first, second], RUN_ID)
        self.assertEqual(1, result["created_records"])
        self.assertEqual(1, len(create_payloads))
        self.assertEqual(1, len(create_payloads[0]))
        self.assertEqual("a", create_payloads[0][0]["内容指纹"])
        update.assert_not_called()

    def test_production_shape_projects_162_to_140(self) -> None:
        items, records = production_shape()
        projection = owners.resolve_owner_projection(items, records, RUN_ID)
        manifest = projection.manifest
        self.assertEqual(162, manifest["raw_planned_count"])
        self.assertEqual(140, manifest["unique_owner_count"])
        self.assertEqual(136, manifest["direct_count"])
        self.assertEqual(26, manifest["alias_count"])
        self.assertEqual(22, manifest["shared_alias_count"])
        self.assertEqual(4, manifest["additional_owner_count"])
        self.assertEqual({"AI热点": 35, "公众号文章": 18, "抖音视频": 87}, manifest["per_source_owner_counts"])
        self.assertEqual(22, len(manifest["dropped_duplicate_order"]))
        self.assertEqual(140, len({value.fingerprint for value in projection.projected_items}))
        self.assertTrue(all(row["record_id"] for row in manifest["owners"]))

    def test_direct_planned_owner_wins_and_additional_owner_keeps_alias_provenance(self) -> None:
        items, records = production_shape()
        projection = owners.resolve_owner_projection(items, records, RUN_ID)
        direct_owner = projection.manifest["owners"][0]
        self.assertEqual("direct", direct_owner["representative_kind"])
        self.assertEqual(items[0].fingerprint, direct_owner["representative_planned_fingerprint"])
        extras = [row for row in projection.manifest["owners"] if row["representative_kind"] == "alias_source"]
        self.assertEqual(4, len(extras))
        self.assertTrue(all(row["representative_planned_fingerprint"] in row["alias_fingerprints"] for row in extras))

    def test_url_metadata_drift_maps_but_same_title_different_url_does_not(self) -> None:
        planned = item(1, "抖音视频")
        owner = record(planned, fingerprint="owner-fp")
        owner["fields"].update({"标题": "changed", "发布时间": "", "来源名称": "changed", "作者/账号": "changed"})
        projection = owners.resolve_owner_projection([planned], [owner], RUN_ID)
        self.assertEqual("owner-fp", projection.projected_items[0].fingerprint)
        self.assertIn("title", projection.manifest["mappings"][0]["metadata_drift"])
        unrelated = copy.deepcopy(owner)
        unrelated["fields"]["链接"] = "https://example.test/other"
        with self.assertRaisesRegex(owners.OwnerProjectionError, "canonical_owner_missing"):
            owners.resolve_owner_projection([planned], [unrelated], RUN_ID)

    def test_hard_stop_owner_identity_mutations(self) -> None:
        planned = item(1, "抖音视频")
        base = record(planned, fingerprint="owner-fp")
        mutations = []
        duplicate_url = copy.deepcopy(base)
        duplicate_url["record_id"] = "rec-other"
        duplicate_url["fields"]["内容指纹"] = "owner-other"
        mutations.append([base, duplicate_url])
        missing_id = copy.deepcopy(base)
        missing_id["record_id"] = ""
        mutations.append([missing_id])
        duplicate_fp = copy.deepcopy(base)
        duplicate_fp["record_id"] = "rec-duplicate"
        duplicate_fp["fields"]["链接"] = "https://example.test/other"
        mutations.append([base, duplicate_fp])
        for records in mutations:
            with self.subTest(records=records):
                with self.assertRaises(owners.OwnerProjectionError):
                    owners.resolve_owner_projection([planned], records, RUN_ID)

    def test_exact_historical_owner_is_reused_for_current_participation(self) -> None:
        planned = item(1, "抖音视频")
        historical = record(planned, fingerprint="historical-owner")
        historical["fields"]["运行批次"] = "run_20260717_000001"
        historical["fields"]["最近参与运行批次"] = "run_20260717_000001"
        projection = owners.resolve_owner_projection([planned], [historical], RUN_ID)
        self.assertEqual(["historical-owner"], [row.fingerprint for row in projection.projected_items])
        self.assertEqual(0, projection.manifest["skipped_historical_count"])
        self.assertEqual(1, projection.manifest["historical_participation_count"])
        self.assertEqual(1, projection.manifest["skipped_historical_group_count"])
        self.assertEqual("existing_historical", projection.manifest["mappings"][0]["resolution"])
        self.assertEqual("rec-historical-owner", projection.manifest["mappings"][0]["record_id"])

    def test_url_empty_requires_unique_full_composite(self) -> None:
        planned = item(1, "公众号文章", url="")
        matching = record(planned, fingerprint="owner-fp")
        projection = owners.resolve_owner_projection([planned], [matching], RUN_ID)
        self.assertEqual("owner-fp", projection.projected_items[0].fingerprint)
        title_only = copy.deepcopy(matching)
        title_only["fields"]["来源名称"] = "other"
        title_only["fields"]["作者/账号"] = "other"
        with self.assertRaisesRegex(owners.OwnerProjectionError, "canonical_owner_missing"):
            owners.resolve_owner_projection([planned], [title_only], RUN_ID)
        collision = copy.deepcopy(matching)
        collision["record_id"] = "rec-collision"
        collision["fields"]["内容指纹"] = "owner-2"
        with self.assertRaisesRegex(owners.OwnerProjectionError, "canonical_owner_ambiguous"):
            owners.resolve_owner_projection([planned], [matching, collision], RUN_ID)

    def test_manifest_and_projection_are_deterministic(self) -> None:
        items, records = production_shape()
        first = owners.resolve_owner_projection(items, records, RUN_ID)
        second = owners.resolve_owner_projection(items, records, RUN_ID)
        self.assertEqual(first.manifest, second.manifest)
        self.assertEqual(
            [value.fingerprint for value in first.projected_items],
            [value.fingerprint for value in second.projected_items],
        )

    def test_owner_readback_requires_exact_unique_same_run_records(self) -> None:
        items, records = production_shape()
        projection = owners.resolve_owner_projection(items, records, RUN_ID)
        self.assertEqual(140, owners.verify_owner_readback(projection.manifest, records, RUN_ID)["owner_count"])
        with self.assertRaises(owners.OwnerProjectionError):
            owners.verify_owner_readback(projection.manifest, records[:-1], RUN_ID)
        duplicate = copy.deepcopy(records[0])
        duplicate["record_id"] = "rec-duplicate"
        with self.assertRaisesRegex(owners.OwnerProjectionError, "duplicate_owner_fingerprint"):
            owners.verify_owner_readback(projection.manifest, [*records, duplicate], RUN_ID)

    def test_candidate_projection_uses_only_unique_owners_and_direct_wins(self) -> None:
        items, records = production_shape()
        projection = owners.resolve_owner_projection(items, records, RUN_ID)
        alias = projection.manifest["mappings"][136]
        direct = next(row for row in projection.manifest["mappings"] if row["planned_fingerprint"] == alias["owner_fingerprint"])
        rows = [
            {"内容指纹": alias["planned_fingerprint"], "选题命题": "alias"},
            {"内容指纹": direct["planned_fingerprint"], "选题命题": "direct"},
        ]
        projected = owners.project_candidate_rows(rows, projection.manifest)
        self.assertEqual(1, len(projected))
        self.assertEqual("direct", projected[0]["选题命题"])
        self.assertEqual(alias["owner_fingerprint"], projected[0]["内容指纹"])

    def test_recomputed_candidates_never_escape_owner_set(self) -> None:
        items, records = production_shape()
        projection = owners.resolve_owner_projection(items, records, RUN_ID)
        owner_set = set(projection.manifest["ordered_owner_fingerprints"])
        with mock.patch.object(content_sampler, "breakdown", side_effect=lambda value: {"内容指纹": value.fingerprint, "是否进入候选初筛": "是"}), \
             mock.patch.object(content_sampler, "topic_from_breakdown", side_effect=lambda row, value: {"内容指纹": value.fingerprint}), \
             mock.patch.object(content_sampler, "apply_editorial_judgement", side_effect=lambda rows, _: rows), \
             mock.patch.object(content_sampler, "select_skill_review_candidates", side_effect=lambda rows: rows[:11]), \
             mock.patch.object(content_sampler, "assign_action_quotas", side_effect=lambda rows: rows), \
             mock.patch.object(content_sampler, "assign_today_priority", side_effect=lambda rows: rows):
            candidates = owners.recompute_candidate_universe(projection.projected_items)
        fingerprints = [row["内容指纹"] for row in candidates]
        self.assertEqual(len(fingerprints), len(set(fingerprints)))
        self.assertTrue(set(fingerprints).issubset(owner_set))

    def test_future_writer_projects_before_any_create_or_update(self) -> None:
        items, records = production_shape()
        updates: list[tuple[str, dict]] = []
        posts: list[dict] = []
        with mock.patch.object(content_sampler, "require_feishu_env", return_value="app"), \
             mock.patch.object(content_sampler.feishu, "tenant_token", return_value="token"), \
             mock.patch.object(content_sampler, "list_tables", return_value=[]), \
             mock.patch.object(content_sampler, "resolve_table_id", return_value="table"), \
             mock.patch.object(content_sampler, "ensure_content_inbox_fields", return_value=[]), \
             mock.patch.object(content_sampler, "all_records", side_effect=[records, records]), \
             mock.patch.object(content_sampler, "update_record_fields", side_effect=lambda _t, _a, _b, rid, fields: updates.append((rid, fields))), \
             mock.patch.object(content_sampler, "batch_create_records", side_effect=lambda _t, _a, _b, rows: posts.extend(rows) or len(rows)), \
             mock.patch.object(content_sampler.time, "sleep"):
            result = content_sampler.write_content_ledger_to_feishu(items, RUN_ID)
        self.assertEqual(140, result["owner_projection"]["unique_owner_count"])
        self.assertEqual(136, len(updates))
        self.assertEqual([], posts)
        self.assertFalse({"rec-owner-extra-0", "rec-owner-extra-1", "rec-owner-extra-2", "rec-owner-extra-3"} & {record_id for record_id, _ in updates})
        self.assertFalse(any(row["planned_fingerprint"] in {field.get("内容指纹") for _, field in updates} for row in result["owner_projection"]["mappings"] if row["resolution"] == "alias"))

    def test_current_recovery_surface_has_zero_writer_calls(self) -> None:
        source = Path(owners.__file__).read_text(encoding="utf-8")
        self.assertNotIn("write_content_ledger_to_feishu", source)
        self.assertIn('"writes_feishu": False', source)
        self.assertIn('"calls_full_writer": False', source)


if __name__ == "__main__":
    unittest.main()

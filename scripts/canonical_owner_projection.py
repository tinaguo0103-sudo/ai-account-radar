#!/usr/bin/env python3
from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from typing import Any, Iterable

import content_sampler


class OwnerProjectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class OwnerProjection:
    projected_items: list[content_sampler.ContentItem]
    manifest: dict[str, Any]


def normalized(value: Any) -> str:
    return " ".join(str(value or "").split())


def record_id(record: dict[str, Any]) -> str:
    return str(record.get("record_id") or record.get("id") or "")


def run_matches(fields: dict[str, Any], run_id: str) -> bool:
    return run_id in {
        str(fields.get("运行批次") or ""),
        str(fields.get("最近参与运行批次") or ""),
    }


def account_values(fields: dict[str, Any]) -> list[str]:
    return [
        normalized(fields.get(name))
        for name in ("作者/账号", "来源名称")
        if normalized(fields.get(name))
    ]


def composite_matches(item: content_sampler.ContentItem, fields: dict[str, Any]) -> bool:
    expected_account = normalized(item.account_name or item.platform)
    expected = {
        "标题": normalized(item.title or item.url),
        "来源类型": normalized(item.source_type),
        "平台": normalized(item.platform),
    }
    if any(not value or normalized(fields.get(name)) != value for name, value in expected.items()):
        return False
    accounts = account_values(fields)
    return bool(expected_account and accounts) and all(value == expected_account for value in accounts)


def owner_key(item: content_sampler.ContentItem) -> tuple[str, ...]:
    url = normalized(item.url)
    if url:
        return ("url", url)
    composite = (
        normalized(item.title),
        normalized(item.source_type),
        normalized(item.account_name or item.platform),
        normalized(item.platform),
    )
    if any(not value for value in composite):
        raise OwnerProjectionError("url_empty_owner_composite_incomplete")
    return ("composite", *composite)


def metadata_drift(item: content_sampler.ContentItem, fields: dict[str, Any]) -> list[str]:
    comparisons = {
        "title": (item.title or item.url, fields.get("标题")),
        "source_type": (item.source_type, fields.get("来源类型")),
        "platform": (item.platform, fields.get("平台")),
        "published_at": (item.published_at, fields.get("发布时间")),
    }
    expected_account = normalized(item.account_name or item.platform)
    accounts = account_values(fields)
    labels = [
        name for name, (planned, current) in comparisons.items()
        if normalized(planned) and normalized(current) != normalized(planned)
    ]
    if expected_account and (not accounts or any(value != expected_account for value in accounts)):
        labels.append("account")
    return labels


def validate_records(records: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    if not isinstance(records, list):
        raise OwnerProjectionError("owner_readback_schema_invalid")
    by_fingerprint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_url: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("fields"), dict):
            raise OwnerProjectionError("owner_readback_schema_invalid")
        fields = record["fields"]
        fingerprint = fields.get("内容指纹")
        if fingerprint not in (None, "") and not isinstance(fingerprint, str):
            raise OwnerProjectionError("owner_fingerprint_invalid")
        if isinstance(fingerprint, str) and fingerprint:
            by_fingerprint[fingerprint].append(record)
        url = normalized(fields.get("链接"))
        if url:
            by_url[url].append(record)
    duplicates = [fingerprint for fingerprint, rows in by_fingerprint.items() if len(rows) > 1]
    if duplicates:
        raise OwnerProjectionError("duplicate_owner_fingerprint")
    return by_fingerprint, by_url


def owner_candidates(
    item: content_sampler.ContentItem,
    records: list[dict[str, Any]],
    by_url: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    url = normalized(item.url)
    if url:
        return list(by_url.get(url, []))
    return [record for record in records if composite_matches(item, record["fields"])]


def resolve_owner_projection(
    items: list[content_sampler.ContentItem],
    records: list[dict[str, Any]],
    run_id: str,
    *,
    allow_new: bool = False,
) -> OwnerProjection:
    if not run_id or not items:
        raise OwnerProjectionError("owner_plan_missing")
    planned_fingerprints = [item.fingerprint for item in items]
    if any(not value for value in planned_fingerprints) or len(planned_fingerprints) != len(set(planned_fingerprints)):
        raise OwnerProjectionError("owner_plan_fingerprint_invalid")
    _, by_url = validate_records(records)
    mappings: list[dict[str, Any]] = []
    skipped_historical_group_count = 0
    grouped: dict[tuple[str, ...], list[tuple[int, content_sampler.ContentItem]]] = defaultdict(list)
    for order, item in enumerate(items):
        grouped[owner_key(item)].append((order, item))

    for group in grouped.values():
        representative = group[0][1]
        candidates = owner_candidates(representative, records, by_url)
        current_candidates = [
            record for record in candidates
            if run_matches(record["fields"], run_id)
        ]
        historical_candidates = [
            record for record in candidates
            if not run_matches(record["fields"], run_id)
        ]
        if len(current_candidates) > 1:
            raise OwnerProjectionError("canonical_owner_ambiguous")
        if not current_candidates and historical_candidates:
            if len(historical_candidates) > 1:
                raise OwnerProjectionError("historical_owner_ambiguous")
            skipped_historical_group_count += 1
            for owner in historical_candidates:
                fields = owner["fields"]
                if not str(fields.get("内容指纹") or ""):
                    raise OwnerProjectionError("canonical_owner_fingerprint_missing")
                if not record_id(owner):
                    raise OwnerProjectionError("canonical_owner_record_id_missing")
            owner = historical_candidates[0]
            owner_fingerprint = str(owner["fields"].get("内容指纹") or "")
            owner_record_id = record_id(owner)
            for order, item in group:
                mappings.append({
                    "order": order,
                    "planned_fingerprint": item.fingerprint,
                    "owner_fingerprint": owner_fingerprint,
                    "record_id": owner_record_id,
                    "resolution": "existing_historical" if order == group[0][0] else "existing_historical_alias",
                    "metadata_drift": metadata_drift(item, owner["fields"]),
                })
            continue
        if not current_candidates:
            if not allow_new:
                raise OwnerProjectionError("canonical_owner_missing")
            owner_fingerprint = representative.fingerprint
            for position, (order, item) in enumerate(group):
                mappings.append({
                    "order": order,
                    "planned_fingerprint": item.fingerprint,
                    "owner_fingerprint": owner_fingerprint,
                    "record_id": "",
                    "resolution": "new" if position == 0 else "new_alias",
                    "metadata_drift": [],
                })
            continue
        owner = current_candidates[0]
        fields = owner["fields"]
        owner_fingerprint = str(fields.get("内容指纹") or "")
        owner_record_id = record_id(owner)
        if not owner_fingerprint:
            raise OwnerProjectionError("canonical_owner_fingerprint_missing")
        if not owner_record_id:
            raise OwnerProjectionError("canonical_owner_record_id_missing")
        for order, item in group:
            if not normalized(item.url) and not composite_matches(item, fields):
                raise OwnerProjectionError("url_empty_composite_owner_mismatch")
            mappings.append({
                "order": order,
                "planned_fingerprint": item.fingerprint,
                "owner_fingerprint": owner_fingerprint,
                "record_id": owner_record_id,
                "resolution": "direct" if item.fingerprint == owner_fingerprint else "alias",
                "metadata_drift": metadata_drift(item, fields),
            })

    mappings.sort(key=lambda row: row["order"])

    direct_items = {item.fingerprint: item for item in items}
    mapping_by_planned = {row["planned_fingerprint"]: row for row in mappings}
    first_owner_order: dict[str, int] = {}
    aliases_by_owner: dict[str, list[str]] = defaultdict(list)
    for row in mappings:
        if row["resolution"] == "local_failure":
            continue
        first_owner_order.setdefault(row["owner_fingerprint"], row["order"])
        if row["resolution"] in {"alias", "new_alias", "existing_historical_alias"}:
            aliases_by_owner[row["owner_fingerprint"]].append(row["planned_fingerprint"])

    projected_items: list[content_sampler.ContentItem] = []
    owner_rows: list[dict[str, Any]] = []
    dropped_alias_order: list[str] = []
    ordered_owners = sorted(first_owner_order, key=lambda value: (first_owner_order[value], value))
    item_by_planned = {item.fingerprint: item for item in items}
    for owner_fingerprint in ordered_owners:
        if owner_fingerprint in direct_items:
            representative = direct_items[owner_fingerprint]
            representative_kind = "direct"
        else:
            owner_mapping = next(row for row in mappings if row["owner_fingerprint"] == owner_fingerprint)
            representative = item_by_planned[owner_mapping["planned_fingerprint"]]
            representative_kind = "alias_source"
        projected_items.append(replace(representative, fingerprint=owner_fingerprint))
        owner_mapping = next(row for row in mappings if row["owner_fingerprint"] == owner_fingerprint)
        owner_rows.append({
            "owner_fingerprint": owner_fingerprint,
            "record_id": owner_mapping["record_id"],
            "action": (
                "existing_historical" if owner_mapping["resolution"].startswith("existing_historical")
                else ("new_create" if owner_mapping["resolution"] in {"new", "new_alias"} else "existing_current")
            ),
            "representative_planned_fingerprint": representative.fingerprint,
            "representative_kind": representative_kind,
            "source_type": representative.source_type,
            "alias_fingerprints": aliases_by_owner.get(owner_fingerprint, []),
        })
        dropped_alias_order.extend(
            value for value in aliases_by_owner.get(owner_fingerprint, [])
            if value != representative.fingerprint
        )

    direct_count = sum(row["resolution"] == "direct" for row in mappings)
    alias_count = sum(row["resolution"] in {"alias", "new_alias", "existing_historical_alias"} for row in mappings)
    additional_owner_count = sum(
        1 for owner in ordered_owners if owner not in direct_items and owner in aliases_by_owner
    )
    shared_alias_count = sum(
        1 for row in mappings if row["resolution"] == "alias" and row["owner_fingerprint"] in direct_items
    )
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "raw_planned_count": len(items),
        "raw_alias_count": len(items) - len(grouped),
        "unique_owner_count": len(projected_items),
        "direct_count": direct_count,
        "alias_count": alias_count,
        "existing_alias_count": sum(row["resolution"] == "alias" for row in mappings),
        "new_owner_alias_count": sum(row["resolution"] == "new_alias" for row in mappings),
        "shared_alias_count": shared_alias_count,
        "additional_owner_count": additional_owner_count,
        "new_owner_count": sum(row["resolution"] == "new" for row in mappings),
        "safe_count": len(projected_items),
        "created_count": sum(row["resolution"] == "new" for row in mappings),
        "historical_participation_count": sum(
            row["resolution"] == "existing_historical" for row in mappings
        ),
        "skipped_historical_count": 0,
        "skipped_historical_group_count": skipped_historical_group_count,
        "blocked_count": sum(row["resolution"] == "local_failure" for row in mappings),
        "blocked_reasons": sorted({
            str(row.get("failure_reason") or "owner_local_failure")
            for row in mappings if row["resolution"] == "local_failure"
        }),
        "per_source_owner_counts": dict(Counter(item.source_type for item in projected_items)),
        "ordered_owner_fingerprints": [item.fingerprint for item in projected_items],
        "mappings": mappings,
        "owners": owner_rows,
        "dropped_duplicate_order": dropped_alias_order,
        "writes_feishu": False,
    }
    return OwnerProjection(projected_items=projected_items, manifest=manifest)


def verify_owner_readback(manifest: dict[str, Any], records: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    owners = manifest.get("ordered_owner_fingerprints")
    if not isinstance(owners, list) or not owners or any(not isinstance(value, str) or not value for value in owners):
        raise OwnerProjectionError("owner_manifest_invalid")
    if len(owners) != len(set(owners)) or str(manifest.get("run_id") or "") != run_id:
        raise OwnerProjectionError("owner_manifest_invalid")
    by_fingerprint, _ = validate_records(records)
    missing: list[str] = []
    wrong_run: list[str] = []
    missing_record_id: list[str] = []
    for fingerprint in owners:
        rows = by_fingerprint.get(fingerprint, [])
        if len(rows) != 1:
            missing.append(fingerprint)
            continue
        if not run_matches(rows[0]["fields"], run_id):
            wrong_run.append(fingerprint)
        if not record_id(rows[0]):
            missing_record_id.append(fingerprint)
    if missing or wrong_run or missing_record_id:
        raise OwnerProjectionError("canonical_owner_readback_failed")
    return {
        "ok": True,
        "run_id": run_id,
        "owner_count": len(owners),
        "ordered_owner_fingerprints": owners,
        "missing_count": 0,
        "duplicate_count": 0,
        "wrong_run_count": 0,
    }


def project_fingerprints(values: Iterable[str], manifest: dict[str, Any]) -> list[str]:
    mapping = {row["planned_fingerprint"]: row["owner_fingerprint"] for row in manifest["mappings"]}
    projected: list[str] = []
    seen: set[str] = set()
    for value in values:
        owner = mapping.get(value)
        if not owner:
            continue
        if owner not in seen:
            projected.append(owner)
            seen.add(owner)
    return projected


def project_candidate_rows(rows: list[dict[str, Any]], manifest: dict[str, Any]) -> list[dict[str, Any]]:
    mapping = {row["planned_fingerprint"]: row for row in manifest["mappings"]}
    grouped: dict[str, list[tuple[int, dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for order, row in enumerate(rows):
        planned = str(row.get("内容指纹") or "")
        owner_row = mapping.get(planned)
        if not owner_row or not owner_row.get("owner_fingerprint"):
            continue
        grouped[owner_row["owner_fingerprint"]].append((order, row, owner_row))
    projected: list[tuple[int, dict[str, Any]]] = []
    for owner, candidates in grouped.items():
        direct = [entry for entry in candidates if entry[2]["resolution"] == "direct"]
        chosen = min(direct or candidates, key=lambda entry: entry[0])
        output = dict(chosen[1])
        original = str(output.get("内容指纹") or "")
        output["内容指纹"] = owner
        output["来源内容指纹"] = original
        projected.append((min(entry[0] for entry in candidates), output))
    return [row for _, row in sorted(projected, key=lambda entry: entry[0])]


def recompute_candidate_universe(items: list[content_sampler.ContentItem]) -> list[dict[str, Any]]:
    item_by_fp = {item.fingerprint: item for item in items}
    breakdown_rows = [content_sampler.breakdown(item) for item in items]
    candidates = [
        content_sampler.topic_from_breakdown(row, item_by_fp[row["内容指纹"]])
        for row in breakdown_rows
        if row["是否进入候选初筛"] == "是"
    ]
    candidates = content_sampler.apply_editorial_judgement(candidates, item_by_fp)
    selected = content_sampler.select_skill_review_candidates(candidates)
    selected = content_sampler.assign_action_quotas(selected)
    selected = content_sampler.apply_editorial_judgement(selected, item_by_fp)
    return content_sampler.assign_today_priority(selected)

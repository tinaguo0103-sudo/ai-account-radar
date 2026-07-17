#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

import content_sampler
import push_to_feishu as feishu


ROOT = Path(__file__).resolve().parents[1]


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
    grouped: dict[tuple[str, ...], list[tuple[int, content_sampler.ContentItem]]] = defaultdict(list)
    for order, item in enumerate(items):
        grouped[owner_key(item)].append((order, item))

    for group in grouped.values():
        representative = group[0][1]
        candidates = owner_candidates(representative, records, by_url)
        if not candidates:
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
        if len(candidates) != 1:
            raise OwnerProjectionError("canonical_owner_ambiguous")
        owner = candidates[0]
        fields = owner["fields"]
        owner_fingerprint = str(fields.get("内容指纹") or "")
        owner_record_id = record_id(owner)
        if not owner_fingerprint:
            raise OwnerProjectionError("canonical_owner_fingerprint_missing")
        if not owner_record_id:
            raise OwnerProjectionError("canonical_owner_record_id_missing")
        if not run_matches(fields, run_id):
            raise OwnerProjectionError("canonical_owner_wrong_run")
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
        first_owner_order.setdefault(row["owner_fingerprint"], row["order"])
        if row["resolution"] in {"alias", "new_alias"}:
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
            alias_fingerprint = aliases_by_owner[owner_fingerprint][0]
            representative = item_by_planned[alias_fingerprint]
            representative_kind = "alias_source"
        projected_items.append(replace(representative, fingerprint=owner_fingerprint))
        owner_mapping = next(row for row in mappings if row["owner_fingerprint"] == owner_fingerprint)
        owner_rows.append({
            "owner_fingerprint": owner_fingerprint,
            "record_id": owner_mapping["record_id"],
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
    alias_count = sum(row["resolution"] in {"alias", "new_alias"} for row in mappings)
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
        "unique_owner_count": len(projected_items),
        "direct_count": direct_count,
        "alias_count": alias_count,
        "existing_alias_count": sum(row["resolution"] == "alias" for row in mappings),
        "new_owner_alias_count": sum(row["resolution"] == "new_alias" for row in mappings),
        "shared_alias_count": shared_alias_count,
        "additional_owner_count": additional_owner_count,
        "new_owner_count": sum(row["resolution"] == "new" for row in mappings),
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
            raise OwnerProjectionError("candidate_fingerprint_not_in_owner_manifest")
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
        if not owner_row:
            raise OwnerProjectionError("candidate_fingerprint_not_in_owner_manifest")
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


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-content-items-sha256", required=True)
    parser.add_argument("--check-only", action="store_true", required=True)
    args = parser.parse_args()
    run_dir = ROOT / "output" / "runs" / args.run_id
    content_path = run_dir / "content_items.csv"
    try:
        if not content_path.is_file() or file_sha256(content_path) != args.expected_content_items_sha256:
            raise OwnerProjectionError("owner_plan_artifact_mismatch")
        items = content_sampler.load_content_items_from_csv(content_path)
        app_token = content_sampler.require_feishu_env()
        token = feishu.tenant_token()
        table_id = content_sampler.resolve_table_id(content_sampler.list_tables(token, app_token), "content_inbox")
        if not table_id:
            raise OwnerProjectionError("content_inbox_table_missing")
        records = content_sampler.all_records(token, app_token, table_id)
        projection = resolve_owner_projection(items, records, args.run_id)
        readback = verify_owner_readback(projection.manifest, records, args.run_id)
        candidates = recompute_candidate_universe(projection.projected_items)
        candidate_fingerprints = [str(row.get("内容指纹") or "") for row in candidates]
        if any(value not in set(projection.manifest["ordered_owner_fingerprints"]) for value in candidate_fingerprints):
            raise OwnerProjectionError("candidate_owner_projection_failed")
        output = {
            "ok": True,
            "run_id": args.run_id,
            "check_only": True,
            "owner_manifest": projection.manifest,
            "owner_readback": readback,
            "candidate_count": len(candidates),
            "candidate_fingerprints": candidate_fingerprints,
            "writes_feishu": False,
            "calls_full_writer": False,
        }
        output_path = ROOT / "output" / "recovery" / args.run_id / "canonical_owner_manifest.json"
        atomic_json(output_path, output)
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - public recovery CLI is typed and fail-closed.
        print(json.dumps({"ok": False, "run_id": args.run_id, "check_only": True,
                          "reason": str(exc), "writes_feishu": False,
                          "calls_full_writer": False}, ensure_ascii=False, sort_keys=True))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

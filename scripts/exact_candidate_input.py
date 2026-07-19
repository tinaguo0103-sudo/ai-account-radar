#!/usr/bin/env python3
"""Fail-closed contract for an exact same-day editorial candidate CSV."""
from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any


RUN_ID_RE = re.compile(r"^run_(\d{8})_(\d{6})$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_COLUMNS = ("内容指纹", "来源类型", "来源内容")
IDENTITY_FIELDS = (
    "来源链接", "内容指纹", "来源类型", "来源内容", "原始来源标题",
    "原始发布文案", "原始来源账号", "平台",
)


class ExactInputError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def hash_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_run_id(run_id: str) -> str:
    match = RUN_ID_RE.fullmatch(str(run_id or "").strip())
    if not match:
        raise ExactInputError("invalid_exact_input_run_id")
    value = match.group(1)
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def expected_input_path(project_root: Path, run_id: str) -> Path:
    parse_run_id(run_id)
    return (project_root / "output" / "runs" / run_id / "today_10_topics.csv").resolve()


def infer_project_root(input_path: Path, run_id: str) -> Path:
    resolved = input_path.expanduser().resolve()
    suffix = Path("output") / "runs" / run_id / "today_10_topics.csv"
    suffix_parts = suffix.parts
    if len(resolved.parts) <= len(suffix_parts) or resolved.parts[-len(suffix_parts):] != suffix_parts:
        raise ExactInputError("non_exact_input_path")
    return Path(*resolved.parts[:-len(suffix_parts)])


def normalized_owner_value(row: dict[str, Any], field: str) -> str:
    value = row.get(field, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ExactInputError(f"malformed_exact_input_field:{field}")
    return value.strip()


def row_identity(row: dict[str, Any], index: int) -> dict[str, Any]:
    identity = {field: normalized_owner_value(row, field) for field in IDENTITY_FIELDS}
    for field in REQUIRED_COLUMNS:
        if not identity[field]:
            raise ExactInputError(f"empty_exact_input_field:{index}:{field}")
    return {
        "index": index,
        "exact_url": identity["来源链接"],
        "content_fingerprint": identity["内容指纹"],
        "source_type": identity["来源类型"],
        "source_content": identity["来源内容"],
        "original_source_title": identity["原始来源标题"],
        "original_publication_copy": identity["原始发布文案"],
        "source_account": identity["原始来源账号"],
        "platform": identity["平台"],
        "candidate_fingerprint": hash_json(identity),
        "row_hash": hash_json({key: str(value or "") for key, value in row.items()}),
    }


def load_exact_input(
    input_path: Path,
    *,
    run_id: str,
    expected_sha256: str,
    project_root: Path,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    run_date = parse_run_id(run_id)
    expected_path = expected_input_path(project_root, run_id)
    resolved_path = input_path.expanduser().resolve()
    if resolved_path != expected_path:
        raise ExactInputError("non_exact_input_path")
    if not resolved_path.is_file():
        raise ExactInputError("missing_exact_input")
    expected_hash = str(expected_sha256 or "").strip().lower()
    if not SHA256_RE.fullmatch(expected_hash):
        raise ExactInputError("invalid_exact_input_sha256")
    actual_hash = file_sha256(resolved_path)
    if actual_hash != expected_hash:
        raise ExactInputError("exact_input_sha256_mismatch")
    with resolved_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        missing_columns = [field for field in REQUIRED_COLUMNS if field not in fieldnames]
        if missing_columns:
            raise ExactInputError("missing_exact_input_columns:" + ",".join(missing_columns))
        rows = list(reader)
    if not rows:
        raise ExactInputError("empty_exact_input")
    identities = [row_identity(row, index) for index, row in enumerate(rows)]
    content_fingerprints = [item["content_fingerprint"] for item in identities]
    candidate_fingerprints = [item["candidate_fingerprint"] for item in identities]
    for values, reason in (
        (content_fingerprints, "duplicate_exact_input_content_fingerprint"),
        (candidate_fingerprints, "duplicate_exact_input_candidate_fingerprint"),
    ):
        if len(values) != len(set(values)):
            raise ExactInputError(reason)
    manifest = {
        "schema_version": "ar033b_exact_candidate_input_v1",
        "mode": "exact_same_day_candidate_input",
        "run_id": run_id,
        "run_date": run_date,
        "input_path": str(resolved_path),
        "input_file_sha256": actual_hash,
        "row_count": len(rows),
        "ordered_candidates": identities,
        "ordered_candidate_fingerprints": candidate_fingerprints,
    }
    manifest["manifest_hash"] = hash_json(manifest)
    return rows, manifest


def validate_candidate_lineage(candidates: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    expected = list(manifest.get("ordered_candidates") or [])
    if len(candidates) != len(expected):
        raise ExactInputError("exact_input_candidate_count_drift")
    for index, (candidate, identity) in enumerate(zip(candidates, expected)):
        expected_values = {
            "index": index,
            "exact_url": identity["exact_url"],
            "content_fingerprint": identity["content_fingerprint"],
            "candidate_fingerprint": identity["candidate_fingerprint"],
            "csv_title": identity["original_source_title"],
            "original_publication_copy": identity["original_publication_copy"],
            "source_account": identity["source_account"],
            "source_type": identity["source_type"],
            "platform": identity["platform"],
        }
        for field, expected_value in expected_values.items():
            if candidate.get(field) != expected_value:
                raise ExactInputError(f"exact_input_identity_drift:{index}:{field}")


def revalidate_locked_manifest(manifest: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if not isinstance(manifest, dict):
        raise ExactInputError("malformed_exact_input_manifest")
    clean = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    if hash_json(clean) != manifest.get("manifest_hash"):
        raise ExactInputError("exact_input_manifest_hash_mismatch")
    if manifest.get("schema_version") != "ar033b_exact_candidate_input_v1":
        raise ExactInputError("exact_input_manifest_schema_mismatch")
    if manifest.get("mode") != "exact_same_day_candidate_input":
        raise ExactInputError("exact_input_manifest_mode_mismatch")
    input_path = Path(str(manifest.get("input_path") or ""))
    run_id = str(manifest.get("run_id") or "")
    rows, reconstructed = load_exact_input(
        input_path,
        run_id=run_id,
        expected_sha256=str(manifest.get("input_file_sha256") or ""),
        project_root=infer_project_root(input_path, run_id),
    )
    if reconstructed != manifest:
        raise ExactInputError("exact_input_manifest_source_drift")
    return rows, reconstructed

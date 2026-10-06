"""Reconcile repeated segment observations without discarding raw evidence."""
from __future__ import annotations

import copy
import re
from typing import Any


def shift_whisper_times(result: dict[str, Any], offset: float) -> dict[str, Any]:
    """Convert local Whisper segment and word times to source-global seconds."""
    shifted = copy.deepcopy(result)
    for part in shifted.get("segments", []) or []:
        part["start"] = float(part["start"]) + offset
        part["end"] = float(part["end"]) + offset
        for word in part.get("words", []) or []:
            word["start"] = float(word["start"]) + offset
            word["end"] = float(word["end"]) + offset
    shifted["timestamp_basis"] = "global_source_seconds"
    return shifted


def segment_intervals(
    duration: float,
    *,
    segment_seconds: float = 480,
    overlap_seconds: float = 5,
    maximum_source_seconds: float = 1200,
) -> list[dict[str, float]]:
    if duration <= 0 or duration > maximum_source_seconds:
        raise ValueError("source_duration_outside_long_media_policy")
    if segment_seconds <= 0 or segment_seconds > 600 or overlap_seconds < 0 or overlap_seconds >= segment_seconds:
        raise ValueError("segment_policy_invalid")
    rows = []
    start = 0.0
    while start < duration:
        end = min(duration, start + segment_seconds)
        rows.append({"start": round(start, 3), "end": round(end, 3)})
        if end >= duration:
            break
        start = end - overlap_seconds
    return rows


def segment_coverage(rows: list[dict[str, Any]], duration: float) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: float(row["start"]))
    covered_end = 0.0
    gaps = []
    for row in ordered:
        start, end = float(row["start"]), float(row["end"])
        if start > covered_end + 0.05:
            gaps.append({"start": covered_end, "end": start})
        covered_end = max(covered_end, end)
    if covered_end < duration - 0.05:
        gaps.append({"start": covered_end, "end": duration})
    return {
        "expected": [0, duration],
        "covered_end": covered_end,
        "gaps": gaps,
        "full_coverage": not gaps,
        "intervals": ordered,
    }


def reconcile_ocr(segments: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[tuple[float, float, str], dict[str, Any]] = {}
    origins: dict[tuple[float, float, str], list[dict[str, Any]]] = {}
    variants: dict[tuple[float, float], set[str]] = {}
    for segment in segments:
        for index, original in enumerate(segment.get("caption_timeline", []) or []):
            row = copy.deepcopy(original)
            key = (float(row["start"]), float(row["end"]), str(row["text"]))
            origin = {
                "segment_index": segment["index"],
                "observation_index": index,
                "frame_sha256": row.get("frame_sha256"),
            }
            groups.setdefault(key, row)
            origins.setdefault(key, []).append(origin)
            variants.setdefault(key[:2], set()).add(key[2])
    merged = []
    trace = []
    for key, row in sorted(groups.items()):
        row["raw_observation_refs"] = origins[key]
        variant_count = len(variants[key[:2]])
        if variant_count > 1:
            row["recognition_variants_at_same_time"] = variant_count
        merged.append(row)
        if len(origins[key]) > 1 or variant_count > 1:
            trace.append({
                "start": key[0], "end": key[1], "text": key[2],
                "raw_observation_refs": origins[key],
                "copies_collapsed": len(origins[key]) - 1,
                "variant_count": variant_count,
                "variant_texts": sorted(variants[key[:2]]),
                "decision": "collapse_identical_text_time_preserve_variants",
            })
    return merged, trace


def reconcile_primary_asr(segments: list[dict[str, Any]]) -> dict[str, Any]:
    raw = [{
        "segment_index": row["index"], "start": row["start"], "end": row["end"],
        "text": str((row.get("asr") or {}).get("text") or ""),
    } for row in segments]
    cleaned = [re.sub(r"<\|[^|>]+\|>", "", row["text"]) for row in raw]
    output = []
    trace = []
    for index, current in enumerate(cleaned):
        removed = ""
        overlap = 0.0
        if index:
            overlap = max(0.0, float(raw[index - 1]["end"]) - float(raw[index]["start"]))
            if overlap:
                previous = cleaned[index - 1]
                for length in range(min(len(previous), len(current)), 7, -1):
                    if previous.endswith(current[:length]):
                        removed = current[:length]
                        break
                trace.append({
                    "left_segment": raw[index - 1]["segment_index"],
                    "right_segment": raw[index]["segment_index"],
                    "overlap_seconds": overlap,
                    "removed_identical_prefix": removed,
                    "removed_characters": len(removed),
                    "decision": "exact_suffix_prefix_match" if removed else "untimed_boundary_not_aligned",
                    "raw_asr_retained": True,
                })
        output.append(
            "[全片 %.2f–%.2f 秒；分段转写，非逐字认证；重叠 %.2f 秒]\n%s"
            % (float(raw[index]["start"]), float(raw[index]["end"]), overlap, current[len(removed):])
        )
    return {
        "text": "\n\n".join(output),
        "raw_segment_texts": raw,
        "boundary_reconciliation": trace,
        "canonical_transcript_verified": False,
        "unaligned_boundaries": sum(row["decision"] == "untimed_boundary_not_aligned" for row in trace),
    }


def reconcile_whisper(segments: list[dict[str, Any]]) -> dict[str, Any]:
    parts = []
    trace = []
    boundaries = []
    dropped_words: set[tuple[int, int, int]] = set()
    dropped_parts: set[tuple[int, int]] = set()
    fallbacks = [row.get("full_large_v3_fallback") or {} for row in segments]

    def span(fallback: dict[str, Any]) -> tuple[float, float] | None:
        rows = fallback.get("segments") or []
        if not rows:
            return None
        return min(float(row["start"]) for row in rows), max(float(row["end"]) for row in rows)

    def tokens(fallback: dict[str, Any]) -> list[tuple[int, int, dict[str, Any]]]:
        return [
            (part_index, word_index, word)
            for part_index, row in enumerate(fallback.get("segments", []))
            for word_index, word in enumerate(row.get("words") or [])
        ]

    def same_timed_word(left: dict[str, Any], right: dict[str, Any]) -> bool:
        same_text = str(left.get("word") or "").strip() == str(right.get("word") or "").strip()
        overlaps = max(float(left["start"]), float(right["start"])) < min(float(left["end"]), float(right["end"]))
        same_point = float(left["start"]) == float(left["end"]) == float(right["start"]) == float(right["end"])
        return bool(same_text and (overlaps or same_point))

    for index in range(1, len(segments)):
        left, right = span(fallbacks[index - 1]), span(fallbacks[index])
        if not left or not right or max(left[0], right[0]) >= min(left[1], right[1]):
            boundaries.append({
                "left_segment": segments[index - 1]["index"],
                "right_segment": segments[index]["index"],
                "decision": "preserve_all_no_actual_fallback_span_overlap",
                "left_fallback_span": left, "right_fallback_span": right,
            })
            continue
        low, high = max(left[0], right[0]), min(left[1], right[1])
        split = (low + high) / 2
        matched_right: set[tuple[int, int]] = set()
        pairs = []
        for left_part, left_word, left_value in tokens(fallbacks[index - 1]):
            if float(left_value["end"]) < low or float(left_value["start"]) > high:
                continue
            candidates = [
                (right_part, right_word, right_value)
                for right_part, right_word, right_value in tokens(fallbacks[index])
                if (right_part, right_word) not in matched_right
                and same_timed_word(left_value, right_value)
            ]
            if not candidates:
                continue
            right_part, right_word, right_value = min(
                candidates,
                key=lambda row: abs(
                    (float(row[2]["start"]) + float(row[2]["end"]))
                    - (float(left_value["start"]) + float(left_value["end"]))
                ),
            )
            matched_right.add((right_part, right_word))
            midpoint = (
                float(left_value["start"]) + float(left_value["end"])
                + float(right_value["start"]) + float(right_value["end"])
            ) / 4
            remove = (index, right_part, right_word) if midpoint < split else (index - 1, left_part, left_word)
            dropped_words.add(remove)
            pairs.append({
                "left_word_ref": [segments[index - 1]["index"], left_part, left_word],
                "right_word_ref": [segments[index]["index"], right_part, right_word],
                "removed_word_ref": [segments[remove[0]]["index"], remove[1], remove[2]],
                "word": left_value["word"],
                "rule": "same_text_overlapping_word_times",
            })
        for left_part, left_row in enumerate(fallbacks[index - 1].get("segments", [])):
            if left_row.get("words"):
                continue
            for right_part, right_row in enumerate(fallbacks[index].get("segments", [])):
                if not right_row.get("words") and (
                    left_row["start"], left_row["end"], left_row["text"]
                ) == (right_row["start"], right_row["end"], right_row["text"]):
                    dropped_parts.add((index, right_part))
        boundaries.append({
            "left_segment": segments[index - 1]["index"],
            "right_segment": segments[index]["index"],
            "decision": "collapse_proven_word_duplicates_preserve_unique_variants",
            "actual_fallback_overlap": [low, high],
            "split_seconds": split,
            "duplicate_word_pairs": pairs,
        })

    for segment_index, source in enumerate(segments):
        fallback = fallbacks[segment_index]
        for part_index, original in enumerate(fallback.get("segments", [])):
            row = copy.deepcopy(original)
            words = row.get("words") or []
            retained = [
                word for word_index, word in enumerate(words)
                if (segment_index, part_index, word_index) not in dropped_words
            ]
            if words:
                if retained:
                    row["words"] = retained
                    row["text"] = "".join(str(word["word"]) for word in retained)
                    row["start"] = retained[0]["start"]
                    row["end"] = retained[-1]["end"]
                    parts.append(row)
                trace.append({
                    "segment_index": segments[segment_index]["index"],
                    "fallback_part_index": part_index,
                    "raw_words": len(words), "retained_words": len(retained),
                    "rule": "preserve_unique_words_collapse_timed_duplicates_only",
                    "original_normalized_part_retained": True,
                })
            elif (segment_index, part_index) not in dropped_parts:
                parts.append(row)
                trace.append({
                    "segment_index": segments[segment_index]["index"],
                    "fallback_part_index": part_index,
                    "rule": "preserve_part_without_word_alignment",
                    "boundary_text_precision_not_verified": True,
                })
    parts.sort(key=lambda row: (float(row["start"]), float(row["end"])))
    return {
        "segments": parts,
        "text": "".join(str(row.get("text") or "") for row in parts),
        "timestamp_basis": "global_source_seconds",
        "boundary_reconciliation": trace,
        "fallback_boundary_decisions": boundaries,
        "raw_normalized_fallbacks": [row.get("full_large_v3_fallback") for row in segments],
        "canonical_transcript_verified": False,
    }

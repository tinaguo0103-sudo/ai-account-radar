from __future__ import annotations

import unittest

from video_evidence_merge import (
    reconcile_ocr,
    reconcile_whisper,
    segment_coverage,
    segment_intervals,
    shift_whisper_times,
)


class VideoEvidenceMergeTest(unittest.TestCase):
    @staticmethod
    def words(values: list[str], start: float = 475.0) -> list[dict]:
        return [
            {"word": value, "start": start + index * 0.1, "end": start + index * 0.1 + 0.08}
            for index, value in enumerate(values)
        ]

    def test_long_source_intervals_cover_with_bounded_overlap(self):
        rows = segment_intervals(982.27)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], {"start": 0.0, "end": 480.0})
        self.assertEqual(rows[1], {"start": 475.0, "end": 955.0})
        self.assertEqual(rows[2], {"start": 950.0, "end": 982.27})
        coverage = segment_coverage(rows, 982.27)
        self.assertTrue(coverage["full_coverage"])
        self.assertEqual(coverage["gaps"], [])

    def test_whisper_times_are_shifted_to_global_source_seconds(self):
        raw = {"segments": [{"start": 0.5, "end": 1.5, "words": [{"start": 0.7, "end": 0.9, "word": "AI"}]}]}
        shifted = shift_whisper_times(raw, 475.0)
        self.assertEqual(shifted["segments"][0]["start"], 475.5)
        self.assertEqual(shifted["segments"][0]["words"][0]["start"], 475.7)
        self.assertEqual(shifted["timestamp_basis"], "global_source_seconds")
        self.assertEqual(raw["segments"][0]["start"], 0.5)

    def test_ocr_identical_overlap_collapses_but_variants_and_raw_trace_remain(self):
        timeline, trace = reconcile_ocr([
            {"index": 0, "caption_timeline": [
                {"start": 12.0, "end": 13.0, "text": "OpenAI", "frame_sha256": "a"},
            ]},
            {"index": 1, "caption_timeline": [
                {"start": 12.0, "end": 13.0, "text": "OpenAI", "frame_sha256": "b"},
                {"start": 12.0, "end": 13.0, "text": "OpenA1", "frame_sha256": "c"},
            ]},
        ])
        self.assertEqual(len(timeline), 2)
        self.assertEqual({row["text"] for row in timeline}, {"OpenAI", "OpenA1"})
        identical = next(row for row in timeline if row["text"] == "OpenAI")
        self.assertEqual(identical["recognition_variants_at_same_time"], 2)
        self.assertEqual(sum(row["copies_collapsed"] for row in trace), 1)
        self.assertEqual(len(identical["raw_observation_refs"]), 2)

    def test_whisper_18_plus_13_boundary_merges_only_timed_duplicates(self):
        left_words = [f"w{index}" for index in range(18)]
        right_words = [*left_words[:8], "w0", "variant-w8", "unique-a", "unique-b", "unique-c"]
        left = {
            "index": 0, "start": 0.0, "end": 480.0,
            "full_large_v3_fallback": {
                "segments": [{"start": 475.0, "end": 477.0, "text": "".join(left_words), "words": self.words(left_words)}],
            },
        }
        right_word_rows = self.words(right_words)
        # One repeated spelling outside the actual matching time remains a distinct observation.
        right_word_rows[8]["start"], right_word_rows[8]["end"] = 478.0, 478.1
        right = {
            "index": 1, "start": 475.0, "end": 955.0,
            "full_large_v3_fallback": {
                "segments": [{"start": 475.0, "end": 479.0, "text": "".join(right_words), "words": right_word_rows}],
            },
        }
        merged = reconcile_whisper([left, right])
        retained = [word for row in merged["segments"] for word in row.get("words", [])]
        self.assertEqual(len(left_words) + len(right_words), 31)
        self.assertEqual(len(retained), 23)
        self.assertTrue(all(word in [row["word"] for row in retained] for word in left_words))
        self.assertTrue(all(word in [row["word"] for row in retained] for word in right_words[8:]))
        boundary = merged["fallback_boundary_decisions"][0]
        self.assertEqual(boundary["decision"], "collapse_proven_word_duplicates_preserve_unique_variants")
        self.assertEqual(len(boundary["duplicate_word_pairs"]), 8)
        self.assertEqual(len(merged["raw_normalized_fallbacks"]), 2)


if __name__ == "__main__":
    unittest.main()

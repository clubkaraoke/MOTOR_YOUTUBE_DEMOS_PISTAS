import unittest

from engine.extractor import CDGLyricsExtractor


class HybridCoverageTests(unittest.TestCase):
    def test_removed_pixels_can_trigger_rewrite(self) -> None:
        extractor = CDGLyricsExtractor(
            min_added_pixels=0.012,
            min_rewrite_pixels=0.012,
        )
        self.assertTrue(
            extractor._meaningful_stable_rewrite(
                added_ratio=0.004,
                removed_ratio=0.010,
            )
        )
        self.assertFalse(
            extractor._meaningful_stable_rewrite(
                added_ratio=0.003,
                removed_ratio=0.004,
            )
        )

    def test_sparse_native_triggers_hybrid(self) -> None:
        native = {
            "duration_seconds": 240,
            "pages_detected": 8,
            "page_events": [
                {"time": 0},
                {"time": 8},
                {"time": 62},
                {"time": 119},
                {"time": 180},
            ],
            "lines_detected": 40,
            "average_confidence": 90,
        }

        self.assertTrue(
            CDGLyricsExtractor._native_needs_hybrid(native)
        )

    def test_dense_native_does_not_trigger_hybrid(self) -> None:
        native = {
            "duration_seconds": 240,
            "pages_detected": 28,
            "page_events": [
                {"time": value}
                for value in range(0, 240, 9)
            ],
            "lines_detected": 70,
            "average_confidence": 92,
        }

        self.assertFalse(
            CDGLyricsExtractor._native_needs_hybrid(native)
        )

    def test_fallback_wins_when_coverage_is_much_better(self) -> None:
        native = {
            "duration_seconds": 240,
            "pages_detected": 10,
            "page_events": [
                {"time": 0},
                {"time": 60},
                {"time": 120},
                {"time": 180},
            ],
            "lines_detected": 40,
            "average_confidence": 90,
            "strategy": "native_page_adaptive_ocr",
        }
        fallback = {
            "duration_seconds": 240,
            "pages_detected": 24,
            "page_events": [
                {"time": value}
                for value in range(0, 240, 10)
            ],
            "lines_detected": 65,
            "average_confidence": 87,
            "strategy": "stable_frame_fallback",
        }

        selected = CDGLyricsExtractor._select_hybrid_result(
            native,
            fallback,
        )
        self.assertEqual(
            selected["strategy"],
            "hybrid_fallback_selected",
        )

    def test_native_wins_when_fallback_is_noisy(self) -> None:
        native = {
            "duration_seconds": 240,
            "pages_detected": 10,
            "page_events": [
                {"time": 0},
                {"time": 60},
                {"time": 120},
                {"time": 180},
            ],
            "lines_detected": 45,
            "average_confidence": 91,
            "strategy": "native_page_adaptive_ocr",
        }
        fallback = {
            "duration_seconds": 240,
            "pages_detected": 30,
            "page_events": [
                {"time": value}
                for value in range(0, 240, 8)
            ],
            "lines_detected": 80,
            "average_confidence": 72,
            "strategy": "stable_frame_fallback",
        }

        selected = CDGLyricsExtractor._select_hybrid_result(
            native,
            fallback,
        )
        self.assertEqual(
            selected["strategy"],
            "hybrid_native_selected",
        )


if __name__ == "__main__":
    unittest.main()

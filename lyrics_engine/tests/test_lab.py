import unittest

from engine.lab import LabAnalyzer
from engine.text_corrector import TextCorrector


class LabAnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        corrector = TextCorrector(
            frequencies={
                "enganaste": 0,
                "en": 6_000_000,
                "ganaste": 5_000,
                "ahora": 1_000_000,
                "sola": 80_000,
            }
        )
        self.lab = LabAnalyzer(
            corrector=corrector
        )

    def test_flags_sparse_page_cdgs(self) -> None:
        result = {
            "filename": "Ada - Te fuiste.cdg",
            "duration_seconds": 229.27,
            "pages_detected": 9,
            "page_events": [
                {"time": 0},
                {"time": 8.1},
                {"time": 58.2},
                {"time": 65.9},
                {"time": 123.8},
                {"time": 176.4},
            ],
            "average_confidence": 89.52,
            "quality": "REVISAR",
            "lines_detected": 21,
            "lines": [
                {"confidence": 90}
                for _ in range(18)
            ] + [
                {"confidence": 70}
                for _ in range(3)
            ],
            "corrections_count": 18,
            "corrections": [],
            "ocr_fallback_pages": 1,
        }

        score = self.lab.analyze_result(
            result
        )

        self.assertIn(
            "LOW_PAGE_DENSITY",
            score.flags,
        )
        self.assertIn(
            "LARGE_PAGE_GAP",
            score.flags,
        )
        self.assertLess(
            score.score,
            result["average_confidence"],
        )

    def test_learns_only_as_evidence(self) -> None:
        results = []

        for index in range(3):
            results.append(
                {
                    "filename": f"song-{index}.cdg",
                    "lines": [
                        {"confidence": 91.0}
                    ],
                    "corrections": [
                        {
                            "line_index": 0,
                            "from": "VAGÍO",
                            "to": "VACÍO",
                            "type": "dictionary",
                        }
                    ],
                }
            )

        learned = (
            self.lab.learned_corrections(
                results
            )
        )

        self.assertEqual(
            learned[0]["from"],
            "VAGÍO",
        )
        self.assertEqual(
            learned[0]["to"],
            "VACÍO",
        )
        self.assertEqual(
            learned[0]["files"],
            3,
        )
        self.assertEqual(
            learned[0]["status"],
            "CANDIDATA",
        )

    def test_summary_orders_worst_first(self) -> None:
        results = [
            {
                "filename": "good.cdg",
                "duration_seconds": 180,
                "pages_detected": 20,
                "page_events": [
                    {"time": value}
                    for value in range(
                        0,
                        180,
                        9,
                    )
                ],
                "average_confidence": 95,
                "quality": "BUENA",
                "lines_detected": 30,
                "lines": [
                    {"confidence": 95}
                    for _ in range(30)
                ],
                "corrections_count": 2,
                "corrections": [],
            },
            {
                "filename": "bad.cdg",
                "duration_seconds": 180,
                "pages_detected": 4,
                "page_events": [
                    {"time": 0},
                    {"time": 60},
                    {"time": 120},
                ],
                "average_confidence": 72,
                "quality": "MALA",
                "lines_detected": 10,
                "lines": [
                    {"confidence": 60}
                    for _ in range(10)
                ],
                "corrections_count": 8,
                "corrections": [],
            },
        ]

        summary = self.lab.summarize(
            results
        )

        self.assertEqual(
            summary["files_analyzed"],
            2,
        )
        self.assertEqual(
            summary["worst_files"][0][
                "filename"
            ],
            "bad.cdg",
        )


if __name__ == "__main__":
    unittest.main()

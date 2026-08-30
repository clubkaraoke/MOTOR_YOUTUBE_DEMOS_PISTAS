import unittest

import importlib.util
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "engine"
    / "text_corrector.py"
)
SPEC = importlib.util.spec_from_file_location(
    "cdg_text_corrector",
    MODULE_PATH,
)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
TextCorrector = MODULE.TextCorrector


class TextCorrectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.corrector = TextCorrector(
            frequencies={
                "con": 3_000_000,
                "dice": 240_000,
                "te": 3_000_000,
                "veo": 162_000,
                "saludo": 3_898,
                "muchachita": 584,
                "loco": 100_000,
                "logo": 50_000,
                "tremendo": 20_000,
                "y": 7_000_000,
                "no": 12_000_000,
                "como": 1_600_000,
                "hace": 495_000,
                "de": 14_000_000,
                "la": 9_000_000,
                "dela": 673,
                "a": 15_000_000,
                "mi": 2_400_000,
                "ami": 1083,
                "coro": 4_929,
                "goro": 288,
                "candeleras": 0,
                "gandeleras": 0,
            }
        )

    def test_confusion_g_to_c(self) -> None:
        corrected, kind = (
            self.corrector.correct_token(
                "GON"
            )
        )
        self.assertEqual(
            corrected,
            "CON",
        )
        self.assertEqual(
            kind,
            "dictionary",
        )

    def test_two_confusions_in_same_word(self) -> None:
        corrected, _ = (
            self.corrector.correct_token(
                "MUGHAGHITA"
            )
        )
        self.assertEqual(
            corrected,
            "MUCHACHITA",
        )

    def test_split_joined_words(self) -> None:
        corrected, kind = (
            self.corrector.correct_token(
                "TEVEO"
            )
        )
        self.assertEqual(
            corrected,
            "TE VEO",
        )
        self.assertEqual(
            kind,
            "split",
        )

    def test_split_with_corrected_segment(self) -> None:
        corrected, _ = (
            self.corrector.correct_token(
                "YHO"
            )
        )
        self.assertEqual(
            corrected,
            "Y NO",
        )

    def test_digits_inside_letters(self) -> None:
        corrected, _ = (
            self.corrector.correct_token(
                "YG0M0"
            )
        )
        self.assertEqual(
            corrected,
            "Y COMO",
        )

    def test_keep_valid_word_when_alternative_not_dominant(self) -> None:
        corrected, kind = (
            self.corrector.correct_token(
                "LOGO"
            )
        )
        self.assertEqual(
            corrected,
            "LOGO",
        )
        self.assertIsNone(kind)

    def test_split_low_frequency_glued_function_words(self) -> None:
        corrected, kind = (
            self.corrector.correct_token(
                "DELA"
            )
        )
        self.assertEqual(
            corrected,
            "DE LA",
        )
        self.assertEqual(
            kind,
            "split",
        )

        corrected, kind = (
            self.corrector.correct_token(
                "AMI"
            )
        )
        self.assertEqual(
            corrected,
            "A MI",
        )
        self.assertEqual(
            kind,
            "split",
        )

    def test_context_prefers_repeated_higher_confidence_variant(self) -> None:
        lines = [
            {
                "text": "ME GUSTA LAS CANDELERAS",
                "confidence": 92.0,
            },
            {
                "text": "GANDELERAS LAS",
                "confidence": 77.0,
            },
        ]

        corrected, changes = (
            self.corrector.correct_lines(
                lines
            )
        )

        self.assertEqual(
            corrected[1]["text"],
            "CANDELERAS LAS",
        )
        self.assertTrue(
            any(
                change["type"]
                == "song_context"
                for change in changes
            )
        )

    def test_context_uses_song_repetition_for_goro_coro(self) -> None:
        lines = [
            {
                "text": "EL CORO QUE",
                "confidence": 90.0,
            },
            {
                "text": "Y COMO DIGE EL GORO",
                "confidence": 84.0,
            },
        ]

        corrected, _ = (
            self.corrector.correct_lines(
                lines
            )
        )

        self.assertIn(
            "CORO",
            corrected[1]["text"],
        )

    def test_yes_que_phrase_but_keep_plain_english_yes(self) -> None:
        corrected, _ = (
            self.corrector.correct_line(
                "YES QUE YO SOY ASÍ"
            )
        )
        self.assertEqual(
            corrected,
            "Y ES QUE YO SOY ASÍ",
        )

        token, kind = (
            self.corrector.correct_token(
                "YES"
            )
        )
        self.assertEqual(
            token,
            "YES",
        )
        self.assertIsNone(kind)


if __name__ == "__main__":
    unittest.main()

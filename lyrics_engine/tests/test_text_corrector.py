import unittest

from engine.text_corrector import TextCorrector


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


if __name__ == "__main__":
    unittest.main()

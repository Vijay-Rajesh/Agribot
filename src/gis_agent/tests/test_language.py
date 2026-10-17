import unittest

from services.language import detect_language


class LanguageDetectionTests(unittest.TestCase):
    def test_detects_english(self):
        self.assertEqual(detect_language("What is the weather today?"), "english")

    def test_detects_roman_urdu(self):
        self.assertEqual(detect_language("Meri fasal ka haal batao"), "hinglish")
        self.assertEqual(detect_language("Khet ko pani kab dena chahiye?"), "hinglish")

    def test_detects_urdu_script(self):
        self.assertEqual(detect_language("میری فصل کا حال بتائیں"), "urdu")

    def test_keeps_previous_language_for_coordinate_only_message(self):
        self.assertEqual(detect_language("31.5204, 74.3587", "urdu"), "urdu")

    def test_switches_language_on_each_message(self):
        self.assertEqual(detect_language("Please check the weather", "hinglish"), "english")
        self.assertEqual(detect_language("موسم بتائیں", "english"), "urdu")


if __name__ == "__main__":
    unittest.main()

import unittest

from Agents.planting_suitability_agent import create_planting_suitability_agent
from services.planting_suitability import (
    is_planting_suitability_request,
    planting_suitability_image_instructions,
)


class PlantingSuitabilityRoutingTests(unittest.TestCase):
    def test_detects_english_suitability_questions(self):
        self.assertTrue(
            is_planting_suitability_request(
                "Can I grow mangoes on this farm in winter?"
            )
        )

    def test_detects_roman_urdu_suitability_questions(self):
        self.assertTrue(
            is_planting_suitability_request(
                "Kya is zameen mein strawberry laga sakta hoon?"
            )
        )
        self.assertTrue(
            is_planting_suitability_request(
                "yahan strawberries laga skte hain is month mai ?"
            )
        )
        self.assertTrue(
            is_planting_suitability_request(
                "karachi mai oct k month mai field pr kon kon se fruits lagaye ja skte hain"
            )
        )

    def test_detects_urdu_script_suitability_questions(self):
        self.assertTrue(
            is_planting_suitability_request(
                "کیا یہ پھل یہاں لگا سکتے ہیں؟"
            )
        )

    def test_does_not_route_ordinary_crop_disease_questions(self):
        self.assertFalse(
            is_planting_suitability_request("What disease is affecting my crop?")
        )


class PlantingSuitabilityAgentTests(unittest.TestCase):
    def test_specialist_instructions_are_cautious_and_localized(self):
        agent = create_planting_suitability_agent("hinglish")

        self.assertEqual(agent.name, "Planting Suitability Advisor")
        self.assertIn("soil texture, pH, salinity, drainage", agent.instructions)
        self.assertIn("Roman-script Urdu/Hindi", agent.instructions)
        self.assertIn("preliminary verdict", agent.instructions)
        self.assertIn("Never answer a planting question with a disease", agent.instructions)
        self.assertIn("at most 100 words", agent.instructions)
        self.assertIn("materially change the answer", agent.instructions)
        self.assertIn("Do not routinely ask for location", agent.instructions)
        self.assertIn("Never ask for details", agent.instructions)

    def test_suitability_photo_prompt_never_uses_disease_template(self):
        expected_phrases = {
            "english": (
                "NOT crop disease detection",
                "only when a missing detail would materially change",
                "Do not ask for location or planting month/season unless",
            ),
            "hinglish": (
                "disease detection NAHI",
                "sirf jab missing detail se recommendation waqai badal sakti ho",
                "Location ya planting month/season tab tak na poochein",
            ),
            "urdu": (
                "فصل کی بیماری کی تشخیص نہیں",
                "کسی ضروری معلومات کی کمی سے سفارش واقعی بدل سکتی ہو",
                "مقام یا کاشت کا مہینہ/موسم تب تک نہ پوچھیں",
            ),
        }
        for language, expected_phrases_for_language in expected_phrases.items():
            with self.subTest(language=language):
                instructions = planting_suitability_image_instructions(language)
                for expected_phrase in expected_phrases_for_language:
                    self.assertIn(expected_phrase, instructions)
                self.assertNotIn("Most likely disease/condition:", instructions)
                self.assertNotIn("Sabse mumkin disease/condition:", instructions)
                self.assertNotIn("زیادہ ممکنہ بیماری/مسئلہ:", instructions)


if __name__ == "__main__":
    unittest.main()

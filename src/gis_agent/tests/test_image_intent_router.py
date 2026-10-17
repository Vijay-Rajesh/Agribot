import unittest

from Agents.farmbot_agent import create_farmbot_agent
from Agents.image_intent_router import (
    CLARIFICATION_MARKER,
    create_combined_field_image_agent,
    create_image_intent_router_agent,
    extract_intent_clarification,
)


class ImageIntentRouterTests(unittest.TestCase):
    def test_extracts_selectable_clarification_without_exposing_marker(self):
        self.assertEqual(
            extract_intent_clarification(
                f"  {CLARIFICATION_MARKER}Disease check ya planting advice?"
            ),
            "Disease check ya planting advice?",
        )
        self.assertIsNone(extract_intent_clarification("Clear planting request"))

    def test_router_has_specialists_and_general_farmbot_handoff(self):
        router = create_image_intent_router_agent(
            "hinglish",
            farmbot_agent=create_farmbot_agent("hinglish"),
        )

        self.assertEqual(
            {agent.name for agent in router.handoffs},
            {
                "Crop Disease and Symptom Advisor",
                "Planting Suitability Advisor",
                "FarmBot",
            },
        )
        self.assertIn("Do not use keyword matching alone", router.instructions)
        self.assertIn(CLARIFICATION_MARKER, router.instructions)
        self.assertIn("Roman Urdu/Hindi", router.instructions)

    def test_combined_choice_uses_concise_assessment(self):
        agent = create_combined_field_image_agent("urdu")

        self.assertEqual(agent.name, "Combined Field Photo Advisor")
        self.assertIn("under 100 words", agent.instructions)
        self.assertIn("Urdu", agent.instructions)


if __name__ == "__main__":
    unittest.main()

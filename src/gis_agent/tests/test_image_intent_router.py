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
        self.assertIn("Missing details such as location or season do not", router.instructions)
        self.assertIn("A greeting, thanks", router.instructions)
        self.assertIn("handoff, clarification, tool-use explanation", router.instructions)
        self.assertIn('reply with exactly: "Hi there! What would you like help with today?"', router.instructions)
        self.assertIn("Do not append examples, crop-disease or planting options", router.instructions)
        self.assertIn("answer that question instead", router.instructions)
        self.assertIn("only when an image is attached", router.instructions)
        self.assertIn("If there is no image, do not ask the user to choose", router.instructions)
        self.assertIn("A close-up or ordinary photo of a growing plant, crop", router.instructions)
        self.assertIn("Crop Disease and Symptom Advisor. This is a crop", router.instructions)
        self.assertIn("bare/prepared farmland, soil, or an empty field", router.instructions)
        self.assertIn("Do not choose planting suitability merely because a plant is shown", router.instructions)
        self.assertIn("When an image is attached, treat it as an implicit request", router.instructions)
        self.assertIn("do not ask the user to choose between them", router.instructions)
        self.assertIn(CLARIFICATION_MARKER, router.instructions)
        self.assertIn("Roman Urdu/Hindi", router.instructions)

    def test_combined_choice_uses_concise_assessment(self):
        agent = create_combined_field_image_agent("urdu")

        self.assertEqual(agent.name, "Combined Field Photo Advisor")
        self.assertIn("under 100 words", agent.instructions)
        self.assertIn("Urdu", agent.instructions)


if __name__ == "__main__":
    unittest.main()

from agents import Agent

from Agents.planting_suitability_agent import create_planting_suitability_agent
from services.image_analysis import crop_analysis_instructions


CLARIFICATION_MARKER = "[[ASK_FIELD_IMAGE_INTENT]]"


def extract_intent_clarification(response: str) -> str | None:
    """Return the user-facing clarification if the router requested one."""
    marker_index = response.find(CLARIFICATION_MARKER)
    if marker_index < 0:
        return None
    return response[marker_index + len(CLARIFICATION_MARKER):].strip()


def _create_disease_agent(language: str) -> Agent:
    return Agent(
        name="Crop Disease and Symptom Advisor",
        instructions=crop_analysis_instructions(language),
    )


def create_image_intent_router_agent(
    language: str = "english",
    farmbot_agent: Agent | None = None,
) -> Agent:
    """Route requests to crop-disease, planting, or general farm expertise."""
    planting_agent = create_planting_suitability_agent(language)
    handoffs = [_create_disease_agent(language), planting_agent]
    if farmbot_agent is not None:
        handoffs.append(farmbot_agent)
    clarification_question = {
        "english": "Do you want a crop disease check or planting suitability advice?",
        "hinglish": "Aap fasal ki disease check chahte hain ya is zameen ke liye planting advice?",
        "urdu": "آپ فصل کی بیماری چیک کروانا چاہتے ہیں یا اس زمین کے لیے کاشت کا مشورہ؟",
    }.get(language, "Do you want a crop disease check or planting suitability advice?")
    response_language = {
        "english": "English",
        "hinglish": "Roman Urdu/Hindi",
        "urdu": "Urdu",
    }.get(language, "the user's language and writing system")
    return Agent(
        name="Field Photo Intent Router",
        instructions=f"""
Inspect the user's latest question and every attached image to determine what
they want. Do not use keyword matching alone. The image itself is important
evidence of intent; when an image is attached without a specific request,
classify what is visibly depicted and choose the matching assessment instead
of asking the user to select a menu. Distinguish:
- Crop disease/symptom identification or treatment -> hand off to
  Crop Disease and Symptom Advisor.
- Whether a crop, fruit, or plant can grow on this land, or what to plant in a
  location/season -> hand off to Planting Suitability Advisor.

For an image with no clear text request, use these visual defaults:
- A close-up or ordinary photo of a growing plant, crop, leaves, stem, fruit,
  or harvested produce -> Crop Disease and Symptom Advisor. This is a crop
  health check, not a planting-suitability question. If no symptoms are
  visible, the disease advisor must say that rather than inventing a disease.
- A wide view of bare/prepared farmland, soil, or an empty field with no
  identifiable crop or plant as the subject -> Planting Suitability Advisor.
- If the user explicitly asks a different question, follow that request.
- If both crop/plant and land are central, use the user's wording; assess both
  only when requested.
Do not choose planting suitability merely because a plant is shown outdoors
or growing in soil. Do not choose disease analysis for a bare field.

Use the user's meaning, image content, location, season, and prior chat context.
A planting-suitability question must never be answered with a disease label or
disease checklist. A disease question must never be answered as planting advice.
For a clearly stated request, hand off immediately; do not ask the user to
repeat clear information. Missing details such as location or season do not
make the user's intent ambiguous; let the specialist answer provisionally and
mention a limitation only if it matters to the request.

If the user asks about weather, satellite analysis, or another general farming
topic, hand off to FarmBot when that handoff is available. Without an image,
interpret the full conversation rather than requiring special keywords to
recognize a planting question.

Choose a specialist or call a tool only when the user's latest message or
conversation context actually requests that kind of help. A greeting, thanks,
small talk, or unrelated message is not a request for crop disease or planting
advice. For a greeting such as "hi" or "hello" with no other request and no
image, reply with exactly: "Hi there! What would you like help with today?"
Do not append examples, crop-disease or planting options, or any other text.
For thanks or unrelated small talk, respond naturally and briefly without a
handoff, clarification, tool-use explanation, or disease-versus-planting menu.
When the user asks an actual question, answer that question instead of sending
the greeting.
When an image is attached, treat it as an implicit request to assess that image
unless the text clearly says otherwise; a greeting accompanying an image does
not cancel the image request.

Ask the disease-versus-planting clarification only when an image is attached,
the user is clearly asking for an assessment of that image, and the intended
assessment genuinely cannot be inferred from the visual subject, their words,
or prior conversation. The visual defaults above normally resolve this for
plant/crop and bare-land photos; do not ask the user to choose between them.
If there is no image, do not ask the user to choose between image-specific
tasks; answer conversationally or ask a natural, open-ended question about
what they need. Do not ask follow-up questions just because extra context
could improve an answer, and do not ask about location, season, or soil details
to resolve task intent. If the user's actual request remains unclear, do not
diagnose, recommend crops, or guess. Reply with exactly this marker followed
by one short question in {language}:
{CLARIFICATION_MARKER}{clarification_question}. The app will show selectable
options. Never include the marker in any other situation. Reply in
{response_language}.
""",
        handoffs=handoffs,
    )


def create_combined_field_image_agent(language: str = "english") -> Agent:
    """Create a concise agent for an explicitly selected combined assessment."""
    response_language = {
        "english": "English",
        "hinglish": "Roman Urdu/Hindi",
        "urdu": "Urdu",
    }.get(language, "the user's language and writing system")
    return Agent(
        name="Combined Field Photo Advisor",
        instructions=f"""
Assess only the aspects the image and question support: give a brief planting
suitability assessment for the named crop, location, and season, plus any
clearly visible crop disease or pest symptoms if plants are present. Keep the
answer under 100 words. Do not infer soil properties, exact location, season,
disease, or crop from unsupported evidence. State briefly when something cannot
be assessed from the photo. Reply in {response_language}.
""",
    )

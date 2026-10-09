import asyncio
import logging
import math
import re

import chainlit as cl
from agents import Agent

from Agents.farmbot_agent import create_farmbot_agent
from config import initialize_earth_engine, setup_gemini, setup_groq
from services.analysis import FarmBotAnalyzer
from services.government import GovernmentSchemes
from services.image_analysis import (
    ImageUploadError,
    crop_analysis_instructions,
    prepare_image_data_urls,
)
from services.model_routing import ModelProviderRouter, model_http_status
from services.weather import WeatherAPI


initialize_earth_engine()
model_router = ModelProviderRouter(setup_groq(), setup_gemini())
logger = logging.getLogger(__name__)


def _temporary_model_error_message(language: str) -> str:
    return _localized(
        language,
        "The AI service is temporarily busy. Please try again in a minute; your request was not completed.",
        "AI service abhi temporary busy hai. Ek minute baad dobara try karein; aapki request complete nahi hui.",
        "AI سروس عارضی طور پر مصروف ہے۔ ایک منٹ بعد دوبارہ کوشش کریں؛ آپ کی درخواست مکمل نہیں ہوئی۔",
    )


def _model_error_message(language: str, error: BaseException) -> str:
    status = model_http_status(error)
    if status == 429 and model_router.gemini_config is None:
        return _localized(
            language,
            "Groq's usage limit was reached, but Gemini fallback is not configured. Add a valid GEMINI_API_KEY or try again after Groq's limit resets.",
            "Groq ki usage limit poori ho gayi hai aur Gemini fallback configured nahi hai. Valid GEMINI_API_KEY add karein ya Groq limit reset hone ke baad try karein.",
            "Groq کی استعمال کی حد پوری ہو گئی ہے، لیکن Gemini fallback فعال نہیں۔ درست GEMINI_API_KEY شامل کریں یا Groq کی حد بحال ہونے کے بعد دوبارہ کوشش کریں۔",
        )
    if status in (400, 401, 403) and not model_router.groq_quota_available:
        return _localized(
            language,
            "Gemini rejected its fallback request. Create a new API key at https://aistudio.google.com/app/apikey, replace GEMINI_API_KEY in the project's root .env file (keep GROQ_API_KEY unchanged), then restart FarmBot. Never send your key in chat.",
            "Gemini fallback request reject ho gayi. https://aistudio.google.com/app/apikey par nayi API key banayein; project ke root .env mein GEMINI_API_KEY replace karein (GROQ_API_KEY ko na badlein), phir FarmBot restart karein. Key chat mein kabhi share na karein.",
            "Gemini کی fallback درخواست مسترد ہو گئی۔ https://aistudio.google.com/app/apikey پر نئی API key بنائیں، project کی root .env فائل میں GEMINI_API_KEY تبدیل کریں (GROQ_API_KEY نہ بدلیں)، پھر FarmBot restart کریں۔ اپنی key چیٹ میں کبھی نہ بھیجیں۔",
        )
    return _temporary_model_error_message(language)


def _groq_fallback_message(language: str) -> str:
    return _localized(
        language,
        "Groq's current usage limit was reached. Continuing with Gemini for this and upcoming requests until Groq's limit resets.",
        "Groq ki current usage limit poori ho gayi. Is request aur agle requests ke liye Groq limit reset hone tak Gemini use hoga.",
        "Groq کی موجودہ استعمال کی حد پوری ہو گئی ہے۔ حد دوبارہ بحال ہونے تک اس اور آئندہ درخواستوں کے لیے Gemini استعمال ہوگا۔",
    )


def _selected_language(message: str) -> str | None:
    normalized = message.strip().lower()
    if re.search(r"\b(hinglish|roman urdu|roman hindi)\b", normalized):
        return "hinglish"
    if re.search(r"\b(urdu|اردو)\b", normalized) or any("\u0600" <= char <= "\u06ff" for char in normalized):
        return "urdu"
    if re.search(r"\b(english|انگریزی)\b", normalized):
        return "english"
    return None


def _message_coordinates(message: str) -> tuple[float, float] | None:
    match = re.search(
        r"(-?\d{1,3}(?:\.\d+)?)\s*[,;]\s*(-?\d{1,3}(?:\.\d+)?)",
        message,
    )
    if not match:
        return None
    first, second = map(float, match.groups())
    if not (-90 <= first <= 90 and -180 <= second <= 180):
        return None
    return first, second


def _location_request(message: str) -> str | None:
    text = message.casefold()
    asks_weather = any(term in text for term in ("weather", "mausam", "موسم"))
    asks_analysis = any(
        term in text for term in (
            "analy", "ndvi", "ndmi", "tajzi", "tajze", "fasal", "crop",
            "khet", "field", "acre", "hectare", "meter", "metre",
            "مٹر", "ایکڑ", "ہیکٹر",
        )
    )
    asks_current_location = any(
        term in text for term in (
            "live location", "current location", "meri location", "mera location",
            "my location", "location le", "location share", "gps", "live jagah",
            "موجودہ مقام", "میری لوکیشن", "میری جگہ",
        )
    )
    if asks_weather:
        return "weather"
    if asks_analysis:
        return "analysis"
    if asks_current_location:
        return "choose"
    return None


def _requested_radius(message: str) -> float | None:
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:m|meter|meters|metre|metres)\b",
        message.casefold(),
    )
    if not match:
        return None
    radius = float(match.group(1))
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError("Radius must be a positive number of metres.")
    return radius


def _requested_area(message: str) -> tuple[float | None, float | None]:
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(acres?|ac|hectares?|hect|ha)\b",
        message.casefold(),
    )
    if not match:
        return None, None
    amount = float(match.group(1))
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("Area must be a positive number of acres or hectares.")
    unit = match.group(2)
    return (amount, None) if unit.startswith("ac") else (None, amount)


def _place_name(message: str, request_type: str) -> str | None:
    text = re.sub(r"[,.!?]+", " ", message).strip()
    stop_words = (
        r"(?:\s+(?:today|now|please|weather|mausam|batao|btao|bataein|"
        r"analyze|analyse|tajzia|tajziya|ndvi|ndmi|for|radius|within|"
        r"today's|ka|ki|ke|hai|hain|karein|kardo)\b.*)?$"
    )
    patterns = [
        r"\b(?:in|at|near|around|for|mein|mai|par)\s+(.+?)" + stop_words,
    ]
    if request_type == "analysis":
        patterns.insert(0, r"\b(?:field|farm|khet)\s+(?:named\s+)?(.+?)" + stop_words)
    else:
        patterns.insert(0, r"^(.+?)\s+(?:ka|ki|ke)\s+(?:(?:aaj|aj|today)\s+)?(?:ka\s+)?(?:weather|mausam)\b")
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            place = re.sub(
                r"^(?:in|at|near|around|par|mein|mai)\s+",
                "",
                match.group(1).strip(" -"),
                flags=re.IGNORECASE,
            )
            if len(place) >= 2 and not re.fullmatch(r"(?:my|mera|meri|a|the|weather)", place):
                return place
    return None


@cl.on_chat_start
async def handle_chat_start():
    cl.user_session.set("history", [])
    cl.user_session.set("preferred_language", None)
    cl.user_session.set("pending_location_request", None)
    cl.user_session.set("pending_location_radius_m", None)
    cl.user_session.set("pending_location_area", None)
    await cl.Message(
        content="Welcome to FarmBot. Which language would you prefer: English, Hinglish, or Urdu?"
    ).send()


@cl.on_message
async def handle_message(message: cl.Message):
    user_input = message.content.strip()
    language = cl.user_session.get("preferred_language")
    image_elements = message.elements or []

    if not language:
        language = _selected_language(user_input)
        if not language:
            image_note = (
                " Please choose a language first, then upload the crop image again."
                if image_elements else ""
            )
            await cl.Message(
                content=(
                    "Please choose English, Hinglish, or Urdu so I can set up your chat."
                    + image_note
                )
            ).send()
            return
        cl.user_session.set("preferred_language", language)
        confirmations = {
            "english": "English selected. Tell me what you would like to analyze.",
            "hinglish": "Hinglish select ho gayi. Batayein aap kya analyze karwana chahte hain.",
            "urdu": "اردو منتخب ہو گئی۔ بتائیے آپ کیا تجزیہ کروانا چاہتے ہیں۔",
        }
        await cl.Message(content=confirmations[language]).send()
        if not image_elements:
            return

    lowered = user_input.lower()

    if image_elements:
        await handle_crop_image(message, user_input, language)
        return

    coordinates = _message_coordinates(user_input)
    pending_request = cl.user_session.get("pending_location_request")
    request_type = _location_request(user_input)

    if coordinates and (pending_request or request_type == "choose" or lowered.startswith("my live location:")):
        pending_radius = cl.user_session.get("pending_location_radius_m")
        cl.user_session.set("pending_location_request", None)
        cl.user_session.set("pending_location_radius_m", None)
        pending_area = cl.user_session.get("pending_location_area")
        cl.user_session.set("pending_location_area", None)
        await _offer_location_actions(
            coordinates,
            language,
            radius_m=pending_radius or _requested_radius(user_input),
            area_acres=pending_area[0] if pending_area else _requested_area(user_input)[0],
            area_hectares=pending_area[1] if pending_area else _requested_area(user_input)[1],
        )
        return

    if request_type is None and pending_request:
        request_type = pending_request["intent"] if isinstance(pending_request, dict) else pending_request

    if request_type == "choose":
        await _ask_for_location(language, "choose")
        return

    if request_type in {"weather", "analysis"} and not coordinates:
        place = _place_name(user_input, request_type)
        if not place:
            cl.user_session.set("pending_location_request", {"intent": request_type})
            if request_type == "analysis":
                cl.user_session.set("pending_location_radius_m", _requested_radius(user_input))
                cl.user_session.set("pending_location_area", _requested_area(user_input))

    async def run_satellite_analysis_tool(
        *,
        latitude: float | None = None,
        longitude: float | None = None,
        place: str | None = None,
        radius_m: float | None = None,
        area_hectares: float | None = None,
        area_acres: float | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> bool:
        return await handle_satellite_analysis(
            user_input=user_input,
            coordinates=(latitude, longitude) if latitude is not None and longitude is not None else None,
            language=language,
            radius_m=radius_m,
            place=place,
            area_acres=area_acres,
            area_hectares=area_hectares,
            date_range=(start_date, end_date) if start_date and end_date else None,
        )

    history = cl.user_session.get("history", [])
    history.append({"role": "user", "content": user_input})
    msg = cl.Message(content="")
    await msg.send()

    async def announce_groq_fallback():
        msg.content = ""
        await msg.update()
        await cl.Message(content=_groq_fallback_message(language)).send()

    try:
        streamed_result = model_router.run_streamed(
            input=history,
            starting_agent=create_farmbot_agent(
                language,
                satellite_analysis=run_satellite_analysis_tool,
            ),
            on_fallback=announce_groq_fallback,
        )
        async for event in streamed_result.stream_events():
            if event.type == "raw_response_event" and hasattr(event.data, "delta"):
                await msg.stream_token(event.data.delta)

        final_output = streamed_result.final_output or _localized(
            language,
            "I couldn't generate a response. Please rephrase your question.",
            "Mujhe jawab generate karne mein problem hui. Sawal thora aur clear likhein.",
            "جواب تیار نہیں ہو سکا۔ براہِ کرم سوال دوبارہ واضح انداز میں لکھیں۔",
        )
        msg.content = final_output
        history.append({"role": "assistant", "content": final_output})
        cl.user_session.set("history", history)
    except Exception as exc:
        logger.exception("FarmBot response generation failed")
        if model_http_status(exc) in (400, 401, 403, 429, 503):
            msg.content = _model_error_message(language, exc)
        else:
            msg.content = _localized(
                language,
                "Sorry, I couldn't process that request right now. Please try again.",
                "Maazrat, abhi request process nahi ho saki. Dobara koshish karein.",
                "معذرت، ابھی آپ کی درخواست مکمل نہیں ہو سکی۔ دوبارہ کوشش کریں۔",
            )
    await msg.update()


async def handle_crop_image(message: cl.Message, user_input: str, language: str):
    try:
        image_data_urls = prepare_image_data_urls(message.elements or [])
    except ImageUploadError as exc:
        logger.info("Rejected crop image upload: %s", exc)
        await cl.Message(
            content=_localized(
                language,
                f"I couldn't use that upload: {exc} Please attach up to three clear JPG, PNG, or WebP crop photos (12 MB maximum each).",
                f"Yeh image use nahi ho saki: {exc} Kripya 12 MB tak ki, zyada se zyada 3 clear JPG, PNG ya WebP crop photos bhejein.",
                f"یہ تصویر استعمال نہیں ہو سکی: {exc} براہِ کرم زیادہ سے زیادہ 3 واضح JPG، PNG یا WebP تصاویر بھیجیں، ہر تصویر 12 MB تک ہو۔",
            )
        ).send()
        return

    question = user_input or _localized(
        language,
        "Please assess the visible crop symptoms and possible causes in this image.",
        "Is image mein fasal ki nazar aane wali alamat aur mumkin wajahen assess karein.",
        "براہِ کرم تصویر میں فصل کی نظر آنے والی علامات اور ممکنہ وجوہات کا جائزہ لیں۔",
    )
    image_instruction = crop_analysis_instructions(language)
    text = f"{image_instruction}\n\nUser question: {question}"
    content = [{"type": "input_text", "text": text}]
    content.extend(
        {
            "type": "input_image",
            "image_url": data_url,
            "detail": "high",
        }
        for data_url in image_data_urls
    )

    history = cl.user_session.get("history", [])
    msg = cl.Message(content="")
    await msg.send()

    async def announce_groq_fallback():
        msg.content = ""
        await msg.update()
        await cl.Message(content=_groq_fallback_message(language)).send()

    try:
        streamed_result = model_router.run_streamed(
            input=history + [{"role": "user", "content": content}],
            starting_agent=create_farmbot_agent(language),
            on_fallback=announce_groq_fallback,
        )
        async for event in streamed_result.stream_events():
            if event.type == "raw_response_event" and hasattr(event.data, "delta"):
                await msg.stream_token(event.data.delta)

        final_output = streamed_result.final_output or _localized(
            language,
            "I couldn't assess the image. Please try a clearer crop photo.",
            "Image assess nahi ho saki. Kripya crop ki zyada clear photo bhejein.",
            "تصویر کا جائزہ نہیں ہو سکا۔ براہِ کرم فصل کی زیادہ واضح تصویر بھیجیں۔",
        )
        msg.content = final_output
        history.extend(
            [
                {
                    "role": "user",
                    "content": f"[Crop image attached; image available for this turn only] {question}",
                },
                {"role": "assistant", "content": final_output},
            ]
        )
        cl.user_session.set("history", history)
    except Exception as exc:
        logger.exception("Crop image analysis failed")
        if model_http_status(exc) in (400, 401, 403, 429, 503):
            msg.content = _model_error_message(language, exc)
        else:
            msg.content = _localized(
                language,
                "I couldn't analyze the image right now. Please try again; if this continues, check the Groq API key/model and the Gemini fallback key.",
                "Abhi image analyze nahi ho saki. Dobara koshish karein; agar masla rahe to Groq API key/model aur Gemini fallback key check karein.",
                "ابھی تصویر کا تجزیہ نہیں ہو سکا۔ دوبارہ کوشش کریں؛ مسئلہ برقرار رہے تو Groq API key/model اور Gemini fallback key چیک کریں۔",
            )
    await msg.update()


def _localized(language: str, english: str, hinglish: str, urdu: str) -> str:
    return {"english": english, "hinglish": hinglish, "urdu": urdu}.get(language, english)


def _index_value(value) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else "No valid data"


async def _ask_for_location(
    language: str,
    request_type: str,
    radius_m: float | None = None,
    area: tuple[float | None, float | None] | None = None,
):
    cl.user_session.set("pending_location_request", {"intent": request_type})
    cl.user_session.set("pending_location_radius_m", radius_m)
    cl.user_session.set("pending_location_area", area)
    message = _localized(
        language,
        "I can't access your device location without permission. Click **Share live location** beside the message box and allow browser access, or send coordinates. For today's weather, you can also name a city.",
        "Main permission ke baghair aapki device location nahi le sakta. Message box ke paas **Share live location** dabayein aur browser access allow karein, ya coordinates bhejein. Aaj ke weather ke liye city ka naam bhi de sakte hain.",
        "میں اجازت کے بغیر آپ کے آلے کا مقام حاصل نہیں کر سکتا۔ میسج باکس کے پاس **Share live location** دبائیں اور اجازت دیں، یا coordinates بھیجیں۔ آج کے موسم کے لیے شہر کا نام بھی لکھ سکتے ہیں۔",
    )
    await cl.Message(content=message).send()


async def _offer_location_actions(
    coordinates: tuple[float, float],
    language: str,
    radius_m: float | None = None,
    area_acres: float | None = None,
    area_hectares: float | None = None,
):
    labels = {
        "english": (
            "What would you like me to do with this location?",
            "Analyze NDVI / NDMI",
            "Check weather",
            "Analyze + weather",
        ),
        "hinglish": (
            "Is location ke liye kya karna hai?",
            "NDVI / NDMI analyze",
            "Weather dekho",
            "Dono: analyze + weather",
        ),
        "urdu": (
            "اس مقام کے لیے کیا کرنا ہے؟",
            "NDVI / NDMI کا تجزیہ",
            "موسم دیکھیں",
            "دونوں: تجزیہ اور موسم",
        ),
    }[language]
    shared_payload = {
        "latitude": coordinates[0],
        "longitude": coordinates[1],
        "radius_m": radius_m,
        "area_acres": area_acres,
        "area_hectares": area_hectares,
        "language": language,
    }
    actions = [
        cl.Action(
            name="farmbot_location_action",
            payload={**shared_payload, "choice": choice},
            label=label,
        )
        for choice, label in (
            ("analyze", labels[1]),
            ("weather", labels[2]),
            ("both", labels[3]),
        )
    ]
    await cl.Message(content=labels[0], actions=actions).send()


@cl.action_callback("farmbot_location_action")
async def handle_location_action(action: cl.Action):
    payload = action.payload
    coordinates = (float(payload["latitude"]), float(payload["longitude"]))
    language = payload.get("language", "english")
    choice = payload.get("choice")
    if choice == "weather":
        await handle_weather_query("", language, coordinates)
    elif choice in {"analyze", "both"}:
        await handle_satellite_analysis(
            user_input="",
            coordinates=coordinates,
            language=language,
            radius_m=payload.get("radius_m"),
            area_acres=payload.get("area_acres"),
            area_hectares=payload.get("area_hectares"),
            include_weather=choice == "both",
        )
    else:
        logger.error("Unknown live-location action: %r", choice)
        await cl.Message(content=_localized(
            language,
            "That location option is not recognized. Please share your location again.",
            "Yeh location option samajh nahi aaya. Location dobara share karein.",
            "یہ مقام والا اختیار درست نہیں۔ براہِ کرم مقام دوبارہ بھیجیں۔",
        )).send()


def _analysis_progress_text(event: dict, language: str) -> str:
    date = event["date"]
    position = f"{event['date_index']}/{event['total_dates']}"
    status = event["status"]
    if status == "started":
        return _localized(
            language,
            f"Processing satellite date {date} ({position})...",
            f"Satellite date {date} process ho rahi hai ({position})...",
            f"سیٹلائٹ تاریخ {date} پر کام جاری ہے ({position})...",
        )
    index_name = status.removesuffix("_ready")
    return _localized(
        language,
        f"{date} ({position}): {index_name} map and pixel data ready.",
        f"{date} ({position}): {index_name} map aur pixel data tayyar.",
        f"{date} ({position}): {index_name} کا نقشہ اور pixel data تیار ہے۔",
    )


async def handle_satellite_analysis(
    user_input: str,
    coordinates: tuple[float, float] | None,
    language: str,
    radius_m: float | None = None,
    place: str | None = None,
    area_acres: float | None = None,
    area_hectares: float | None = None,
    date_range: tuple[str, str] | None = None,
    include_weather: bool = True,
) -> bool:
    if date_range:
        period = f"{date_range[0]} to {date_range[1]}"
        progress_message = _localized(
            language,
            f"Analyzing Sentinel-2 NDVI and NDMI for {period}. This may take a minute...",
            f"{period} ke Sentinel-2 NDVI aur NDMI analyze ho rahe hain. Thora waqt lag sakta hai...",
            f"{period} کے Sentinel-2 NDVI اور NDMI کا تجزیہ جاری ہے۔ کچھ وقت لگ سکتا ہے۔",
        )
    else:
        progress_message = _localized(
            language,
            "Analyzing Sentinel-2 NDVI and NDMI for the last two months. This may take a minute...",
            "Pichhle do mahinoñ ke Sentinel-2 NDVI aur NDMI analyze ho rahe hain. Thora waqt lag sakta hai...",
            "گزشتہ دو ماہ کے Sentinel-2 NDVI اور NDMI کا تجزیہ جاری ہے۔ کچھ وقت لگ سکتا ہے۔",
        )
    progress = cl.Message(content=progress_message)
    await progress.send()
    try:
        parsed_acres, parsed_hectares = _requested_area(user_input)
        area_acres = area_acres if area_acres is not None else parsed_acres
        area_hectares = area_hectares if area_hectares is not None else parsed_hectares
        loop = asyncio.get_running_loop()
        progress_events: asyncio.Queue = asyncio.Queue()

        def report_progress(event: dict):
            loop.call_soon_threadsafe(progress_events.put_nowait, event)

        analysis_task = asyncio.create_task(asyncio.to_thread(
            FarmBotAnalyzer.get_analysis_data,
            coords=[coordinates] if coordinates else None,
            date_range=date_range,
            place=place,
            radius_m=radius_m or _requested_radius(user_input),
            area_acres=area_acres,
            area_hectares=area_hectares,
            analysis_type="ndvi_ndmi",
            progress_callback=report_progress,
        ))
        while not analysis_task.done():
            try:
                event = await asyncio.wait_for(progress_events.get(), timeout=0.5)
                progress.content = _analysis_progress_text(event, language)
                await progress.update()
            except asyncio.TimeoutError:
                continue
        while not progress_events.empty():
            progress.content = _analysis_progress_text(progress_events.get_nowait(), language)
            await progress.update()
        result = await analysis_task
        point = result["point_0"]
        progress.content = _localized(
            language,
            "Satellite maps are ready. Preparing the report...",
            "Satellite maps tayyar hain. Report prepare ho rahi hai...",
            "سیٹلائٹ نقشے تیار ہیں۔ رپورٹ تیار ہو رہی ہے...",
        )
        if include_weather:
            progress.content = _localized(
                language,
                "Satellite maps are ready. Collecting current weather and preparing the report...",
                "Satellite maps tayyar hain. Current weather aur report prepare ho rahi hai...",
                "سیٹلائٹ نقشے تیار ہیں۔ موجودہ موسم اور رپورٹ تیار ہو رہی ہے...",
            )
        await progress.update()
        weather = (
            await asyncio.to_thread(
                WeatherAPI.get_weather,
                point["coordinates"][0],
                point["coordinates"][1],
            )
            if include_weather
            else {"not_requested": True}
        )
        ai_analysis_error = None
        try:
            ai_analysis = await _generate_satellite_recommendations(point, weather, language)
        except Exception as error:
            logger.exception("AI satellite report generation failed")
            ai_analysis_error = f"{type(error).__name__}: {error}"
            ai_analysis = (
                "AI recommendations could not be generated for this run. "
                "The measurements below are still included; retry to request AI advice."
            )
        report_path = await asyncio.to_thread(
            FarmBotAnalyzer.generate_satellite_report,
            point,
            weather,
            ai_analysis,
            language,
        )
        content = _localized(
            language,
            f"**Satellite analysis — every available date**\n\nLocation: {point['coordinates'][0]:.5f}, {point['coordinates'][1]:.5f}\nArea: {point['area']}\nPeriod: {point['analysis_period']}\nAcquisition dates: {len(point['periods'])}\nAverage NDVI: {_index_value(point['ndvi'])} ({point['crop_health']})\nAverage NDMI: {_index_value(point['ndmi'])} (vegetation moisture proxy)\n\nFilter: <10% scene cloud cover; same-date scenes combined into one daily map.\n\n",
            f"**Satellite analysis — har available date**\n\nLocation: {point['coordinates'][0]:.5f}, {point['coordinates'][1]:.5f}\nArea: {point['area']}\nPeriod: {point['analysis_period']}\nSatellite dates: {len(point['periods'])}\nAverage NDVI: {_index_value(point['ndvi'])} ({point['crop_health']})\nAverage NDMI: {_index_value(point['ndmi'])} (vegetation moisture proxy)\n\nFilter: <10% scene cloud cover; same-date scenes combined into one daily map.\n\n",
            f"**ہر دستیاب تاریخ کا سیٹلائٹ تجزیہ**\n\nمقام: {point['coordinates'][0]:.5f}, {point['coordinates'][1]:.5f}\nرقبہ: {point['area']}\nمدت: {point['analysis_period']}\nسیٹلائٹ تاریخیں: {len(point['periods'])}\nاوسط NDVI: {_index_value(point['ndvi'])} ({point['crop_health']})\nاوسط NDMI: {_index_value(point['ndmi'])} (نباتاتی نمی کا اشاریہ)\n\nبادلوں کی حد: 10% سے کم؛ ایک تاریخ کی متعدد تصاویر سے ایک نقشہ بنایا جاتا ہے۔\n\n",
        )
        images = []
        content += "| Date | NDVI | NDMI | S2 scenes | NDVI/NDMI pixel samples |\n|---|---:|---:|---:|---:|\n"
        for period in point["periods"]:
            content += (
                f"| {period['selected_date']} | {_index_value(period['ndvi'])} | "
                f"{_index_value(period['ndmi'])} | {period['image_count']} | "
                f"{len(period['ndvi_pixels'])}/{len(period['ndmi_pixels'])} |\n"
            )
            for index_name in ("ndvi", "ndmi"):
                path = period.get(f"{index_name}_image")
                if path:
                    images.append(cl.Image(
                        name=f"{index_name.upper()} — {period['selected_date']}",
                        path=path,
                        display="inline",
                    ))
        if not include_weather:
            content += "\n**Weather:** Not requested.\n"
        elif not weather.get("error"):
            content += (
                f"\n**Current weather ({weather['timestamp']})** — "
                f"{weather['temperature']} C, {weather['conditions']}, "
                f"humidity {weather['humidity']}%, wind {weather['wind_speed']} km/h, "
                f"precipitation {weather['rain']} mm.\n"
            )
        else:
            content += f"\n**Weather:** {weather['error']}\n"
        content += (
            "\n**AI interpretation and recommendations**\n\n"
            + ai_analysis
            + (
                f"\n\nAI report error: {ai_analysis_error}"
                if ai_analysis_error
                else ""
            )
            + (
                "\n\nWeather in the report is the current observation, not historical weather "
                "for each satellite date."
                if include_weather
                else ""
            )
            + " Full sampled pixel coordinates/values are in the JSON attachment."
        )
        images.extend([
            cl.File(
                name="FarmBot satellite analysis report.pdf",
                path=report_path,
                display="inline",
            ),
            cl.File(
                name="NDVI and NDMI pixel values by date.json",
                path=point["pixel_data_file"],
                display="inline",
            ),
        ])
        await progress.remove()
        await cl.Message(content=content, elements=images).send()
        return True
    except Exception as error:
        logger.exception("Satellite analysis failed")
        progress.content = _localized(
            language,
            f"Analysis failed: {type(error).__name__}: {str(error)[:400]}",
            f"Analysis mein error: {type(error).__name__}: {str(error)[:400]}",
            f"تجزیے میں خرابی: {type(error).__name__}: {str(error)[:400]}",
        )
        await progress.update()
        return False


async def _generate_satellite_recommendations(point: dict, weather: dict, language: str) -> str:
    """Ask the configured language model for evidence-grounded crop advice."""
    evidence = {
        "location": point.get("place") or point["coordinates"],
        "analysis_area": point["area"],
        "period": point["analysis_period"],
        "average_ndvi": point["ndvi"],
        "average_ndmi": point["ndmi"],
        "ndvi_health_class": point["crop_health"],
        "per_acquisition_date": [
            {
                "date": item["selected_date"],
                "sentinel2_scene_count": item["image_count"],
                "ndvi_mean": item["ndvi"],
                "ndmi_mean": item["ndmi"],
            }
            for item in point["periods"]
        ],
        "current_weather": None if weather.get("not_requested") else weather,
    }
    report_agent = Agent(
        name="FarmBot Satellite Report Analyst",
        instructions=f"""
Write detailed, practical agricultural monitoring recommendations in
{"Roman-script Hinglish" if language == "hinglish" else "English"} using only
the supplied evidence. Structure the report with: executive summary, observed
NDVI/NDMI trend and dates, interpretation, actionable field checks, irrigation
and crop-management guidance, and limitations/next monitoring steps.
NDVI describes vegetation greenness, not a definitive diagnosis. NDMI is a
vegetation/canopy moisture proxy, not a direct soil-moisture sensor. Provided weather
is a current observation only; do not associate it with historical satellite
dates. Do not infer crop type, growth stage, disease, fertilizer deficiency,
or causation when not provided. Tie each claim to the reported values/dates,
and mark recommendations as general where farm-specific evidence is missing.
Keep the output detailed but suitable for a professional downloadable report.
""",
    )
    result = await model_router.run(
        starting_agent=report_agent,
        input=json.dumps(evidence, ensure_ascii=False),
    )
    if not isinstance(result.final_output, str) or not result.final_output.strip():
        raise ValueError("AI report generation returned no recommendations.")
    return result.final_output.strip()


async def handle_government_schemes(user_input: str, language: str):
    """Handle government scheme queries."""
    scheme_name = next(
        (name for name in GovernmentSchemes.SCHEMES if name.lower() in user_input.lower()),
        None,
    )
    scheme_info = GovernmentSchemes.get_scheme_info(scheme_name, language)

    if "error" in scheme_info:
        prefix = (
            "معذرت، کوئی اسکیم نہیں ملی۔ دستیاب اسکیمیں:"
            if language == "urdu"
            else "Sorry, no scheme found. Available schemes are:"
        )
        await cl.Message(
            content=prefix + "\n" + "\n".join(f"- {name}" for name in GovernmentSchemes.SCHEMES)
        ).send()
        return

    if scheme_name:
        if language == "urdu":
            response = (
                f"**{scheme_name}**\n\nتفصیل: {scheme_info['description']}\n"
                f"اہلیت: {scheme_info['eligibility']}\nفوائد: {scheme_info['benefits']}"
            )
        else:
            response = (
                f"**{scheme_name}**\n\nDescription: {scheme_info['description']}\n"
                f"Eligibility: {scheme_info['eligibility']}\nBenefits: {scheme_info['benefits']}"
            )
    else:
        title = "پاکستانی سرکاری زرعی اسکیمیں" if language == "urdu" else "Pakistani Government Farming Schemes"
        response = f"**{title}**\n\n" + "\n\n".join(
            f"**{name}**\n{details['description']}"
            for name, details in scheme_info.items()
        )
    await cl.Message(content=response).send()


async def handle_weather_query(
    user_input: str,
    language: str,
    coordinates: tuple[float, float] | None = None,
):
    """Fetch current weather at coordinates, live location, or a named city."""
    location = coordinates or _message_coordinates(user_input)
    place = None if location else _place_name(user_input, "weather")
    if not location and place:
        try:
            lat, lon, place = await asyncio.to_thread(FarmBotAnalyzer._geocode_place, place)
            location = (lat, lon)
        except Exception:
            logger.exception("Weather location lookup failed")
            await cl.Message(content=_localized(
                language,
                "I couldn't find that place. Please check the city name or share your live location.",
                "Yeh jagah nahi mili. City ka naam check karein ya live location share karein.",
                "یہ مقام نہیں ملا۔ شہر کا نام درست کریں یا موجودہ مقام بھیجیں۔",
            )).send()
            return

    if not location:
        await _ask_for_location(language, "weather")
        return

    lat, lon = location
    weather = await asyncio.to_thread(WeatherAPI.get_weather, lat, lon)
    if "error" in weather:
        message = _localized(
            language,
            "I couldn't fetch today's weather. Please try again later.",
            f"Aaj ka weather fetch nahi ho saka: {weather['error']}",
            f"آج کا موسم حاصل نہیں ہو سکا: {weather['error']}",
        )
        await cl.Message(content=message).send()
        return

    display_location = place or f"{lat:.5f}, {lon:.5f}"
    if language == "urdu":
        response = (
            f"📍 آج کا موسم — {display_location} ({weather['timestamp']})\n\nدرجہ حرارت: {weather['temperature']}°C\n"
            f"نمی: {weather['humidity']}%\nہوا کی رفتار: {weather['wind_speed']} km/h\n"
            f"حالات: {weather['conditions'].capitalize()}\nبارش: {weather['rain']} mm"
        )
    elif language == "hinglish":
        response = (
            f"📍 Aaj ka weather — {display_location} ({weather['timestamp']})\n\nTemperature: {weather['temperature']}°C\n"
            f"Humidity: {weather['humidity']}%\nWind: {weather['wind_speed']} km/h\n"
            f"Conditions: {weather['conditions'].capitalize()}\nRain: {weather['rain']} mm"
        )
    else:
        response = (
            f"📍 Today's weather — {display_location} ({weather['timestamp']})\n\nTemperature: {weather['temperature']}°C\n"
            f"Humidity: {weather['humidity']}%\nWind speed: {weather['wind_speed']} km/h\n"
            f"Conditions: {weather['conditions'].capitalize()}\nRain: {weather['rain']} mm"
        )
    await cl.Message(content=response).send()

import asyncio
import json
import math
import random
from collections.abc import Awaitable, Callable
from typing import Optional

import chainlit as cl
from agents import Agent, function_tool

from services.analysis import FarmBotAnalyzer
from services.government import GovernmentSchemes
from services.weather import WeatherAPI


FARMING_PHRASES = [
    "Allah barkat de aap ki fasal ko! 🌱",
    "Mashallah, aap ke khet ki sehat achi hai! 💚",
    "Thora aur pani aur mehnat, phir dekho kamal! 💧",
    "Fasal ki hifazat ke liye dua karein, Allah madad karega 🤲",
    "Kheti mein barkat ka sirf Allah hi haqdar hai 🌾",
    "Apni mehnat par bharosa rakho, rizq dena Allah ka kaam hai 🌿",
]


def create_farmbot_agent(
    preferred_language: str = "english",
    satellite_analysis: Callable[..., Awaitable[bool]] | None = None,
) -> Agent:
    """Create FarmBot with an Earth Engine NDVI/NDMI analysis tool."""

    @function_tool
    async def analyze_satellite_indices(
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        place: Optional[str] = None,
        radius_m: Optional[float] = None,
        area_hectares: Optional[float] = None,
        area_acres: Optional[float] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> str:
        """Analyze NDVI and NDMI for a coordinate/place and radius or area.

        Use latitude/longitude for coordinates, or place for a named location.
        Radius is the centre-to-edge distance of a square, in metres.
        Provide hectares or acres for square area requests.
        Dates must be YYYY-MM-DD; omitted dates mean the previous two months.
        """
        if (latitude is None) != (longitude is None):
            raise ValueError("Provide both latitude and longitude, or provide a place name.")
        if latitude is None and not place:
            return json.dumps({
                "needs_location": True,
                "message": (
                    "Ask the user for a city/place or coordinates. For their device's "
                    "current location, they must explicitly use Share live location."
                ),
            })
        coordinates = [(latitude, longitude)] if latitude is not None and longitude is not None else None
        date_range = (start_date, end_date) if start_date and end_date else None
        if (start_date is None) != (end_date is None):
            raise ValueError("Provide both start_date and end_date, or omit both.")

        if satellite_analysis is not None:
            completed = await satellite_analysis(
                latitude=latitude,
                longitude=longitude,
                place=place,
                radius_m=radius_m,
                area_hectares=area_hectares,
                area_acres=area_acres,
                start_date=start_date,
                end_date=end_date,
            )
            return json.dumps({
                "completed": completed,
                "message": (
                    "The full satellite report, maps, and pixel data are displayed in chat."
                    if completed
                    else "Satellite analysis failed. The error is displayed in chat."
                ),
            }, ensure_ascii=False)

        analysis_data = FarmBotAnalyzer.get_analysis_data(
            coords=coordinates,
            date_range=date_range,
            analysis_type="ndvi_ndmi",
            place=place,
            radius_m=radius_m,
            area_hectares=area_hectares,
            area_acres=area_acres,
        )
        point = analysis_data["point_0"]

        elements = []
        for period in point["periods"]:
            for index_name in ("ndvi", "ndmi"):
                image_path = period.get(f"{index_name}_image")
                if image_path:
                    elements.append(cl.Image(
                        name=f"{index_name.upper()} — {period['selected_date']}",
                        path=image_path,
                        display="inline",
                    ))
        elements.append(cl.File(
            name="NDVI and NDMI pixel values by date.json",
            path=point["pixel_data_file"],
            display="inline",
        ))
        if elements:
            await cl.Message(content="Satellite maps and sampled pixel data:", elements=elements).send()

        summary = {
            "location": point["place"] or {
                "latitude": point["coordinates"][0],
                "longitude": point["coordinates"][1],
            },
            "analysis_area": point["area"],
            "analysis_period": point["analysis_period"],
            "crop_health_from_average_ndvi": point["crop_health"],
            "acquisition_dates": len(point["periods"]),
            "periods": [
                {
                    "date": period["selected_date"],
                    "sentinel2_images": period["image_count"],
                    "NDVI": period["ndvi"],
                    "NDMI": period["ndmi"],
                    "NDVI_sampled_pixels": len(period["ndvi_pixels"]),
                    "NDMI_sampled_pixels": len(period["ndmi_pixels"]),
                }
                for period in point["periods"]
            ],
        }
        return json.dumps(summary, ensure_ascii=False)

    @function_tool
    async def check_current_weather(
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        place: Optional[str] = None,
    ) -> str:
        """Fetch the current weather for coordinates or a named city/place.

        Provide both latitude and longitude, or a place name. Never guess a
        location; ask the user if they have not provided one.
        """
        if (latitude is None) != (longitude is None):
            raise ValueError("Provide both latitude and longitude, or provide a place name.")
        if latitude is None and not place:
            return json.dumps({
                "needs_location": True,
                "message": (
                    "Ask the user for a city/place or coordinates. For their device's "
                    "current location, they must explicitly use Share live location."
                ),
            })

        if latitude is None:
            latitude, longitude, resolved_place = await asyncio.to_thread(
                FarmBotAnalyzer._geocode_place, place
            )
        else:
            if not math.isfinite(latitude) or not -90 <= latitude <= 90:
                raise ValueError("Latitude must be between -90 and 90.")
            if not math.isfinite(longitude) or not -180 <= longitude <= 180:
                raise ValueError("Longitude must be between -180 and 180.")
            resolved_place = None

        weather = await asyncio.to_thread(
            WeatherAPI.get_weather,
            latitude,
            longitude,
        )
        return json.dumps({
            "location": resolved_place or {
                "latitude": latitude,
                "longitude": longitude,
            },
            "weather": weather,
        }, ensure_ascii=False)

    @function_tool
    def get_government_agricultural_schemes(
        scheme_name: Optional[str] = None,
    ) -> str:
        """Look up the locally available Pakistani agricultural schemes.

        Use this for scheme names, eligibility, and listed benefits. This is
        reference information, not live application or current-status tracking.
        Omit scheme_name to return all available schemes.
        """
        known_name = next(
            (
                name for name in GovernmentSchemes.SCHEMES
                if scheme_name and name.casefold() == scheme_name.strip().casefold()
            ),
            None,
        )
        result = GovernmentSchemes.get_scheme_info(
            known_name or scheme_name,
            preferred_language,
        )
        if "error" in result:
            result = {
                "error": result["error"],
                "available_schemes": list(GovernmentSchemes.SCHEMES),
            }
        return json.dumps(result, ensure_ascii=False)

    language_instructions = {
        "english": "Respond in clear, professional English.",
        "hinglish": "Respond in natural Roman-script Hinglish (Hindi/Urdu mixed with English), not Urdu script.",
        "urdu": "جواب صاف، پیشہ ورانہ اردو رسم الخط میں دیں۔",
    }.get(preferred_language, "Respond in clear, professional English.")

    return Agent(
        name="FarmBot",
        instructions=f"""
You are FarmBot, a professional agricultural assistant. {language_instructions}
Understand natural requests and follow-up messages in the user's chosen language,
including colloquial Hinglish and Urdu. Be concise, respectful, and do not invent
measurements, map boundaries, or satellite results.

Satellite analysis:
- You have one tool for Sentinel-2 NDVI (vegetation health) and NDMI (canopy/vegetation moisture proxy). Use it when a user asks to analyze a field or location.
- Use the current-weather tool for live weather requests and the government-schemes tool for scheme names, eligibility, and listed benefits. Do not claim that static scheme information is current application status.
- Select the appropriate tool from the user's intent even when they use colloquial Urdu, Hinglish, or an indirect phrasing. If a tool reports a missing location, ask the user for a city/place or coordinates; never invent one.
- Understand coordinate order from context; the tool needs separate latitude and longitude. For a named address, pass the address as place. Interpret metres as the centre-to-edge distance of a square: a 200 m radius means a 400 m × 400 m square. Acres/hectares specify the exact area and are converted to square dimensions. If the user gives no size, use a 300 m radius, i.e. a 600 m × 600 m square (360,000 m² / 36 hectares), and disclose that assumption.
- The default date range is the previous two months. Report every available satellite acquisition date separately with that date's NDVI/NDMI mean, scene count, and NDVI-derived health interpretation. Describe NDMI as a vegetation/canopy moisture proxy, not direct soil moisture.
- The geocoder returns a place marker, not cadastral parcel boundaries. State that named addresses such as a block/plot are approximate; coordinates are treated as the centre of the requested square, not as a field polygon.
- You cannot access the user's device GPS silently. If they ask for their live location without coordinates, explain that they must click the app's **Share live location** button and grant browser permission, or paste a map pin/coordinates. Never imply permission was granted.
- Satellite imagery is not guaranteed for every date/area. Clearly report missing coverage, and do not make up results.

Uploaded crop photos:
- When a crop image is attached, start the first line with the most likely disease/condition and a low/medium/high confidence estimate; do not start with a disclaimer or symptom list.
- Then use this order: visible symptoms; what to do now/treatment; prevention; what remains uncertain and how to confirm.
- Give practical, problem-matched steps. For visibly moldy maize, advise against eating or feeding affected grain, separating it from healthy harvest, following local agricultural-extension disposal advice, and keeping unaffected grain dry. Do not imply fungicide cures moldy grain.
- A photo cannot confirm a disease. Do not claim certainty or prescribe a pesticide/product/dose; recommend local agronomist or diagnostic-lab confirmation as appropriate.

For other agricultural questions, answer helpfully but do not claim access to soil tests, government scheme application status, or data not returned by a tool.
""",
        tools=[
            analyze_satellite_indices,
            check_current_weather,
            get_government_agricultural_schemes,
        ],
    )


def get_random_farming_phrase() -> str:
    """Return a random farming phrase."""
    return random.choice(FARMING_PHRASES)

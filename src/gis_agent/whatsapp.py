"""Meta WhatsApp Cloud API webhook for the FarmBot agents."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import hmac
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlparse

from dotenv import find_dotenv, load_dotenv
from fastapi import APIRouter, FastAPI, Request
from flask import Flask, Response, request
import requests
from starlette.responses import JSONResponse, Response as ASGIResponse
from agents import Agent


_PACKAGE_DIRECTORY = str(Path(__file__).resolve().parent)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if _PACKAGE_DIRECTORY not in sys.path:
    sys.path.insert(0, _PACKAGE_DIRECTORY)

load_dotenv(find_dotenv())

from Agents.farmbot_agent import create_farmbot_agent
from Agents.image_intent_router import (
    create_combined_field_image_agent,
    create_image_intent_router_agent,
    extract_intent_clarification,
)
from Agents.planting_suitability_agent import create_planting_suitability_agent
from config import (
    initialize_earth_engine,
    setup_gemini,
    setup_groq,
    setup_openai,
)
from services.analysis import FarmBotAnalyzer
from services.image_analysis import (
    MAX_IMAGE_SIZE_BYTES,
    ImageUploadError,
    crop_analysis_instructions,
    prepare_image_bytes_data_url,
)
from services.language import detect_language
from services.model_routing import ModelProviderRouter
from services.planting_suitability import planting_suitability_image_instructions
from services.weather import WeatherAPI


logger = logging.getLogger(__name__)
_MAX_HISTORY_MESSAGES = 40
_MAX_WHATSAPP_TEXT_LENGTH = 4096
_IMAGE_URL_HOST_SUFFIXES = (".fbsbx.com", ".fbcdn.net", ".facebook.com")


def _localized(language: str, english: str, hinglish: str, urdu: str) -> str:
    return {"english": english, "hinglish": hinglish, "urdu": urdu}.get(language, english)


def _split_message(text: str, max_length: int = _MAX_WHATSAPP_TEXT_LENGTH) -> list[str]:
    parts = []
    remaining = text.strip()
    while len(remaining) > max_length:
        split_at = remaining.rfind("\n", 0, max_length + 1)
        if split_at < max_length // 2:
            split_at = remaining.rfind(" ", 0, max_length + 1)
        if split_at < max_length // 2:
            split_at = max_length
        parts.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def _valid_meta_signature(raw_body: bytes, signature: str | None, app_secret: str) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(
        app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature[7:], expected)


def _verify_webhook(
    mode: str | None,
    supplied_token: str | None,
    challenge: str | None,
    verify_token: str,
) -> tuple[str, int]:
    if not verify_token:
        return "Webhook verification is not configured.", 503
    if mode == "subscribe" and hmac.compare_digest(supplied_token or "", verify_token):
        return challenge or "", 200
    return "Verification failed.", 403


def _missing_whatsapp_config(
    verify_token: str,
    app_secret: str,
    access_token: str,
    phone_number_id: str,
) -> list[str]:
    return [
        name
        for name, value in (
            ("VERIFY_TOKEN", verify_token),
            ("META_APP_SECRET", app_secret),
            ("WHATSAPP_ACCESS_TOKEN", access_token),
            ("WHATSAPP_PHONE_NUMBER_ID", phone_number_id),
        )
        if not value
    ]


def _receive_webhook(
    raw_body: bytes,
    signature: str | None,
    *,
    app_secret: str,
    business_account_id: str,
    store: WhatsAppStore,
    adapter: WhatsAppBot,
    executor: ThreadPoolExecutor,
) -> tuple[str, int]:
    if not app_secret:
        return "Webhook signature verification is not configured.", 503
    if not _valid_meta_signature(raw_body, signature, app_secret):
        return "Invalid webhook signature.", 403
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "Invalid JSON payload.", 400
    if not isinstance(payload, dict):
        return "Invalid JSON payload.", 400

    entries = payload.get("entry", [])
    if not isinstance(entries, list):
        return "Invalid JSON payload.", 400
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if business_account_id and entry.get("id") != business_account_id:
            logger.warning("Ignoring WhatsApp webhook event for an unexpected business account.")
            continue
        changes = entry.get("changes", [])
        if not isinstance(changes, list):
            continue
        for change in changes:
            value = change.get("value", {}) if isinstance(change, dict) else {}
            messages = value.get("messages", []) if isinstance(value, dict) else []
            if not isinstance(messages, list):
                continue
            for message in messages:
                if not isinstance(message, dict):
                    continue
                message_id = message.get("id")
                if not isinstance(message_id, str) or not message_id:
                    logger.warning("Ignoring WhatsApp message without an ID.")
                    continue
                if store.claim_webhook_message(message_id, message):
                    executor.submit(_process_webhook_message, adapter, store, message)
    return "EVENT_RECEIVED", 200


class WhatsAppStore:
    """Small SQLite store for per-number chat state and webhook deduplication."""

    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS whatsapp_users (
                    phone_number TEXT PRIMARY KEY,
                    language TEXT NOT NULL DEFAULT 'english',
                    history_json TEXT NOT NULL DEFAULT '[]',
                    pending_json TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS whatsapp_webhook_events (
                    message_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    message_json TEXT
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(whatsapp_webhook_events)"
                )
            }
            if "message_json" not in columns:
                connection.execute(
                    "ALTER TABLE whatsapp_webhook_events ADD COLUMN message_json TEXT"
                )

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_user(self, phone_number: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT language, history_json, pending_json FROM whatsapp_users WHERE phone_number = ?",
                (phone_number,),
            ).fetchone()
        if row is None:
            return {"language": "english", "history": [], "pending": None}
        return {
            "language": row["language"],
            "history": json.loads(row["history_json"]),
            "pending": json.loads(row["pending_json"]) if row["pending_json"] else None,
        }

    def save_user(
        self,
        phone_number: str,
        *,
        language: str,
        history: list[dict[str, Any]],
        pending: dict[str, Any] | None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO whatsapp_users (phone_number, language, history_json, pending_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(phone_number) DO UPDATE SET
                    language = excluded.language,
                    history_json = excluded.history_json,
                    pending_json = excluded.pending_json
                """,
                (
                    phone_number,
                    language,
                    json.dumps(history[-_MAX_HISTORY_MESSAGES:], ensure_ascii=False),
                    json.dumps(pending, ensure_ascii=False) if pending is not None else None,
                ),
            )

    def claim_webhook_message(self, message_id: str, message: dict[str, Any]) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO whatsapp_webhook_events
                    (message_id, status, message_json)
                VALUES (?, 'queued', ?)
                """,
                (message_id, json.dumps(message, ensure_ascii=False)),
            )
            if cursor.rowcount == 1:
                return True
            cursor = connection.execute(
                """
                UPDATE whatsapp_webhook_events
                SET status = 'queued', message_json = ?
                WHERE message_id = ? AND status = 'failed'
                """,
                (json.dumps(message, ensure_ascii=False), message_id),
            )
            return cursor.rowcount == 1

    def queued_webhook_messages(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT message_id, message_json
                FROM whatsapp_webhook_events
                WHERE status = 'queued' AND message_json IS NOT NULL
                ORDER BY received_at, rowid
                """
            ).fetchall()
        return [
            {"message_id": row["message_id"], "message": json.loads(row["message_json"])}
            for row in rows
        ]

    def set_webhook_message_status(self, message_id: str, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE whatsapp_webhook_events SET status = ? WHERE message_id = ?",
                (status, message_id),
            )


class WhatsAppBot:
    def __init__(
        self,
        *,
        store: WhatsAppStore,
        access_token: str,
        phone_number_id: str,
        api_version: str,
        model_router: ModelProviderRouter | None = None,
    ):
        self.store = store
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.api_version = api_version
        self._model_router = model_router
        self._model_router_guard = threading.Lock()
        self._earth_engine_initialized = False
        self._phone_locks: dict[str, threading.Lock] = {}
        self._phone_locks_guard = threading.Lock()
        self._model_loop: asyncio.AbstractEventLoop | None = None
        self._model_loop_thread: threading.Thread | None = None
        self._model_loop_guard = threading.Lock()

    def _get_model_router(self) -> ModelProviderRouter:
        with self._model_router_guard:
            if self._model_router is None:
                self._model_router = ModelProviderRouter(
                    setup_openai(),
                    setup_groq(),
                    setup_gemini(),
                )
            return self._model_router

    def _get_model_loop(self) -> asyncio.AbstractEventLoop:
        with self._model_loop_guard:
            if self._model_loop is None:
                loop = asyncio.new_event_loop()
                started = threading.Event()

                def run_loop() -> None:
                    asyncio.set_event_loop(loop)
                    started.set()
                    try:
                        loop.run_forever()
                    finally:
                        pending_tasks = asyncio.all_tasks(loop)
                        for task in pending_tasks:
                            task.cancel()
                        if pending_tasks:
                            loop.run_until_complete(
                                asyncio.gather(*pending_tasks, return_exceptions=True)
                            )
                        loop.run_until_complete(loop.shutdown_asyncgens())
                        loop.close()

                thread = threading.Thread(
                    target=run_loop,
                    name="whatsapp-agent-loop",
                    daemon=True,
                )
                thread.start()
                started.wait()
                self._model_loop = loop
                self._model_loop_thread = thread
            return self._model_loop

    def close(self) -> None:
        with self._model_loop_guard:
            loop = self._model_loop
            thread = self._model_loop_thread
            self._model_loop = None
            self._model_loop_thread = None
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5)

    def _send_api_request(self, payload: dict[str, Any]) -> requests.Response:
        if not self.access_token or not self.phone_number_id:
            raise RuntimeError(
                "Set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID before sending messages."
            )
        response = requests.post(
            f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages",
            headers={"Authorization": f"Bearer {self.access_token}"},
            json={"messaging_product": "whatsapp", **payload},
            timeout=(5, 20),
        )
        response.raise_for_status()
        return response

    def send_text(self, to_number: str, text: str) -> None:
        for part in _split_message(text):
            self._send_api_request({
                "to": to_number,
                "type": "text",
                "text": {"body": part},
            })

    def _send_reply_buttons(
        self,
        to_number: str,
        body: str,
        buttons: list[tuple[str, str]],
    ) -> None:
        self._send_api_request({
            "to": to_number,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body},
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {"id": button_id, "title": title[:20]},
                        }
                        for button_id, title in buttons[:3]
                    ],
                },
            },
        })

    def _download_image(self, media_id: str) -> str:
        if not self.access_token:
            raise RuntimeError("WHATSAPP_ACCESS_TOKEN is not configured.")
        headers = {"Authorization": f"Bearer {self.access_token}"}
        metadata_response = requests.get(
            f"https://graph.facebook.com/{self.api_version}/{media_id}",
            headers=headers,
            timeout=(5, 20),
        )
        metadata_response.raise_for_status()
        metadata = metadata_response.json()
        image_url = metadata.get("url")
        parsed_url = urlparse(image_url or "")
        if (
            parsed_url.scheme != "https"
            or not parsed_url.hostname
            or not any(
                parsed_url.hostname == suffix[1:] or parsed_url.hostname.endswith(suffix)
                for suffix in _IMAGE_URL_HOST_SUFFIXES
            )
        ):
            raise ValueError("Meta returned an unsupported image download URL.")
        if int(metadata.get("file_size", 0)) > MAX_IMAGE_SIZE_BYTES:
            raise ImageUploadError("Each image must be 12 MB or smaller.")

        image_response = requests.get(
            image_url,
            headers=headers,
            timeout=(5, 60),
            stream=True,
        )
        image_response.raise_for_status()
        content_length = image_response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_IMAGE_SIZE_BYTES:
            raise ImageUploadError("Each image must be 12 MB or smaller.")
        image_bytes = bytearray()
        for chunk in image_response.iter_content(chunk_size=64 * 1024):
            image_bytes.extend(chunk)
            if len(image_bytes) > MAX_IMAGE_SIZE_BYTES:
                raise ImageUploadError("Each image must be 12 MB or smaller.")
        return prepare_image_bytes_data_url(bytes(image_bytes))

    def _run_model(self, *, history: list[dict[str, Any]], agent) -> str:
        result = asyncio.run_coroutine_threadsafe(
            self._get_model_router().run(input=history, starting_agent=agent),
            self._get_model_loop(),
        )
        result = result.result()
        if not isinstance(result.final_output, str) or not result.final_output.strip():
            raise RuntimeError("The AI agent returned an empty response.")
        return result.final_output.strip()

    async def _satellite_analysis(
        self,
        *,
        latitude: float | None = None,
        longitude: float | None = None,
        place: str | None = None,
        radius_m: float | None = None,
        area_hectares: float | None = None,
        area_acres: float | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any] | bool:
        if not self._earth_engine_initialized:
            if not initialize_earth_engine():
                raise RuntimeError("Earth Engine could not be initialized for satellite analysis.")
            self._earth_engine_initialized = True
        coordinates = (
            [(latitude, longitude)]
            if latitude is not None and longitude is not None
            else None
        )
        date_range = (start_date, end_date) if start_date and end_date else None
        result = await asyncio.to_thread(
            FarmBotAnalyzer.get_analysis_data,
            coords=coordinates,
            date_range=date_range,
            analysis_type="ndvi_ndmi",
            place=place,
            radius_m=radius_m,
            area_hectares=area_hectares,
            area_acres=area_acres,
        )
        point = result["point_0"]
        return {
            "location": {
                "latitude": point["coordinates"][0],
                "longitude": point["coordinates"][1],
                "place": point.get("place"),
            },
            "analysis_area": point["area"],
            "analysis_period": point["analysis_period"],
            "average_ndvi": point["ndvi"],
            "average_ndmi": point["ndmi"],
            "ndvi_crop_health_class": point["crop_health"],
            "acquisition_dates": [
                {
                    "date": period["selected_date"],
                    "scene_count": period["image_count"],
                    "ndvi": period["ndvi"],
                    "ndmi": period["ndmi"],
                }
                for period in point["periods"]
            ],
        }

    def _create_router(self, language: str):
        return create_image_intent_router_agent(
            language,
            farmbot_agent=create_farmbot_agent(
                language,
                satellite_analysis=self._satellite_analysis,
            ),
        )

    def _append_turn(
        self,
        user: dict[str, Any],
        question: str,
        response: str,
        *,
        had_image: bool = False,
    ) -> None:
        user["history"].extend([
            {
                "role": "user",
                "content": (
                    f"[Image attached; available for this turn only] {question}"
                    if had_image
                    else question
                ),
            },
            {"role": "assistant", "content": response},
        ])
        user["history"] = user["history"][-_MAX_HISTORY_MESSAGES:]

    def _send_intent_options(self, phone_number: str, language: str) -> None:
        labels = {
            "english": (
                "Choose the image assessment:",
                ("Check disease", "Planting advice", "Check both"),
            ),
            "hinglish": (
                "Tasveer ka kya check karna hai?",
                ("Disease check", "Planting advice", "Dono check"),
            ),
            "urdu": (
                "تصویر کا کون سا جائزہ چاہیے؟",
                ("بیماری چیک", "کاشت کا مشورہ", "دونوں"),
            ),
        }
        body, titles = labels.get(language, labels["english"])
        self._send_reply_buttons(
            phone_number,
            body,
            list(zip(("intent_disease", "intent_planting", "intent_both"), titles)),
        )

    def _handle_turn(
        self,
        phone_number: str,
        question: str,
        language: str,
        *,
        image_data_url: str | None = None,
        explicit_intent: str | None = None,
    ) -> None:
        user = self.store.get_user(phone_number)
        user["language"] = language
        user["pending"] = None
        user_content: Any = question
        if image_data_url:
            user_content = [
                {"type": "input_text", "text": question},
                {"type": "input_image", "image_url": image_data_url, "detail": "high"},
            ]
        if explicit_intent:
            if explicit_intent == "disease":
                agent = Agent(
                    name="Crop Disease and Symptom Advisor",
                    instructions=crop_analysis_instructions(language),
                )
            elif explicit_intent == "planting":
                agent = create_planting_suitability_agent(language)
                agent.instructions = (
                    f"{agent.instructions}\n\n"
                    f"{planting_suitability_image_instructions(language)}"
                )
            elif explicit_intent == "both":
                agent = create_combined_field_image_agent(language)
            else:
                raise ValueError("Unknown image assessment selection.")
        else:
            agent = self._create_router(language)
        input_history = user["history"] + [{"role": "user", "content": user_content}]
        response = self._run_model(history=input_history, agent=agent)
        clarification = extract_intent_clarification(response)
        if clarification is not None and image_data_url:
            user["pending"] = {
                "kind": "image_intent",
                "question": question,
                "image_data_url": image_data_url,
            }
            user["history"].append({
                "role": "user",
                "content": f"[Image attached; available for this turn only] {question}",
            })
            self.store.save_user(
                phone_number,
                language=language,
                history=user["history"],
                pending=user["pending"],
            )
            self._send_intent_options(phone_number, language)
            return
        self._append_turn(
            user,
            question,
            response,
            had_image=image_data_url is not None,
        )
        self.store.save_user(
            phone_number,
            language=language,
            history=user["history"],
            pending=None,
        )
        self.send_text(phone_number, response)

    def _handle_text(self, phone_number: str, text: str) -> None:
        user = self.store.get_user(phone_number)
        language = detect_language(text, user["language"])
        user["language"] = language
        if text.casefold().strip() in {
            "hi", "hello", "hey", "salam", "assalam o alaikum",
        }:
            response = "Hi there! What would you like help with today?"
            user["history"].extend([
                {"role": "user", "content": text},
                {"role": "assistant", "content": response},
            ])
            self.store.save_user(
                phone_number,
                language=language,
                history=user["history"],
                pending=None,
            )
            self.send_text(phone_number, response)
            return
        self._handle_turn(phone_number, text, language)

    def _handle_image(self, phone_number: str, message: dict[str, Any]) -> None:
        user = self.store.get_user(phone_number)
        caption = message.get("image", {}).get("caption", "").strip()
        language = detect_language(caption, user["language"])
        question = caption or (
            "Please assess this agricultural image and give the most relevant advice."
        )
        data_url = self._download_image(message["image"]["id"])
        self._handle_turn(
            phone_number,
            question,
            language,
            image_data_url=data_url,
        )

    def _send_location_options(
        self,
        phone_number: str,
        latitude: float,
        longitude: float,
        language: str,
    ) -> None:
        user = self.store.get_user(phone_number)
        user["language"] = language
        user["pending"] = {
            "kind": "location",
            "latitude": latitude,
            "longitude": longitude,
        }
        self.store.save_user(
            phone_number,
            language=language,
            history=user["history"],
            pending=user["pending"],
        )
        labels = {
            "english": (
                "What should I check at this location?",
                ("Analyze field", "Check weather", "Both"),
            ),
            "hinglish": (
                "Is jagah par kya check karna hai?",
                ("Field analysis", "Weather", "Dono"),
            ),
            "urdu": (
                "اس مقام پر کیا چیک کرنا ہے؟",
                ("کھیت کا تجزیہ", "موسم", "دونوں"),
            ),
        }
        body, titles = labels.get(language, labels["english"])
        self._send_reply_buttons(
            phone_number,
            body,
            list(zip(("location_analyze", "location_weather", "location_both"), titles)),
        )

    def _handle_location(self, phone_number: str, location: dict[str, Any]) -> None:
        try:
            latitude = float(location["latitude"])
            longitude = float(location["longitude"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("The shared location did not include valid coordinates.") from exc
        if (
            not math.isfinite(latitude)
            or not math.isfinite(longitude)
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
        ):
            raise ValueError("The shared location coordinates are outside valid bounds.")
        user = self.store.get_user(phone_number)
        self._send_location_options(phone_number, latitude, longitude, user["language"])

    def _handle_interactive(self, phone_number: str, interactive: dict[str, Any]) -> None:
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        reply_id = reply.get("id", "")
        user = self.store.get_user(phone_number)
        language = user["language"]
        pending = user.get("pending")
        if reply_id.startswith("intent_"):
            if not pending or pending.get("kind") != "image_intent":
                self.send_text(
                    phone_number,
                    _localized(
                        language,
                        "Please send the image again so I can assess it.",
                        "Tasveer dobara bhejein taa-ke main uska jaiza le sakoon.",
                        "جائزے کے لیے تصویر دوبارہ بھیجیں۔",
                    ),
                )
                return
            intent = reply_id.removeprefix("intent_")
            self._handle_turn(
                phone_number,
                pending["question"],
                language,
                image_data_url=pending["image_data_url"],
                explicit_intent=intent,
            )
            return
        if reply_id.startswith("location_"):
            if not pending or pending.get("kind") != "location":
                self.send_text(
                    phone_number,
                    _localized(
                        language,
                        "Please share your location again.",
                        "Apni location dobara share karein.",
                        "براہِ کرم اپنا مقام دوبارہ بھیجیں۔",
                    ),
                )
                return
            action = reply_id.removeprefix("location_")
            latitude = pending["latitude"]
            longitude = pending["longitude"]
            if action not in {"analyze", "weather", "both"}:
                raise ValueError("Unknown location action.")
            request_text = {
                "analyze": "Analyze Sentinel-2 NDVI and NDMI at the shared location.",
                "weather": "Check the current weather at the shared location.",
                "both": (
                    "Analyze Sentinel-2 NDVI and NDMI and check the current weather "
                    "at the shared location."
                ),
            }[action]
            self._handle_turn(
                phone_number,
                (
                    f"{request_text} Coordinates: latitude {latitude}, "
                    f"longitude {longitude}."
                ),
                language,
            )
            return
        self.send_text(
            phone_number,
            _localized(
                language,
                "I couldn't identify that option. Please send your request again.",
                "Yeh option samajh nahi aaya. Apni request dobara bhejein.",
                "یہ اختیار سمجھ نہیں آیا۔ اپنی درخواست دوبارہ بھیجیں۔",
            ),
        )

    def process_message(self, message: dict[str, Any]) -> None:
        phone_number = message.get("from")
        if not phone_number:
            raise ValueError("Incoming WhatsApp message has no sender number.")
        with self._phone_locks_guard:
            phone_lock = self._phone_locks.setdefault(phone_number, threading.Lock())
        with phone_lock:
            self._process_message(phone_number, message)

    def _process_message(self, phone_number: str, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        if message_type == "text":
            self._handle_text(phone_number, message.get("text", {}).get("body", "").strip())
        elif message_type == "image":
            self._handle_image(phone_number, message)
        elif message_type == "location":
            self._handle_location(phone_number, message.get("location", {}))
        elif message_type == "interactive":
            self._handle_interactive(phone_number, message.get("interactive", {}))
        else:
            language = self.store.get_user(phone_number)["language"]
            self.send_text(
                phone_number,
                _localized(
                    language,
                    "I can help with text, crop photos, and shared locations.",
                    "Main text, fasal ki tasveer aur shared location ke saath madad kar sakta hoon.",
                    "میں متن، فصل کی تصاویر اور بھیجے گئے مقام کے ساتھ مدد کر سکتا ہوں۔",
                ),
            )


def _process_webhook_message(
    adapter: WhatsAppBot,
    store: WhatsAppStore,
    message: dict[str, Any],
) -> None:
    message_id = message.get("id")
    try:
        adapter.process_message(message)
        if message_id:
            store.set_webhook_message_status(message_id, "processed")
    except Exception as exc:
        logger.exception("WhatsApp message processing failed")
        if message_id:
            store.set_webhook_message_status(message_id, "failed")
        phone_number = message.get("from")
        if phone_number:
            try:
                language = store.get_user(phone_number)["language"]
                if isinstance(exc, ImageUploadError):
                    response = _localized(
                        language,
                        f"I couldn't process that image: {exc}",
                        f"Yeh tasveer process nahi ho saki: {exc}",
                        f"یہ تصویر استعمال نہیں ہو سکی: {exc}",
                    )
                else:
                    response = _localized(
                        language,
                        "Sorry, I couldn't process that request. Please try again.",
                        "Maazrat, request process nahi ho saki. Dobara koshish karein.",
                        "معذرت، درخواست مکمل نہیں ہو سکی۔ دوبارہ کوشش کریں۔",
                    )
                adapter.send_text(
                    phone_number,
                    response,
                )
            except Exception:
                logger.exception("Could not send WhatsApp processing error")


def create_app(
    *,
    verify_token: str | None = None,
    app_secret: str | None = None,
    access_token: str | None = None,
    phone_number_id: str | None = None,
    business_account_id: str | None = None,
    api_version: str | None = None,
    database_path: str | Path | None = None,
    adapter: WhatsAppBot | None = None,
    executor: ThreadPoolExecutor | None = None,
) -> Flask:
    verify_token = verify_token if verify_token is not None else os.getenv("VERIFY_TOKEN", "").strip()
    app_secret = app_secret if app_secret is not None else os.getenv("META_APP_SECRET", "").strip()
    access_token = (
        access_token
        if access_token is not None
        else (
            os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
            or os.getenv("ACCESS_TOKEN", "").strip()
        )
    )
    phone_number_id = (
        phone_number_id
        if phone_number_id is not None
        else (
            os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
            or os.getenv("PHONE_NUMBER_ID", "").strip()
        )
    )
    business_account_id = (
        business_account_id
        if business_account_id is not None
        else (
            os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "").strip()
            or os.getenv("BUSINESS_ACCOUNT_ID", "").strip()
        )
    )
    api_version = (
        api_version
        if api_version is not None
        else os.getenv("WHATSAPP_API_VERSION", os.getenv("API_VERSION", "v26.0")).strip()
    )
    api_version = api_version.lower() or "v26.0"
    if not api_version.startswith("v"):
        api_version = f"v{api_version}"
    if database_path is None:
        database_path = os.getenv("WHATSAPP_DATABASE_PATH", ".files/whatsapp.sqlite3")
    database_path = Path(database_path)
    if not database_path.is_absolute():
        database_path = _PROJECT_ROOT / database_path
    store = adapter.store if adapter is not None else WhatsAppStore(database_path)
    adapter = adapter or WhatsAppBot(
        store=store,
        access_token=access_token,
        phone_number_id=phone_number_id,
        api_version=api_version,
    )
    executor = executor or ThreadPoolExecutor(max_workers=4, thread_name_prefix="whatsapp")
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    app.extensions["whatsapp_adapter"] = adapter
    app.extensions["whatsapp_store"] = store
    app.extensions["whatsapp_executor"] = executor
    app.config["WHATSAPP_VERIFY_TOKEN"] = verify_token
    app.config["WHATSAPP_APP_SECRET"] = app_secret
    app.config["WHATSAPP_BUSINESS_ACCOUNT_ID"] = business_account_id
    app.config["WHATSAPP_MISSING_CONFIG"] = _missing_whatsapp_config(
        verify_token,
        app_secret,
        access_token,
        phone_number_id,
    )

    @app.get("/")
    def health() -> Response:
        missing = app.config["WHATSAPP_MISSING_CONFIG"]
        if missing:
            return Response(
                f"WhatsApp configuration is incomplete: {', '.join(missing)}",
                status=503,
                mimetype="text/plain",
            )
        return Response("FarmBot WhatsApp webhook is running.", status=200, mimetype="text/plain")

    @app.get("/webhook")
    def verify_webhook() -> Response:
        body, status = _verify_webhook(
            request.args.get("hub.mode"),
            request.args.get("hub.verify_token"),
            request.args.get("hub.challenge"),
            verify_token,
        )
        return Response(body, status=status, mimetype="text/plain")

    @app.post("/webhook")
    def receive_webhook() -> Response:
        body, status = _receive_webhook(
            request.get_data(),
            request.headers.get("X-Hub-Signature-256"),
            app_secret=app_secret,
            business_account_id=business_account_id,
            store=store,
            adapter=adapter,
            executor=executor,
        )
        return Response(body, status=status, mimetype="text/plain")

    for queued_message in store.queued_webhook_messages():
        executor.submit(
            _process_webhook_message,
            adapter,
            store,
            queued_message["message"],
        )

    return app


def create_fastapi_router(flask_app: Flask) -> APIRouter:
    """Expose the WhatsApp webhook on the same ASGI server as Chainlit."""
    router = APIRouter()
    store = flask_app.extensions["whatsapp_store"]
    adapter = flask_app.extensions["whatsapp_adapter"]
    executor = flask_app.extensions["whatsapp_executor"]
    verify_token = flask_app.config["WHATSAPP_VERIFY_TOKEN"]
    app_secret = flask_app.config["WHATSAPP_APP_SECRET"]
    business_account_id = flask_app.config["WHATSAPP_BUSINESS_ACCOUNT_ID"]

    @router.get("/healthz")
    async def health_check():
        missing = flask_app.config["WHATSAPP_MISSING_CONFIG"]
        if missing:
            return JSONResponse(
                {"status": "degraded", "missing_configuration": missing},
                status_code=503,
            )
        return {"status": "ok"}

    @router.get("/webhook")
    async def verify_webhook(request: Request):
        body, status = _verify_webhook(
            request.query_params.get("hub.mode"),
            request.query_params.get("hub.verify_token"),
            request.query_params.get("hub.challenge"),
            verify_token,
        )
        return ASGIResponse(body, status_code=status, media_type="text/plain")

    @router.post("/webhook")
    async def receive_webhook(request: Request):
        body, status = _receive_webhook(
            await request.body(),
            request.headers.get("X-Hub-Signature-256"),
            app_secret=app_secret,
            business_account_id=business_account_id,
            store=store,
            adapter=adapter,
            executor=executor,
        )
        return ASGIResponse(body, status_code=status, media_type="text/plain")

    return router


def register_fastapi_routes(asgi_app: FastAPI, flask_app: Flask) -> None:
    if getattr(asgi_app.state, "whatsapp_routes_registered", False):
        return
    routes = create_fastapi_router(flask_app).routes
    app_routes = asgi_app.router.routes
    # FastAPI can wrap included routers lazily, hiding Chainlit's catch-all
    # from a top-level path lookup. Put these explicit API routes first so
    # the UI's fallback cannot return HTML for health checks or webhooks.
    app_routes[0:0] = routes
    asgi_app.state.whatsapp_routes_registered = True


def run() -> None:
    port = int(os.getenv("PORT", "5000"))
    app = create_app()
    app.run(host="0.0.0.0", port=port)


app = create_app()


if __name__ == "__main__":
    run()

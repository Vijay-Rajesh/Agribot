import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from whatsapp import (
    WhatsAppBot,
    WhatsAppStore,
    _valid_meta_signature,
    create_app,
    create_fastapi_router,
    register_fastapi_routes,
)


class ImmediateExecutor:
    def submit(self, callback, *args):
        callback(*args)


class RecordingAdapter:
    def __init__(self, store):
        self.store = store
        self.messages = []

    def process_message(self, message):
        self.messages.append(message)


class FakeModelRouter:
    def __init__(self, final_output="FarmBot response"):
        self.final_output = final_output
        self.input = None
        self.starting_agent = None

    async def run(self, *, input, starting_agent):
        self.input = input
        self.starting_agent = starting_agent
        return SimpleNamespace(final_output=self.final_output)


class WhatsAppSignatureTests(unittest.TestCase):
    def test_validates_meta_sha256_signature(self):
        body = b'{"object":"whatsapp_business_account"}'
        signature = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()

        self.assertTrue(_valid_meta_signature(body, f"sha256={signature}", "app-secret"))
        self.assertFalse(_valid_meta_signature(body, "sha256=invalid", "app-secret"))
        self.assertFalse(_valid_meta_signature(body, None, "app-secret"))


class WhatsAppWebhookTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = WhatsAppStore(Path(self.temp_dir.name) / "whatsapp.sqlite3")
        self.adapter = RecordingAdapter(self.store)
        self.app = create_app(
            verify_token="verify-me",
            app_secret="app-secret",
            access_token="test-token",
            phone_number_id="test-phone-id",
            business_account_id="",
            adapter=self.adapter,
            executor=ImmediateExecutor(),
        )
        self.client = self.app.test_client()
        asgi_app = FastAPI()
        asgi_app.include_router(create_fastapi_router(self.app))
        self.asgi_client = TestClient(asgi_app)

    def test_meta_get_verification_returns_challenge(self):
        response = self.client.get(
            "/webhook",
            query_string={
                "hub.mode": "subscribe",
                "hub.verify_token": "verify-me",
                "hub.challenge": "challenge-123",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "challenge-123")

    def test_asgi_router_serves_health_and_meta_verification(self):
        self.assertEqual(self.asgi_client.get("/healthz").json(), {"status": "ok"})
        response = self.asgi_client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "verify-me",
                "hub.challenge": "challenge-456",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "challenge-456")

    def test_health_reports_missing_whatsapp_configuration(self):
        app = create_app(
            verify_token="verify-me",
            app_secret="",
            access_token="",
            phone_number_id="",
            business_account_id="",
            adapter=self.adapter,
            executor=ImmediateExecutor(),
        )
        asgi_app = FastAPI()
        register_fastapi_routes(asgi_app, app)
        asgi_client = TestClient(asgi_app)

        response = asgi_client.get("/healthz")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["missing_configuration"],
            [
                "META_APP_SECRET",
                "WHATSAPP_ACCESS_TOKEN",
                "WHATSAPP_PHONE_NUMBER_ID",
            ],
        )

    def test_webhook_routes_precede_chainlit_catch_all(self):
        asgi_app = FastAPI()

        @asgi_app.get("/{full_path:path}")
        async def chainlit_fallback(full_path: str):
            return {"fallback": full_path}

        register_fastapi_routes(asgi_app, self.app)

        response = TestClient(asgi_app).get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "verify-me",
                "hub.challenge": "challenge-789",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "challenge-789")

    def test_webhook_routes_precede_included_chainlit_router(self):
        asgi_app = FastAPI()
        ui_router = APIRouter()

        @ui_router.get("/{full_path:path}")
        async def chainlit_fallback(full_path: str):
            return {"fallback": full_path}

        asgi_app.include_router(ui_router)
        register_fastapi_routes(asgi_app, self.app)
        client = TestClient(asgi_app)
        response = client.get("/webhook", params={
            "hub.mode": "subscribe", "hub.verify_token": "verify-me",
            "hub.challenge": "nested-router-check",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "nested-router-check")
        self.assertEqual(client.get("/healthz").json(), {"status": "ok"})
        self.assertEqual(client.get("/other-ui-path").json(), {"fallback": "other-ui-path"})

    def test_asgi_router_validates_signature_and_processes_message(self):
        body = json.dumps({
            "object": "whatsapp_business_account",
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "id": "wamid.asgi-test",
                            "from": "15551234567",
                            "type": "text",
                            "text": {"body": "hello"},
                        }],
                    },
                }],
            }],
        }).encode()
        signature = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()

        response = self.asgi_client.post(
            "/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": f"sha256={signature}",
                "Content-Type": "application/json",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "EVENT_RECEIVED")
        self.assertEqual(len(self.adapter.messages), 1)

    def test_post_requires_valid_signature_and_deduplicates_messages(self):
        body = json.dumps({
            "object": "whatsapp_business_account",
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "id": "wamid.test-1",
                            "from": "15551234567",
                            "type": "text",
                            "text": {"body": "hello"},
                        }],
                    },
                }],
            }],
        }).encode()
        signature = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
        headers = {"X-Hub-Signature-256": f"sha256={signature}"}

        self.assertEqual(
            self.client.post(
                "/webhook",
                data=body,
                content_type="application/json",
            ).status_code,
            403,
        )
        first = self.client.post(
            "/webhook",
            data=body,
            headers=headers,
            content_type="application/json",
        )
        second = self.client.post(
            "/webhook",
            data=body,
            headers=headers,
            content_type="application/json",
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(self.adapter.messages), 1)
        with self.store._connect() as connection:
            status = connection.execute(
                "SELECT status FROM whatsapp_webhook_events WHERE message_id = ?",
                ("wamid.test-1",),
            ).fetchone()["status"]
        self.assertEqual(status, "processed")

    def test_ignores_whatsapp_delivery_status_events(self):
        body = json.dumps({
            "object": "whatsapp_business_account",
            "entry": [{
                "changes": [{
                    "value": {"statuses": [{"id": "wamid.sent", "status": "delivered"}]},
                }],
            }],
        }).encode()
        signature = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()

        response = self.client.post(
            "/webhook",
            data=body,
            headers={"X-Hub-Signature-256": f"sha256={signature}"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.adapter.messages, [])

    def test_ignores_events_for_another_business_account(self):
        app = create_app(
            app_secret="app-secret",
            business_account_id="expected-business-id",
            adapter=self.adapter,
            executor=ImmediateExecutor(),
        )
        body = json.dumps({
            "entry": [{
                "id": "unexpected-business-id",
                "changes": [{
                    "value": {
                        "messages": [{
                            "id": "wamid.wrong-business",
                            "from": "15551234567",
                            "type": "text",
                            "text": {"body": "hello"},
                        }],
                    },
                }],
            }],
        }).encode()
        signature = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()

        response = app.test_client().post(
            "/webhook",
            data=body,
            headers={"X-Hub-Signature-256": f"sha256={signature}"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.adapter.messages, [])

    def test_requeues_received_messages_after_webhook_restart(self):
        message = {
            "id": "wamid.recovery",
            "from": "15551234567",
            "type": "text",
            "text": {"body": "hello"},
        }
        self.assertTrue(self.store.claim_webhook_message("wamid.recovery", message))
        self.assertFalse(self.store.claim_webhook_message("wamid.recovery", message))
        recovered_adapter = RecordingAdapter(self.store)

        create_app(
            app_secret="app-secret",
            adapter=recovered_adapter,
            executor=ImmediateExecutor(),
        )

        self.assertEqual(recovered_adapter.messages, [message])
        self.assertEqual(self.store.queued_webhook_messages(), [])


class WhatsAppBotTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = WhatsAppStore(Path(self.temp_dir.name) / "whatsapp.sqlite3")

    def test_openai_is_the_primary_whatsapp_provider(self):
        openai_config = object()
        with (
            patch("whatsapp.setup_openai", return_value=openai_config),
            patch("whatsapp.setup_groq", return_value=object()),
            patch("whatsapp.setup_gemini", return_value=object()),
        ):
            bot = WhatsAppBot(
                store=self.store,
                access_token="test-token",
                phone_number_id="phone-id",
                api_version="v26.0",
            )
            router = bot._get_model_router()

        self.assertIs(router._primary_config()[0], openai_config)

    def test_outbound_api_requests_use_the_configured_bearer_token(self):
        bot = WhatsAppBot(
            store=self.store,
            access_token="test-token",
            phone_number_id="phone-id",
            api_version="v26.0",
        )
        response = Mock()
        with patch("whatsapp.requests.post", return_value=response) as post:
            bot._send_api_request({"to": "15551234567", "type": "text"})

        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            "Bearer test-token",
        )
        response.raise_for_status.assert_called_once_with()

    def test_legacy_whatsapp_environment_names_are_supported(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ",
            {
                "ACCESS_TOKEN": "legacy-token",
                "PHONE_NUMBER_ID": "legacy-phone-id",
                "BUSINESS_ACCOUNT_ID": "legacy-business-id",
            },
            clear=True,
        ):
            app = create_app(database_path=Path(temp_dir) / "legacy.sqlite3")

        adapter = app.extensions["whatsapp_adapter"]
        self.assertEqual(adapter.access_token, "legacy-token")
        self.assertEqual(adapter.phone_number_id, "legacy-phone-id")
        self.assertEqual(
            app.config["WHATSAPP_BUSINESS_ACCOUNT_ID"],
            "legacy-business-id",
        )
        app.extensions["whatsapp_executor"].shutdown(wait=False)

    def test_greeting_has_short_reply_without_calling_agent(self):
        model_router = Mock()
        bot = WhatsAppBot(
            store=self.store,
            access_token="test-token",
            phone_number_id="phone-id",
            api_version="v26.0",
            model_router=model_router,
        )
        with patch.object(bot, "send_text") as send_text:
            bot.process_message({
                "from": "15551234567",
                "type": "text",
                "text": {"body": "hi"},
            })

        send_text.assert_called_once_with(
            "15551234567",
            "Hi there! What would you like help with today?",
        )
        model_router.run.assert_not_called()

    def test_image_is_sent_to_multimodal_intent_router(self):
        model_router = FakeModelRouter()
        bot = WhatsAppBot(
            store=self.store,
            access_token="test-token",
            phone_number_id="phone-id",
            api_version="v26.0",
            model_router=model_router,
        )
        self.addCleanup(bot.close)
        with (
            patch.object(bot, "_download_image", return_value="data:image/jpeg;base64,abc"),
            patch.object(bot, "send_text") as send_text,
        ):
            bot.process_message({
                "from": "15551234567",
                "type": "image",
                "image": {"id": "media-id"},
            })

        self.assertEqual(model_router.input[0]["role"], "user")
        content = model_router.input[0]["content"]
        self.assertEqual(content[1]["type"], "input_image")
        self.assertEqual(content[1]["image_url"], "data:image/jpeg;base64,abc")
        self.assertEqual(model_router.starting_agent.name, "Field Photo Intent Router")
        send_text.assert_called_once_with("15551234567", "FarmBot response")

    def test_shared_location_offers_weather_and_satellite_options(self):
        bot = WhatsAppBot(
            store=self.store,
            access_token="test-token",
            phone_number_id="phone-id",
            api_version="v26.0",
        )
        with patch.object(bot, "_send_api_request") as send_request:
            bot.process_message({
                "from": "15551234567",
                "type": "location",
                "location": {"latitude": 31.5, "longitude": 74.3},
            })

        pending = self.store.get_user("15551234567")["pending"]
        self.assertEqual(pending["kind"], "location")
        self.assertEqual(pending["latitude"], 31.5)
        payload = send_request.call_args.args[0]
        self.assertEqual(payload["interactive"]["type"], "button")
        self.assertEqual(
            [button["reply"]["id"] for button in payload["interactive"]["action"]["buttons"]],
            ["location_analyze", "location_weather", "location_both"],
        )

    def test_location_action_passes_shared_coordinates_into_agent_context(self):
        model_router = FakeModelRouter()
        bot = WhatsAppBot(
            store=self.store,
            access_token="test-token",
            phone_number_id="phone-id",
            api_version="v26.0",
            model_router=model_router,
        )
        self.addCleanup(bot.close)
        self.store.save_user(
            "15551234567",
            language="english",
            history=[],
            pending={
                "kind": "location",
                "latitude": 31.5,
                "longitude": 74.3,
            },
        )
        with patch.object(bot, "send_text"):
            bot.process_message({
                "from": "15551234567",
                "type": "interactive",
                "interactive": {
                    "button_reply": {"id": "location_analyze"},
                },
            })

        self.assertIn("latitude 31.5, longitude 74.3", model_router.input[0]["content"])
        self.assertIsNone(self.store.get_user("15551234567")["pending"])

    def test_image_intent_selection_keeps_user_question_separate_from_instructions(self):
        model_router = FakeModelRouter()
        bot = WhatsAppBot(
            store=self.store,
            access_token="test-token",
            phone_number_id="phone-id",
            api_version="v26.0",
            model_router=model_router,
        )
        self.addCleanup(bot.close)
        self.store.save_user(
            "15551234567",
            language="english",
            history=[],
            pending={
                "kind": "image_intent",
                "question": "Can I plant this crop here?",
                "image_data_url": "data:image/jpeg;base64,abc",
            },
        )
        with patch.object(bot, "send_text"):
            bot.process_message({
                "from": "15551234567",
                "type": "interactive",
                "interactive": {
                    "button_reply": {"id": "intent_planting"},
                },
            })

        self.assertEqual(
            model_router.input[0]["content"][0]["text"],
            "Can I plant this crop here?",
        )
        self.assertIn(
            "Do not ask for location or planting month/season",
            model_router.starting_agent.instructions,
        )

    def test_rejects_out_of_range_location(self):
        bot = WhatsAppBot(
            store=self.store,
            access_token="test-token",
            phone_number_id="phone-id",
            api_version="v26.0",
        )

        with self.assertRaises(ValueError):
            bot.process_message({
                "from": "15551234567",
                "type": "location",
                "location": {"latitude": 95, "longitude": 74.3},
            })


if __name__ == "__main__":
    unittest.main()

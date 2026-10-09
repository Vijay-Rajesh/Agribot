import base64
import io
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from agents.models.chatcmpl_converter import Converter

from config import setup_gemini, setup_groq
from services.image_analysis import (
    ImageUploadError,
    crop_analysis_instructions,
    prepare_image_data_urls,
)


class PrepareImageDataUrlsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.image_path = Path(self.temp_dir.name) / "crop.png"
        Image.new("RGB", (2200, 1200), color="green").save(self.image_path)

    def test_prepares_resized_jpeg_data_url(self):
        element = SimpleNamespace(type="image", path=str(self.image_path))

        [data_url] = prepare_image_data_urls([element])

        self.assertTrue(data_url.startswith("data:image/jpeg;base64,"))
        image_bytes = base64.b64decode(data_url.split(",", 1)[1])
        with Image.open(io.BytesIO(image_bytes)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (1600, 873))

    def test_rejects_non_image_upload(self):
        element = SimpleNamespace(type="file", path=str(self.image_path))

        with self.assertRaises(ImageUploadError):
            prepare_image_data_urls([element])

    def test_rejects_unsupported_image_format(self):
        gif_path = Path(self.temp_dir.name) / "crop.gif"
        Image.new("RGB", (10, 10)).save(gif_path)
        element = SimpleNamespace(type="image", path=str(gif_path))

        with self.assertRaises(ImageUploadError):
            prepare_image_data_urls([element])

    def test_rejects_more_than_three_images(self):
        element = SimpleNamespace(type="image", path=str(self.image_path))

        with self.assertRaises(ImageUploadError):
            prepare_image_data_urls([element] * 4)

    def test_rejects_corrupt_image(self):
        corrupt_path = Path(self.temp_dir.name) / "broken.png"
        corrupt_path.write_bytes(b"not an image")
        element = SimpleNamespace(type="image", path=str(corrupt_path))

        with self.assertRaises(ImageUploadError):
            prepare_image_data_urls([element])

    def test_agent_sdk_converts_image_input_to_chat_completion_format(self):
        element = SimpleNamespace(type="image", path=str(self.image_path))
        [data_url] = prepare_image_data_urls([element])

        converted = Converter.extract_all_content(
            [
                {"type": "input_text", "text": "Assess this crop image."},
                {"type": "input_image", "image_url": data_url, "detail": "high"},
            ]
        )

        self.assertEqual(converted[1]["type"], "image_url")
        self.assertEqual(converted[1]["image_url"]["url"], data_url)


class CropAnalysisInstructionsTests(unittest.TestCase):
    def test_hinglish_starts_with_disease_and_includes_actionable_safe_steps(self):
        prompt = crop_analysis_instructions("hinglish")

        self.assertIn("Pehli line '**Sabse mumkin disease/condition:", prompt)
        self.assertLess(
            prompt.index("Pehli line '**Sabse mumkin disease/condition:"),
            prompt.index("sirf photo mein waqai nazar aane wali cheezein"),
        )
        self.assertIn("'**Abhi kya karein / ilaaj:**'", prompt)
        self.assertIn("affected anaj na khane aur na janwaron ko khilane", prompt)
        self.assertIn("fungicide pehle se moldy dane theek kar dega", prompt)
        self.assertIn("pesticide/product/dose prescribe na karein", prompt)

    def test_all_supported_languages_start_with_a_likely_condition(self):
        for language, opening in (
            ("english", "Start with '**Most likely disease/condition:"),
            ("hinglish", "Pehli line '**Sabse mumkin disease/condition:"),
            ("urdu", "پہلی سطر '**زیادہ ممکنہ بیماری/مسئلہ:"),
        ):
            with self.subTest(language=language):
                self.assertIn(opening, crop_analysis_instructions(language))


class GeminiRetryConfigTests(unittest.TestCase):
    def test_run_config_retries_transient_model_failures(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
            run_config = setup_gemini()

        retry = run_config.model_settings.retry
        self.assertEqual(retry.max_retries, 3)
        self.assertEqual(retry.backoff.initial_delay, 1)
        self.assertEqual(retry.backoff.max_delay, 8)
        self.assertEqual(retry.backoff.multiplier, 2)
        self.assertTrue(retry.backoff.jitter)

    def test_groq_uses_supported_multimodal_model_and_skips_429_retries(self):
        with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
            run_config = setup_groq()

        self.assertEqual(run_config.model.model, "qwen/qwen3.8-27b")
        retry = run_config.model_settings.retry
        self.assertEqual(retry.max_retries, 2)
        self.assertIsNotNone(retry.policy)
        self.assertFalse(
            retry.policy(
                SimpleNamespace(
                    normalized=SimpleNamespace(
                        status_code=429,
                        is_network_error=False,
                        is_timeout=False,
                    )
                )
            ).retry
        )
        self.assertTrue(
            retry.policy(
                SimpleNamespace(
                    normalized=SimpleNamespace(
                        status_code=503,
                        is_network_error=False,
                        is_timeout=False,
                    )
                )
            ).retry
        )


if __name__ == "__main__":
    unittest.main()

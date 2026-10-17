import unittest
from unittest.mock import Mock, patch

from services.weather import WeatherAPI


class WeatherApiTests(unittest.TestCase):
    @patch.dict("os.environ", {"WEATHER_API_KEY": "test-weather-key"})
    @patch("services.weather.requests.get")
    def test_returns_normalized_current_weather(self, get):
        get.return_value = Mock(
            status_code=200,
            json=lambda: {
                "current": {
                    "temp_c": 24.5,
                    "humidity": 63,
                    "condition": {"text": "Partly cloudy"},
                    "wind_kph": 12.2,
                    "precip_mm": 0.1,
                },
                "location": {"localtime": "2026-10-17 15:00"},
            },
        )

        result = WeatherAPI.get_weather(31.52, 74.35)

        self.assertEqual(result, {
            "temperature": 24.5,
            "humidity": 63,
            "conditions": "Partly cloudy",
            "wind_speed": 12.2,
            "rain": 0.1,
            "timestamp": "2026-10-17 15:00",
        })
        get.assert_called_once()
        self.assertEqual(get.call_args.kwargs["timeout"], 15)
        self.assertEqual(get.call_args.kwargs["params"], {
            "key": "test-weather-key",
            "q": "31.52,74.35",
        })

    @patch.dict("os.environ", {"WEATHER_API_KEY": "test-weather-key"})
    @patch("services.weather.requests.get")
    def test_returns_provider_error_message(self, get):
        get.return_value = Mock(
            status_code=403,
            json=lambda: {"error": {"message": "API key is invalid."}},
        )

        result = WeatherAPI.get_weather(31.52, 74.35)

        self.assertEqual(result, {"error": "Weather API error: API key is invalid."})

    @patch.dict("os.environ", {}, clear=True)
    def test_reports_missing_api_key(self):
        self.assertEqual(
            WeatherAPI.get_weather(31.52, 74.35),
            {"error": "Weather API key not configured"},
        )


if __name__ == "__main__":
    unittest.main()

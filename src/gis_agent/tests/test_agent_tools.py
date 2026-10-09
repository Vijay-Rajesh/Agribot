import json
import unittest
from unittest.mock import AsyncMock, patch

from Agents.farmbot_agent import create_farmbot_agent
from agents.tool import ToolContext
from services.analysis import FarmBotAnalyzer
from services.weather import WeatherAPI


class FarmBotNaturalLanguageToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tools = {
            tool.name: tool
            for tool in create_farmbot_agent("urdu").tools
        }

    async def invoke(self, name, arguments):
        serialized_arguments = json.dumps(arguments)
        context = ToolContext(
            context=None,
            tool_name=name,
            tool_call_id="test-call",
            tool_arguments=serialized_arguments,
        )
        return json.loads(
            await self.tools[name].on_invoke_tool(context, serialized_arguments)
        )

    def test_agent_exposes_tools_for_all_supported_operations(self):
        self.assertEqual(
            set(self.tools),
            {
                "analyze_satellite_indices",
                "check_current_weather",
                "get_government_agricultural_schemes",
            },
        )

    async def test_weather_tool_geocodes_place_and_returns_live_weather(self):
        current_weather = {
            "temperature": 28,
            "humidity": 50,
            "conditions": "Sunny",
            "wind_speed": 10,
            "rain": 0,
            "timestamp": "2026-10-07 09:00",
        }
        with (
            patch.object(
                FarmBotAnalyzer,
                "_geocode_place",
                return_value=(30.2, 71.5, "Multan"),
            ) as geocode,
            patch.object(
                WeatherAPI,
                "get_weather",
                return_value=current_weather,
            ) as get_weather,
        ):
            result = await self.invoke(
                "check_current_weather",
                {"place": "Multan"},
            )

        self.assertEqual(result["location"], "Multan")
        self.assertEqual(result["weather"], current_weather)
        geocode.assert_called_once_with("Multan")
        get_weather.assert_called_once_with(30.2, 71.5)

    async def test_weather_tool_requests_location_instead_of_guessing(self):
        result = await self.invoke("check_current_weather", {})

        self.assertTrue(result["needs_location"])
        self.assertIn("Share live location", result["message"])

    async def test_satellite_tool_requests_location_instead_of_running_without_one(self):
        result = await self.invoke("analyze_satellite_indices", {})

        self.assertTrue(result["needs_location"])

    async def test_satellite_tool_uses_full_chat_analysis_workflow(self):
        satellite_analysis = AsyncMock(return_value=True)
        tools = {
            tool.name: tool
            for tool in create_farmbot_agent(
                "english",
                satellite_analysis=satellite_analysis,
            ).tools
        }
        arguments = {
            "place": "Lahore",
            "area_acres": 2,
            "start_date": "2026-01-01",
            "end_date": "2026-02-01",
        }
        serialized_arguments = json.dumps(arguments)
        context = ToolContext(
            context=None,
            tool_name="analyze_satellite_indices",
            tool_call_id="test-call",
            tool_arguments=serialized_arguments,
        )

        result = json.loads(
            await tools["analyze_satellite_indices"].on_invoke_tool(
                context,
                serialized_arguments,
            )
        )

        self.assertTrue(result["completed"])
        satellite_analysis.assert_awaited_once_with(
            latitude=None,
            longitude=None,
            place="Lahore",
            radius_m=None,
            area_hectares=None,
            area_acres=2,
            start_date="2026-01-01",
            end_date="2026-02-01",
        )

    async def test_scheme_tool_returns_localized_reference_data(self):
        result = await self.invoke(
            "get_government_agricultural_schemes",
            {"scheme_name": "Tubewell Subsidy"},
        )

        self.assertEqual(result["name"], "Tubewell Subsidy")
        self.assertIn("ٹیوب ویل سبسڈی", result["description"])


if __name__ == "__main__":
    unittest.main()

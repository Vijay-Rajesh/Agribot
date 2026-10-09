# services/weather.py
import os
import requests
from datetime import datetime as dt
from typing import Dict
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

class WeatherAPI:
    """Class to handle weather data using WeatherAPI.com"""
    
    @staticmethod
    def get_weather(lat: float, lon: float) -> Dict:
        api_key = os.getenv("WEATHER_API_KEY")
        if not api_key:
            return {"error": "Weather API key not configured"}
        
        try:
            url = f"https://api.weatherapi.com/v1/current.json?key={api_key}&q={lat},{lon}"
            response = requests.get(url, timeout=15)
            data = response.json()

            if response.status_code != 200:
                return {"error": f"Weather API error: {data.get('error', {}).get('message', 'Unknown error')}"}
            
            current = data["current"]
            location = data["location"]

            weather_data = {
                "temperature": current["temp_c"],
                "humidity": current["humidity"],
                "conditions": current["condition"]["text"],
                "wind_speed": current["wind_kph"],
                "rain": current.get("precip_mm", 0),
                "timestamp": dt.strptime(location["localtime"], '%Y-%m-%d %H:%M').strftime('%Y-%m-%d %H:%M')
            }
            
            return weather_data
            
        except Exception as e:
            return {"error": f"Weather data fetch failed: {str(e)}"}
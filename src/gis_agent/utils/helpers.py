# utils/helpers.py
import random
from typing import Dict, Optional

def get_random_farming_phrase() -> str:
    """Return a random farming phrase with Islamic touch"""
    PHRASES = [
        "Allah barkat de aap ki fasal ko! 🌱",
        "Mashallah, aap ke khet ki sehat achi hai! 💚",
        "Thora aur pani aur mehnat, phir dekho kamal! 💧",
        "Fasal ki hifazat ke liye dua karein, Allah madad karega 🤲",
        
    ]
    return random.choice(PHRASES)

def validate_coordinates(lat: float, lon: float) -> bool:
    """Check if coordinates are within Pakistan"""
    return (23.5 <= lat <= 37.0) and (60.0 <= lon <= 77.0)

def format_weather_data(weather_data: Optional[Dict]) -> Dict:
    """Format raw weather data into standardized format with proper error handling"""
    if not isinstance(weather_data, dict):
        return {
            'temperature': 'N/A',
            'humidity': 'N/A',
            'wind_speed': 'N/A',
            'conditions': 'Unknown',
            'rain': 0
        }
    
    try:
        if 'error' in weather_data:
            raise ValueError(weather_data['error'])
            
        return {
            'temperature': weather_data.get('temperature', 'N/A'),
            'humidity': weather_data.get('humidity', 'N/A'),
            'wind_speed': weather_data.get('wind_speed', 'N/A'),
            'conditions': weather_data.get('conditions', 'Unknown'),
            'rain': weather_data.get('rain', 0)
        }
    except Exception as e:
        return {
            'temperature': 'N/A',
            'humidity': 'N/A',
            'wind_speed': 'N/A',
            'conditions': f'Error: {str(e)}',
            'rain': 0
        }
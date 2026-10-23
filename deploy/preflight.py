"""Check configured external services without printing credentials or sending messages."""
import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / '.env')


def check(name, url, *, headers=None, params=None):
    try:
        response = requests.get(url, headers=headers, params=params, timeout=25)
        result = {"service": name, "http_status": response.status_code}
        if response.ok and name == "WhatsApp subscriptions":
            result["subscribed_app_count"] = len(response.json().get("data", []))
        if not response.ok:
            error = response.json().get("error", {})
            if isinstance(error, dict):
                result["error_code"] = error.get("code")
                result["error_type"] = error.get("type")
        print(json.dumps(result), flush=True)
    except Exception as exc:
        print(json.dumps({"service": name, "error_type": type(exc).__name__}), flush=True)


if __name__ == '__main__':
    token = os.getenv('WHATSAPP_ACCESS_TOKEN') or os.getenv('ACCESS_TOKEN')
    phone = os.getenv('WHATSAPP_PHONE_NUMBER_ID') or os.getenv('PHONE_NUMBER_ID')
    waba = os.getenv('WHATSAPP_BUSINESS_ACCOUNT_ID') or os.getenv('BUSINESS_ACCOUNT_ID')
    version = os.getenv('WHATSAPP_API_VERSION', 'v26.0')
    if token and phone:
        check('WhatsApp phone', f'https://graph.facebook.com/{version}/{phone}',
              headers={'Authorization': f'Bearer {token}'})
    if token and waba:
        check('WhatsApp subscriptions', f'https://graph.facebook.com/{version}/{waba}/subscribed_apps',
              headers={'Authorization': f'Bearer {token}'})
    if os.getenv('OPENAI_API_KEY'):
        check('OpenAI authentication', 'https://api.openai.com/v1/models',
              headers={'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY']})
    if os.getenv('WEATHER_API_KEY'):
        check('Weather authentication', 'https://api.weatherapi.com/v1/current.json',
              params={'key': os.environ['WEATHER_API_KEY'], 'q': 'Lahore'})
    if os.getenv('GEMINI_API_KEY'):
        model = os.getenv('GEMINI_MODEL', 'gemini-3.8-flash').removeprefix('models/')
        check('Gemini configured model',
              f'https://generativelanguage.googleapis.com/v1beta/models/{model}',
              headers={'x-goog-api-key': os.environ['GEMINI_API_KEY']})
    try:
        import ee
        ee.Initialize(project='ee-ewe111vijay')
        print(json.dumps({'service': 'Earth Engine', 'ready': ee.Number(1).getInfo() == 1}), flush=True)
    except Exception as exc:
        print(json.dumps({'service': 'Earth Engine', 'ready': False, 'error_type': type(exc).__name__}), flush=True)

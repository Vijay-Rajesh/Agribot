# Agribot

## Setup (Windows PowerShell)

Use Python 3.12 or newer:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Add at least one AI provider key to a root `.env` file before starting the app:

```dotenv
OPENAI_API_KEY=your-openai-api-key
```

Alternatively, configure `GROQ_API_KEY` or `GEMINI_API_KEY`. Keep `.env` private
and never paste API keys into chat or commit them to the repository.

Start the app from the repository root:

```powershell
chainlit run src\gis_agent\main.py -w --port 8000
```

Open [http://localhost:8000](http://localhost:8000) in a browser. The
Earth Engine analysis features also require Earth Engine authentication. In a
separate PowerShell window, authenticate using the project's virtual environment:

```powershell
.\.venv\Scripts\earthengine.exe authenticate
```

Complete the browser sign-in with an account that has access to the configured
Earth Engine project (`ee-ewe111vijay`), then restart FarmBot.

AI provider priority:

1. `OPENAI_API_KEY` is used first.
2. `GROQ_API_KEY` is used if OpenAI is not configured or returns a rate-limit response.
3. `GEMINI_API_KEY` is used if neither earlier configured provider is available.

Add provider keys to the root `.env` file. When a provider returns HTTP 429,
FarmBot tries the next configured provider and temporarily avoids the
rate-limited provider until its quota reset time.

## Planting suitability advice

Ask FarmBot directly whether a named crop, fruit, or plant is suitable for your
field, for example: “Can I grow mango here this winter?” You can attach a clear
field photo and include your location, planting season, irrigation availability,
or soil-test results. For uploaded field photos, the AI router considers both
your question and the image, then hands off to disease diagnosis, planting
suitability, or FarmBot as appropriate. If it cannot tell what you want, it asks
you to select a disease check, planting suitability, or both; your selection is
then applied to the same uploaded image. The Planting Suitability Advisor uses
the configured model (OpenAI is tried first) to give a preliminary assessment
and identify missing information. A photo alone cannot determine soil or climate
conditions, and high-investment planting decisions should be checked with a
local agricultural extension service.

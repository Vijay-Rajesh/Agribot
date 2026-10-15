# Agribot

cmd to activate env = .venv312\scripts\activate
command to Run the project =  chainlit run src\gis_agent\main.py -w --port 8000

AI provider priority:

1. `OPENAI_API_KEY` is used first.
2. `GROQ_API_KEY` is used if OpenAI is not configured or returns a rate-limit response.
3. `GEMINI_API_KEY` is used if neither earlier configured provider is available.

Add provider keys to the root `.env` file. When a provider returns HTTP 429,
FarmBot tries the next configured provider and temporarily avoids the
rate-limited provider until its quota reset time.

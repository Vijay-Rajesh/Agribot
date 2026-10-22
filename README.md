# Agribot

## Setup (Windows PowerShell)

Use Python 3.12 or newer:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Copy [`.env.example`](./.env.example) to `.env` and set the keys you use.
WhatsApp and Chainlit run in the same application by default (`APP_SERVICE=all`).
Keep `.env` private and never paste API keys into chat or commit them.

Start the app from the repository root:

```powershell
chainlit run src\gis_agent\main.py -w --port 8000
```

Open [http://localhost:8000](http://localhost:8000) for Chainlit. The same
server also exposes the WhatsApp webhook at `/webhook` and a health check at
`/healthz`; readiness returns HTTP `503` and names any missing required
WhatsApp settings. Set `PORT` to use a different port.
For local satellite-analysis testing, authenticate Earth Engine in the active
virtual environment with
`.\.venv\Scripts\earthengine.exe authenticate`, then sign in with an account
that has access to project `ee-ewe111vijay`.

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
suitability, or FarmBot as appropriate. Plant/crop photos default to a crop
health check; bare or prepared field photos default to planting suitability.
The Planting Suitability Advisor uses the configured model (OpenAI is tried
first) to give a preliminary assessment and identify missing information. A
photo alone cannot determine soil or climate conditions, and high-investment
planting decisions should be checked with a local agricultural extension
service.

## WhatsApp Cloud API

The WhatsApp webhook and Chainlit UI are served by one application process,
using the same agents, OpenAI key, and deployment environment. Configure these
Meta values in the same root `.env` file or deployment environment:

```dotenv
VERIFY_TOKEN=your-private-webhook-verification-token
META_APP_SECRET=your-meta-app-secret
WHATSAPP_ACCESS_TOKEN=your-whatsapp-cloud-api-access-token
WHATSAPP_PHONE_NUMBER_ID=your-meta-phone-number-id
WHATSAPP_API_VERSION=v26.0
WHATSAPP_DATABASE_PATH=.files/whatsapp.sqlite3
```

`META_APP_SECRET` is required for signed webhook events; the webhook returns
`503` for POST requests until it is configured. `SKIP_RAG_STARTUP` is an
obsolete setting (this project has no RAG subsystem) and can be removed.
Configure Meta's callback as the same deployment's public HTTPS URL ending
in `/webhook`, use the same `VERIFY_TOKEN`, and subscribe the WhatsApp
Business Account to the `messages` field. Meta requires HTTPS; for local
testing, expose the app's port 8000 through a secure tunnel. The webhook
verifies Meta's `X-Hub-Signature-256` using `META_APP_SECRET`, optionally
filters events by `WHATSAPP_BUSINESS_ACCOUNT_ID`, and ignores duplicate
message deliveries. `ACCESS_TOKEN`, `PHONE_NUMBER_ID`, and
`BUSINESS_ACCOUNT_ID` are accepted as legacy aliases.

The WhatsApp bot supports text chat, crop-image uploads (downloaded from Meta
and passed to the existing image-aware agent), shared location, and interactive
location choices for satellite analysis, weather, or both. Conversation history
and pending image/location choices are stored in the ignored
`.files/whatsapp.sqlite3` database; mount persistent storage there in
production. Earth Engine analysis still requires credentials for the configured
project. Replies are sent inside the user's
WhatsApp conversation window; outbound template messages are not implemented.

## Docker image and CI/CD

The Docker image defaults to `APP_SERVICE=all`, running both Chainlit and the
WhatsApp webhook on the same port and public HTTPS domain. `APP_SERVICE=chainlit`
and `APP_SERVICE=whatsapp` remain available for optional single-interface
deployments.

The GitHub Actions workflow at
[`.github/workflows/docker-publish.yml`](./.github/workflows/docker-publish.yml)
runs the unit tests and builds the image for pull requests. After tests pass,
pushes to `main` publish:

- `ghcr.io/vijay-rajesh/agribot:latest`
- `ghcr.io/vijay-rajesh/agribot:main`
- `ghcr.io/vijay-rajesh/agribot:sha-<commit>`

Pushing a version tag such as `v1.0.0` also publishes
`ghcr.io/vijay-rajesh/agribot:v1.0.0`. GitHub Actions uses the repository's
`GITHUB_TOKEN`; no registry password needs to be added as a GitHub secret.
The first successful workflow run creates the image package in GitHub
Packages. Set its visibility to **Public** if the hosting platform should pull
the image without registry credentials; otherwise configure that platform to
authenticate to GHCR.

To build and run locally:

```powershell
docker build -t ghcr.io/vijay-rajesh/agribot:latest .
docker run --rm -p 8000:8000 --env-file .env ghcr.io/vijay-rajesh/agribot:latest
```

Deploy one container/service and add all required variables to that service:
`OPENAI_API_KEY`, `VERIFY_TOKEN`, `META_APP_SECRET`,
`WHATSAPP_ACCESS_TOKEN`, and `WHATSAPP_PHONE_NUMBER_ID`. Add
`WEATHER_API_KEY` for weather responses and the optional fallback provider
keys if desired. Set Meta's callback to `https://<your-domain>/webhook` and
subscribe to `messages`. Attach persistent storage at `/app/.files` if WhatsApp
conversation history must survive restarts. Do not bake `.env` or credentials
into the Docker image.

Earth Engine is the remaining external setup: provide credentials with access
to project `ee-ewe111vijay` in the same deployment (for example, mount a
service-account credentials file and set `GOOGLE_APPLICATION_CREDENTIALS`).
The UI and webhook can deploy together, but satellite analysis will remain
unavailable until Earth Engine credentials and project access are configured.

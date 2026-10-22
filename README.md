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
suitability, or FarmBot as appropriate. Plant/crop photos default to a crop
health check; bare or prepared field photos default to planting suitability.
The Planting Suitability Advisor uses the configured model (OpenAI is tried
first) to give a preliminary assessment and identify missing information. A
photo alone cannot determine soil or climate conditions, and high-investment
planting decisions should be checked with a local agricultural extension
service.

## WhatsApp Cloud API

WhatsApp runs as a separate Flask webhook process. It does not replace or
remove Chainlit; both interfaces use the existing FarmBot agents and services
and can run at the same time.

Add these Meta WhatsApp Cloud API values to the root `.env` file. Keep the
access token and app secret private:

```dotenv
VERIFY_TOKEN=choose-a-private-webhook-verification-token
META_APP_SECRET=your-meta-app-secret
WHATSAPP_ACCESS_TOKEN=your-whatsapp-cloud-api-access-token
WHATSAPP_PHONE_NUMBER_ID=your-meta-phone-number-id
WHATSAPP_API_VERSION=v26.0
WHATSAPP_DATABASE_PATH=.files/whatsapp.sqlite3
```

Install/update the project dependencies, then start the webhook from the
repository root in a separate terminal:

```powershell
python -m pip install -e .
agribot-whatsapp
```

The local webhook listens on port `5000` by default; set `PORT` to change it.
Configure the Meta webhook callback as your publicly reachable HTTPS URL ending
in `/webhook`, use the same `VERIFY_TOKEN`, and subscribe the WhatsApp
Business Account to the `messages` field. Meta needs an HTTPS endpoint; for
local testing, expose port 5000 through a secure tunnel. The webhook verifies
Meta's `X-Hub-Signature-256` using `META_APP_SECRET` and ignores duplicate
message deliveries.

The WhatsApp bot supports text chat, crop-image uploads (downloaded from Meta
and passed to the existing image-aware agent), shared location, and interactive
location choices for satellite analysis, weather, or both. Conversation history
and pending image/location choices are stored in the ignored
`.files/whatsapp.sqlite3` database. Earth Engine analysis still requires the
project's Earth Engine authentication. Replies are sent inside the user's
WhatsApp conversation window; outbound template messages are not implemented.

Keep Chainlit running with its existing command when you also want the browser
app. For production, serve `gis_agent.whatsapp:app` behind a production WSGI
server and HTTPS reverse proxy rather than using Flask's built-in development
server.

## Docker image and CI/CD

The Docker image can run the Chainlit browser app or the WhatsApp webhook,
selected with `APP_SERVICE` (`chainlit` by default, or `whatsapp`). It runs one
service per container so Railway can give each service its own public HTTPS
domain and `PORT`.

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
docker run --rm -p 8000:8000 -e OPENAI_API_KEY=your-key ghcr.io/vijay-rajesh/agribot:latest
docker run --rm -p 5000:5000 -e APP_SERVICE=whatsapp -e VERIFY_TOKEN=your-token -e META_APP_SECRET=your-secret -e WHATSAPP_ACCESS_TOKEN=your-access-token -e WHATSAPP_PHONE_NUMBER_ID=your-phone-id ghcr.io/vijay-rajesh/agribot:latest
```

Set AI provider keys (`OPENAI_API_KEY`, `GROQ_API_KEY`, or `GEMINI_API_KEY`)
as deployment environment variables, not in the image. To run both interfaces
on Railway, create two services from the same image:

1. **Chainlit service:** `APP_SERVICE=chainlit`; expose its Railway-generated
   public domain.
2. **WhatsApp service:** `APP_SERVICE=whatsapp`; expose its own Railway
   generated public domain and add `VERIFY_TOKEN`, `META_APP_SECRET`,
   `WHATSAPP_ACCESS_TOKEN`, and `WHATSAPP_PHONE_NUMBER_ID`. Configure the Meta
   callback URL as `https://<whatsapp-domain>/webhook`, subscribe to `messages`,
   and use the same `VERIFY_TOKEN` in Meta and Railway.

Add the AI provider keys to both services if both should answer messages.
Set `WEATHER_API_KEY` on either service that needs weather responses. Attach a
Railway persistent volume at `/app/.files` to the WhatsApp service to retain
conversation history across restarts. Earth Engine analysis also needs
Earth Engine credentials and project access; configure those separately when
enabling that feature in production.

For a local run, the default `chainlit` service listens on port 8000. Start the
WhatsApp service in a second container with `APP_SERVICE=whatsapp` and map
port 5000; configure the WhatsApp webhook against a publicly reachable HTTPS
URL.

For the simplest first deployment, Railway can deploy the GHCR image and
provide a `PORT` to each service; DigitalOcean App Platform can also deploy
both container services. Configure GHCR access if the package is private, and
add each service's required environment variables there.

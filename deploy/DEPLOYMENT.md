# Agribot deployment — 24 October 2026

- AWS region: `ap-south-1` (Mumbai)
- Instance: `i-0410e02e273298254`, name `agribot`
- Ubuntu 26.04, `t3.micro`, Standard CPU credits, 20 GiB gp3 disk
- Public IPv4: `13.232.155.101`
- Live HTTPS URL: `https://13-232-155-101.sslip.io`
- Active Meta callback: `https://13-232-155-101.sslip.io/webhook`
- Meta app: `AGRIBOT` (`1402674948639985`)
- SSH user: `ubuntu`; app directory: `/opt/agribot`
- SSH private key: `deploy/private/agribot-20261024.pem` (ignored by Git)
- Private configuration: `deploy/private/app.env` and `earthengine.json`

The account was verified as AWS Free Plan with $132.15 remaining credits
before launch. Credits and the free-plan duration are finite. This deployment
does not upgrade the account or provision a load balancer, NAT gateway, or RDS.

Validation before upload: 67 unit tests passed in the locked Python 3.12
environment; local UI and JSON health endpoint returned 200; Meta verification
challenge passed; unsigned webhook POST was rejected with 403. OpenAI and
WeatherAPI authentication passed, and an Earth Engine server request succeeded.
A minimal live request with the configured primary AI model also succeeded.
The configured Gemini fallback model (`gemini-2.5-flash`) was found and its
key was accepted by the model metadata endpoint.

The EC2 app container is healthy and runs as `appuser`; both containers use
`unless-stopped` restart policies. Caddy obtained a valid Let's Encrypt
certificate. External checks confirmed HTTPS UI and JSON health return 200,
the verification challenge returns 200, a valid signed empty event returns
200, and an unsigned event returns 403. Meta's subscriptions API confirmed
the new callback is saved, active, and subscribed to `messages`.

The final app image includes writable-directory ownership for Chainlit's
translations and generated satellite reports. The main Dockerfile contains
the same fix for subsequent image builds.

The original WhatsApp token does not match the AGRIBOT
Meta app; original phone access returned Meta error 100/subcode 33 and business
phone/subscription access returned error 200 (missing permission).

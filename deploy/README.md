# EC2 deployment

Run one Ubuntu x86_64 `t3.micro` instance with a 20 GiB gp3 root disk.
Use Standard CPU credits, IMDSv2 required, and a dedicated SSH key pair.
Allow TCP 22 only from the administrator's current public IP; allow 80 and
443 publicly for HTTPS and Meta webhooks. Do not expose port 8000.

Use `cloud-init.yaml` as instance user data. It contains no secrets.
Wait for `sudo cloud-init status --wait` after connecting as `ubuntu`.
Upload the project to `/opt/agribot`, excluding local virtual environments,
`.git`, `.env`, `.files`, `.deploy-tmp` and `deploy/private`.

Transfer `.env` separately over SSH to `deploy/private/app.env` with mode 600.
Transfer Google authorized-user ADC or service-account JSON with appropriate
Earth Engine project access to `deploy/private/earthengine.json`. Make that
file readable by container uid 10001, without making it world-readable.
Never include API keys, private SSH keys, or Google credentials in user data,
Git, Docker build context, images, or public download links.

Create `deploy/.env` with `AGRIBOT_HOST=<public-ip-with-dashes>.sslip.io`.
The hostname must resolve to the EC2 instance's current public IPv4 address.
The public IP can change after an instance stop/start; update the hostname
and the Meta callback if that happens.

From `/opt/agribot`, run:

```sh
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --build
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml ps
curl --fail https://HOST/healthz
```

Caddy obtains and renews the HTTPS certificate and proxies WebSocket traffic.
Docker volumes persist WhatsApp conversations and TLS certificates.
Keep copies of those volumes before replacing or deleting the instance.

Set the Meta callback to `https://HOST/webhook`, using the existing private
VERIFY_TOKEN, and subscribe to `messages`. Confirm successful verification,
valid signed POST acceptance, rejection of invalid signatures and duplicate
delivery handling. An actual inbound WhatsApp message and reply is needed
to validate the external delivery flow.

Run `python deploy/preflight.py` locally to check API access without sending
WhatsApp messages. API authentication success does not prove model billing
quota or WhatsApp send permissions. AWS Free Plan credits are finite; do
not upgrade the account or add paid services to continue this deployment.

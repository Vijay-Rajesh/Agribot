FROM ghcr.io/astral-sh/uv:0.5.31 AS uv

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY public ./public
COPY .chainlit/config.toml ./.chainlit/config.toml

RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/.files \
    && chown appuser:appuser /app/.files

USER appuser

EXPOSE 8000

CMD ["python", "-m", "gis_agent.container"]

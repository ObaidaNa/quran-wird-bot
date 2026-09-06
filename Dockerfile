# Optional: the supported deployment is a VPS with the systemd unit in deploy/.
# This image exists for anyone who would rather run a container.
#
#   docker build -t quran-wird .
#   docker run -d --name quran-wird --restart unless-stopped \
#     --env-file .env \
#     -v "$PWD/data:/app/data" -v "$PWD/assets/pages:/app/assets/pages:ro" \
#     quran-wird
#
# The page images are NOT built into the image: 604 PNGs are ~106 MB and are
# generated once from assets/hafs.zip, so they are mounted instead.

FROM python:3.12-slim AS base

# uv is the project's only build tool; copying the binary beats installing pip.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Dependencies first, so a code change does not re-resolve the lock file.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src

RUN uv sync --frozen --no-dev

# Timezones matter here: every send time is local to its group, and a slim
# image ships no zoneinfo database.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 wird \
    && mkdir -p /app/data /app/assets/pages \
    && chown -R wird:wird /app
USER wird

VOLUME ["/app/data"]

# No HEALTHCHECK: the bot makes outbound calls only and listens on nothing in
# polling mode. systemd-style "restart if it exits" is what matters, and that is
# the container runtime's --restart flag.
CMD ["uv", "run", "--frozen", "--no-dev", "-m", "quran_wird"]

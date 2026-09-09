#
# One image serving both the REST API and the mounted MCP sub-app (spec §2, §7
# Ticket 5). Build from the repository root:
#
#     docker build -f infra/Dockerfile -t weatherpruf .
#
# The frontend is not in this image — it is a static Vite build served
# separately. This container is the thing Claude.ai's connector talks to.

# ---------------------------------------------------------------------------
# Builder: resolve dependencies into a self-contained virtualenv.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Copy the dependency manifest on its own first, so the (slow) install layer is
# cached until the dependencies themselves actually change.
COPY backend/pyproject.toml ./
COPY backend/app ./app

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install '.[mcp]'

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000

# curl is here for the container health check, which the load balancer and ECS
# both use to decide whether a task is serving.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /srv
COPY --chown=app:app backend/app ./app

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

# One worker: traffic is single-digit concurrent users (spec §0), and a single
# process keeps the taxonomy cache and both connection pools to one copy each.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips '*'"]

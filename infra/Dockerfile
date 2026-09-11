#
# One image serving the whole product on one origin (spec §2, §7 Ticket 5):
# the React bundle at /, the REST API at /api, and the MCP connector at /mcp.
# Build from the repository root:
#
#     docker build -f infra/Dockerfile -t weatherpruf .
#
# Serving the frontend from the same origin as the API is what removes CORS,
# the second certificate and the second deploy from this project entirely.

# ---------------------------------------------------------------------------
# Frontend builder: compile the React app to static assets.
# ---------------------------------------------------------------------------
FROM node:22-slim AS frontend

WORKDIR /build

# npm ci needs both files and installs exactly what the lockfile pins.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# Vite inlines VITE_* values at build time rather than reading them at runtime,
# so the Supabase project has to be known here, not in the task definition.
# Both are publishable: the anon key is safe in a bundle because RLS is what
# protects the data. Never pass the service-role key.
ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_ANON_KEY
RUN npm run build

# ---------------------------------------------------------------------------
# Backend builder: install the app and its dependencies into a self-contained venv.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# The build backend needs both the manifest and the package directory present,
# so a change to any source file does re-run the dependency install. That is
# the honest trade for not maintaining a second, drift-prone requirements.txt;
# the install is small enough that it is not worth a lockfile here.
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

# curl is here for the container health check, which ECS and the load balancer
# both use to decide whether a task is serving.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app

# The venv carries the installed `app` package, so there is no second copy of
# the source in the image and no ambiguity about which one is imported.
COPY --from=builder --chown=root:root /opt/venv /opt/venv

# The built frontend. FRONTEND_DIST_PATH defaults to this location; the app
# serves it at / and falls back to index.html for client-side routes.
COPY --from=frontend --chown=root:root /build/dist /srv/static

WORKDIR /srv
USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

# One worker: traffic is single-digit concurrent users (spec §0), and a single
# process keeps the taxonomy cache and both connection pools to one copy each.
# --proxy-headers so the app sees the scheme and client address the Express
# Mode load balancer forwarded, rather than the load balancer's own.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips '*'"]

# SwasthSetu — one image serving the website and its API.
#
#   docker build -t swasthsetu .
#   docker run -p 8080:8080 --env-file .env.production swasthsetu
#
# Stage 1 builds the site; stage 2 is a slim Python runtime that serves the
# built files and the API from a single origin, as a non-root user.

# ---------------------------------------------------------------- website ---
FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- runtime ---
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    STATIC_DIR=/app/static

WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY backend/scripts ./scripts
COPY --from=web /web/dist ./static

# A missing website is the one deployment fault this image could otherwise hide:
# the API would answer, the health check would pass, and every page would 404.
# Fail the build instead, where somebody is watching the output.
RUN test -f /app/static/index.html || { echo "BUILD FAILED: the frontend did not reach /app/static/index.html. The web stage built, but its dist/ did not land in the runtime layer. If a cached layer is to blame, redeploy with the build cache cleared."; ls -la /app/static; exit 1; }

RUN useradd --system --uid 10001 --home /app app && chown -R app:app /app
USER app

EXPOSE 8080

# Migrations run before the first request, not by hand afterwards: the free
# tiers of most hosts have no pre-deploy hook and no shell, so a schema change
# that only a human could apply would silently never be applied. One instance
# serves this service, so there is no race between two of them migrating.
#
# --proxy-headers: behind a platform front end, take the real client address
# (used for sign-in rate limiting) and the scheme from X-Forwarded-* headers.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 20"]

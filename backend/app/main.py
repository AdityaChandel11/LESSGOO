"""SwasthSetu — one service for the website and its API.

Development:  uvicorn app.main:app --reload --port 8000   (Vite serves the site)
Production:   the container serves the built site from STATIC_DIR and the API
              under /api, from a single origin.

The site and API share one origin, so session cookies stay first-party and no
CORS is involved. That holds both when the container is reached directly and
when Firebase Hosting fronts it: Hosting serves the static files and rewrites
/api to this service. Every API request is short (live updates are polled,
see events.py), so Hosting's 60-second rewrite limit never applies.
"""

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import auth
from .api import public_router, router
from .config import settings
from .db import engine, ping

DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


# =================================================================== logging ===


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Cloud Logging reads `severity` natively."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "method", "path", "status", "duration_ms"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    if settings.is_production:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


configure_logging()
log = logging.getLogger("swasthsetu")


# =================================================================== app ===


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "%s v%s starting (%s) - llm=%s maps=%s comms=%s demo=%s",
        settings.app_name, settings.api_version, settings.environment,
        settings.llm_mode, settings.maps_mode, settings.comms_mode, settings.demo_mode,
    )
    if not await ping():
        # Loud but not fatal: the health endpoint must stay reachable so the
        # operator can see *why* the service is degraded.
        log.error("Database unreachable at startup")
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version=settings.api_version,
    description="National PHC resource and supply chain platform",
    lifespan=lifespan,
    # Interactive API docs are a development tool, not a public page.
    docs_url=None if settings.is_production else "/api/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/api/openapi.json",
)

cors_origins = settings.cors_origins or ([] if settings.is_production else DEV_ORIGINS)
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )

# Sources the browser may load from. Map tiles and fonts are the only third
# parties; Google's origins are listed ahead of MAPS_MODE=google.
# KEEP IN SYNC with the Content-Security-Policy header in firebase.json, which
# applies when Firebase Hosting serves the site instead of this process.
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' https://maps.googleapis.com",
        # Inline styles: React style props and Leaflet's map markup need them.
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data: blob: https://tile.openstreetmap.org "
        "https://*.tile.openstreetmap.org https://maps.gstatic.com https://*.googleapis.com",
        "connect-src 'self' https://maps.googleapis.com https://tile.googleapis.com",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "object-src 'none'",
    ]
)


def _same_origin(request: Request, origin: str) -> bool:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return urlparse(origin).netloc == host or origin in cors_origins


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-cloud-trace-context", "").split("/")[0] or uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()

    # Defence in depth against cross-site requests. SameSite=Lax cookies are
    # already withheld from cross-site POSTs; this also refuses any write whose
    # browser-supplied Origin is foreign. Server-to-server callers (Twilio
    # webhooks) send no Origin and are verified by signature instead.
    origin = request.headers.get("origin")
    if (
        request.method in UNSAFE_METHODS
        and request.url.path.startswith("/api/")
        and origin
        and not _same_origin(request, origin)
    ):
        return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)

    try:
        response: Response = await call_next(request)
    except Exception:
        log.exception(
            "Unhandled error",
            extra={"request_id": request_id, "method": request.method, "path": request.url.path},
        )
        response = JSONResponse(
            {"detail": "Something went wrong on our side.", "request_id": request_id},
            status_code=500,
        )

    path = request.url.path
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(self), microphone=(self), geolocation=(self)"
    response.headers["Content-Security-Policy"] = CSP
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    if path.startswith("/assets/"):
        # Vite fingerprints asset filenames, so they can be cached forever.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path.startswith("/api/") and "Cache-Control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"

    if path != "/api/health":
        log.info(
            "%s %s %s",
            request.method, path, response.status_code,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
    return response


app.include_router(public_router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(router, prefix="/api")


# ============================================================ website ===

static_dir: Path = settings.site_root

if settings.serves_built_site:
    if (static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def website(full_path: str) -> Response:
        # Unknown API paths get a JSON 404, never the HTML page.
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        if full_path:
            candidate = (static_dir / full_path).resolve()
            # resolve() + parent check blocks ../ traversal out of the site.
            if candidate.is_file() and static_dir in candidate.parents:
                return FileResponse(candidate)
        # Every other path is a page of the single-page app; the browser-side
        # router decides what to show, so deep links survive a refresh.
        return FileResponse(static_dir / "index.html", headers={"Cache-Control": "no-cache"})
elif settings.is_production:
    # In production this is not a mode, it is a broken image: the frontend
    # stage did not land in the runtime layer. The API will answer and every
    # page will 404, so say so at a level that shows up in a host's log filter.
    log.error(
        "No built site at %s. The API is answering but the website is not being "
        "served — this container was built without the frontend.",
        static_dir,
    )
else:
    log.info("No built site at %s; the API only is served (development)", static_dir)

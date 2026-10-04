from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from .config import settings
from .public_cache import PublicCacheMiddleware

# VERSION at the repo root is the server's one version number: the deploy
# installer reads it to decide when to reconfigure, and the API reports it
# here. pyproject.toml takes it from the same file, so nothing else carries a
# number of its own to drift.
VERSION = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()

# Static files directory: driven by `settings.static_dir` (env var STATIC_DIR).
# See issue #9 — this used to be hardcoded to `<club_server>/static`, which
# silently ignored the value deploy.sh exported and put served files in the
# wrong location. The directory is created on startup if it doesn't exist.
STATIC_DIR = Path(settings.static_dir).resolve()
STATIC_DIR.mkdir(parents=True, exist_ok=True)
from .routers import (  # noqa: E402 — directories created at import time, before route/worker modules load
    admin_router,
    admin_preferences_router,
    auth_router,
    users_router,
    groups_router,
    venues_router,
    events_router,
    event_lifecycle_router,
    event_enrollments_router,
    myevents_router,
    mygroups_router,
    occurrences_router,
    notifications_router,
    broadcasts_router,
    public_router,
    public_venues_router,
    public_events_router,
    public_club_info_router,
    event_marketing_router,
    public_event_marketing_router,
    public_inquiries_router,
    admin_inquiries_router,
    admin_staff_listing_router,
    media_router,
    user_media_router,
    event_media_router,
    group_media_router,
    venue_media_router,
    audit_log_router,
    capabilities_router,
    credits_router,
    credits_query_router,
    credit_roster_router,
    mycredits_router,
    evaluations_router,
    evaluation_lifecycle_router,
    evaluation_templates_router,
    evaluation_template_items_router,
    evaluation_answers_router,
    myevaluations_router,
    evaluation_media_router,
)
from .worker import start_worker, stop_worker  # noqa: E402 — see above


UPLOAD_DIR = Path(settings.upload_dir).resolve()
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager."""
    # Startup
    print(f"Starting Club Server in {settings.environment} mode")
    await start_worker()
    yield
    # Shutdown
    await stop_worker()
    print("Shutting down Club Server")


app = FastAPI(
    title="Club Server API",
    description="Backend API for Club SDK",
    version=VERSION,
    lifespan=lifespan,
)

from .exception_handlers import register_exception_handlers  # noqa: E402

register_exception_handlers(app)

# CORS configuration. Both values come from settings, which sources them from
# the environment or the deployer's config file — see issue #311. There is no
# fallback: the previous one hardcoded one club's domain on a server that
# served another, and any deployment omitting the setting inherited it silently.
ALLOWED_ORIGINS = settings.cors_allowed_origins
ALLOW_CREDENTIALS = settings.cors_allow_credentials


class StaticFilesCORSMiddleware(BaseHTTPMiddleware):
    """Middleware to add CORS headers to static file responses.

    FastAPI's CORSMiddleware doesn't apply to mounted sub-apps (like StaticFiles),
    so we need this middleware to ensure video/media files can be loaded by browsers.

    Note: Static files are public resources, so we use permissive CORS.
    Safari video requests may not send Origin header (Sec-Fetch-Mode: no-cors),
    so we allow all origins for static files.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Only add CORS headers for static file requests
        if request.url.path.startswith("/static/"):
            # Static files are public - allow all origins for broad compatibility
            # Safari video requests often don't send Origin header
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "*"
            response.headers["Access-Control-Expose-Headers"] = (
                "Content-Length, Content-Range"
            )
            # Cache static files for 1 day (videos, images are immutable by path)
            response.headers["Cache-Control"] = "public, max-age=86400"

        return response


# Add static files CORS middleware first (runs last, after response is created)
app.add_middleware(StaticFilesCORSMiddleware)
# Cache headers and conditional 304s on the public surface (#297).
app.add_middleware(PublicCacheMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=ALLOW_CREDENTIALS,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"],
)


# Global exception handler for consistent error responses
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle unexpected exceptions."""
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred",
                "details": {"type": type(exc).__name__}
                if settings.environment == "development"
                else None,
            }
        },
    )


# Include routers
app.include_router(admin_router, prefix=settings.api_v1_prefix)
app.include_router(admin_preferences_router, prefix=settings.api_v1_prefix)
app.include_router(auth_router, prefix=settings.api_v1_prefix)
app.include_router(users_router, prefix=settings.api_v1_prefix)
app.include_router(groups_router, prefix=settings.api_v1_prefix)
app.include_router(venues_router, prefix=settings.api_v1_prefix)
app.include_router(events_router, prefix=settings.api_v1_prefix)
app.include_router(event_lifecycle_router, prefix=settings.api_v1_prefix)
app.include_router(event_enrollments_router, prefix=settings.api_v1_prefix)
app.include_router(myevents_router, prefix=settings.api_v1_prefix)
app.include_router(mygroups_router, prefix=settings.api_v1_prefix)
app.include_router(occurrences_router, prefix=settings.api_v1_prefix)
app.include_router(notifications_router, prefix=settings.api_v1_prefix)
app.include_router(broadcasts_router, prefix=settings.api_v1_prefix)
app.include_router(public_router, prefix=settings.api_v1_prefix)
app.include_router(public_venues_router, prefix=settings.api_v1_prefix)
app.include_router(public_event_marketing_router, prefix=settings.api_v1_prefix)
app.include_router(public_events_router, prefix=settings.api_v1_prefix)
app.include_router(event_marketing_router, prefix=settings.api_v1_prefix)
app.include_router(public_inquiries_router, prefix=settings.api_v1_prefix)
app.include_router(admin_inquiries_router, prefix=settings.api_v1_prefix)
app.include_router(public_club_info_router, prefix=settings.api_v1_prefix)
app.include_router(admin_staff_listing_router, prefix=settings.api_v1_prefix)
app.include_router(media_router, prefix=settings.api_v1_prefix)
app.include_router(user_media_router, prefix=settings.api_v1_prefix)
app.include_router(event_media_router, prefix=settings.api_v1_prefix)
app.include_router(group_media_router, prefix=settings.api_v1_prefix)
app.include_router(venue_media_router, prefix=settings.api_v1_prefix)
app.include_router(audit_log_router, prefix=settings.api_v1_prefix)
# Credit system (#294). Registered on every deployment so the published API
# does not vary with configuration; a deployment that does not run on credits
# refuses every route with 503 via require_credit_system_enabled.
app.include_router(capabilities_router, prefix=settings.api_v1_prefix)
app.include_router(credits_router, prefix=settings.api_v1_prefix)
app.include_router(credits_query_router, prefix=settings.api_v1_prefix)
app.include_router(credit_roster_router, prefix=settings.api_v1_prefix)
app.include_router(mycredits_router, prefix=settings.api_v1_prefix)
# Evaluations (#302). Registered on every deployment for the same reason the
# credit routers are: the published API must not vary with configuration.
# A deployment that does not ship evaluations refuses every route with 503
# via require_evaluations_enabled. The templates router is included before
# the evaluations router so /evaluations/templates/... is matched by its own
# literal prefix rather than by /evaluations/by_id/{id}.
app.include_router(evaluation_templates_router, prefix=settings.api_v1_prefix)
app.include_router(evaluation_template_items_router, prefix=settings.api_v1_prefix)
app.include_router(evaluations_router, prefix=settings.api_v1_prefix)
app.include_router(evaluation_lifecycle_router, prefix=settings.api_v1_prefix)
app.include_router(evaluation_answers_router, prefix=settings.api_v1_prefix)
app.include_router(myevaluations_router, prefix=settings.api_v1_prefix)
app.include_router(evaluation_media_router, prefix=settings.api_v1_prefix)


# Mount static files directory for serving images and pages
# Images will be accessible at /static/images/filename.jpg
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Club Server API", "version": VERSION}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}

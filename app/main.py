"""FastAPI application entry point.

Run with a single worker: SQLite has one writer, and the analysis job cache
lives in the same process.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import engine
from app.models import Base
from app.routers import auth, dashboard, distribution, editor, participation, reports
from app.security import NotAuthenticated, redirect_to_login
from app.templating import templates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Alembic owns the schema in deployment; this keeps a fresh checkout runnable.
    Base.metadata.create_all(engine)
    if settings.secret_key.startswith("dev-only"):
        logger.warning(
            "HGSFP_SECRET_KEY is unset — sessions use a well-known development key."
        )
    yield


app = FastAPI(title="HGSFP Umfrage-Dashboard", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="hgsfp_session",
    same_site="lax",
    https_only=settings.cookie_secure,
    max_age=8 * 60 * 60,
)

app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(editor.router)
app.include_router(distribution.router)
app.include_router(reports.router)
app.include_router(participation.router)


@app.exception_handler(NotAuthenticated)
async def handle_not_authenticated(request: Request, exc: NotAuthenticated):
    """Send browsers to the login form rather than a bare 401."""
    return redirect_to_login(exc.next_url)


@app.exception_handler(404)
async def handle_not_found(request: Request, exc):
    if request.url.path.startswith(("/s/", "/dashboard", "/surveys")):
        return templates.TemplateResponse(
            request,
            "errors/404.html",
            {"detail": getattr(exc, "detail", "Nicht gefunden")},
            status_code=404,
        )
    return JSONResponse({"detail": "Not found"}, status_code=404)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}

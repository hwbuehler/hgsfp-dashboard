"""Login and logout for the dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.security import authenticate, get_optional_user, login_session, logout_session
from app.templating import templates

router = APIRouter(tags=["auth"])


def _safe_next(candidate: str | None) -> str:
    """Only allow same-site paths, so ?next= cannot bounce users off-site."""
    if not candidate or not candidate.startswith("/") or candidate.startswith("//"):
        return "/dashboard"
    return candidate


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str | None = None, user=Depends(get_optional_user)):
    if user is not None:
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "auth/login.html", {"next_url": _safe_next(next), "error": None, "user": None}
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/dashboard"),
    session: Session = Depends(get_session),
):
    user = authenticate(session, username.strip(), password)
    if user is None:
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {
                "next_url": _safe_next(next),
                "error": "Benutzername oder Passwort ist falsch.",
                "user": None,
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    login_session(request, user)
    return RedirectResponse(_safe_next(next), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
def logout(request: Request):
    logout_session(request)
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

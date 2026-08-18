"""Public routes — the questionnaire itself.

Nothing here requires a login, and nothing here stores anything that could
identify a participant: no IP address, no user agent, no session cookie, and a
date rather than a timestamp on the response.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Response as ResponseRow
from app.models import Survey, SurveyStatus
from app.surveyjs import ValidationProblem, build_definition, validate_payload
from app.templating import templates

router = APIRouter(tags=["participation"])


def _load_survey(session: Session, token: str) -> Survey:
    survey = session.scalars(
        select(Survey).where(Survey.public_token == token)
    ).first()
    if survey is None:
        raise HTTPException(status_code=404, detail="Diese Umfrage existiert nicht.")
    return survey


@router.get("/s/{token}", response_class=HTMLResponse)
def participate(request: Request, token: str, session: Session = Depends(get_session)):
    survey = _load_survey(session, token)
    if not survey.is_open:
        return templates.TemplateResponse(
            request, "participation/closed.html", {"survey": survey}, status_code=410
        )
    return templates.TemplateResponse(
        request,
        "participation/survey.html",
        {
            "survey": survey,
            "definition_url": f"/s/{token}/definition.json",
            "submit_url": f"/s/{token}/responses",
            "is_preview": False,
        },
    )


@router.get("/s/{token}/definition.json")
def definition(token: str, session: Session = Depends(get_session)):
    survey = _load_survey(session, token)
    if not survey.is_open:
        raise HTTPException(status_code=410, detail="Diese Umfrage ist geschlossen.")
    return JSONResponse(build_definition(survey))


@router.post("/s/{token}/responses")
async def submit(request: Request, token: str, session: Session = Depends(get_session)):
    survey = _load_survey(session, token)
    if not survey.is_open:
        raise HTTPException(status_code=410, detail="Diese Umfrage ist geschlossen.")

    payload: Any = await request.json()
    try:
        cleaned = validate_payload(survey, payload)
    except ValidationProblem as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.add(ResponseRow(survey_id=survey.id, payload_json=cleaned))
    session.commit()
    return JSONResponse({"status": "ok"})


@router.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return templates.TemplateResponse(request, "participation/privacy.html", {})

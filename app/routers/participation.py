"""Public routes — the questionnaire itself.

Nothing here requires a login, and nothing here stores anything that could
identify a participant: no IP address, no user agent, no session cookie, and a
date rather than a timestamp on the response.

These three routes are the only ones a lecture hall hits at the same moment, so
they are also the only ones written with concurrency in mind:

* the survey and its slots are loaded in one query rather than one per slot;
* the blocking database work runs in the threadpool, never on the event loop;
* the request body is bounded before it is parsed.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session, joinedload, selectinload
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.db import get_session
from app.models import Response as ResponseRow
from app.models import Survey, SurveySlot
from app.models import Survey, SurveySlot
from app.surveyjs import ValidationProblem, build_definition, validate_payload
from app.templating import templates

router = APIRouter(tags=["participation"])


def _load_survey(session: Session, token: str, *, with_template: bool = False) -> Survey:
    """Load a survey by its public token, slots and choices included.

    The eager load is not a micro-optimisation: validating one submission walks
    every slot and every slot's choices, so lazy loading turns a single request
    into one query per slot.  On the public endpoints that multiplier lands
    exactly when the most people are submitting at once.
    """
    statement = select(Survey).where(Survey.public_token == token)
    statement = statement.options(
        selectinload(Survey.slots).selectinload(SurveySlot.choices)
    )
    if with_template:
        statement = statement.options(joinedload(Survey.template))

    survey = session.scalars(statement).first()
    if survey is None:
        raise HTTPException(status_code=404, detail="Diese Umfrage existiert nicht.")
    return survey


async def _read_json_body(request: Request) -> Any:
    """Read and parse the request body, refusing anything oversized.

    Content-Length is checked first so an obvious flood is rejected before a
    single byte is read; the streaming loop then catches a chunked body, which
    carries no length up front.
    """
    limit = settings.max_submission_bytes

    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise HTTPException(status_code=413, detail="Die Antwort ist zu groß.")
        except ValueError:
            raise HTTPException(status_code=400, detail="Ungültiger Request.") from None

    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > limit:
            raise HTTPException(status_code=413, detail="Die Antwort ist zu groß.")
        chunks.append(chunk)

    try:
        return json.loads(b"".join(chunks))
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Without this the decode error escapes as a 500 and the participant is
        # told nothing useful about an answer that is now lost.
        raise HTTPException(
            status_code=400, detail="Antwort hat kein gültiges Format."
        ) from None


def _store_response(session: Session, token: str, payload: Any) -> None:
    """Validate and persist one submission.  Blocking — call off the event loop."""
    survey = _load_survey(session, token)
    if not survey.is_open:
        raise HTTPException(status_code=410, detail="Diese Umfrage ist geschlossen.")

    try:
        cleaned = validate_payload(survey, payload)
    except ValidationProblem as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.add(ResponseRow(survey_id=survey.id, payload_json=cleaned))
    session.commit()


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
    survey = _load_survey(session, token, with_template=True)
    if not survey.is_open:
        raise HTTPException(status_code=410, detail="Diese Umfrage ist geschlossen.")
    return JSONResponse(build_definition(survey))


@router.post("/s/{token}/responses")
async def submit(request: Request, token: str, session: Session = Depends(get_session)):
    """Accept one anonymous submission.

    Async only because the body has to be awaited.  Everything after that is
    synchronous SQLAlchemy, which would otherwise stall the event loop for the
    whole survey — every other participant's page load included — for the
    duration of each insert.  ``run_in_threadpool`` is what keeps a burst of
    submissions from serialising behind one another.
    """
    payload = await _read_json_body(request)
    await run_in_threadpool(_store_response, session, token, payload)
    return JSONResponse({"status": "ok"})


@router.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return templates.TemplateResponse(request, "participation/privacy.html", {})

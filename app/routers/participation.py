"""Public routes — the questionnaire itself.

Nothing here requires a login, and nothing here stores anything that could
identify a participant: no IP address, no user agent, no session cookie, and a
date rather than a timestamp on the response.

These three routes are the only ones a lecture hall hits at the same moment, so
they are also the only ones written for a burst:

* the definition is built once per edit and served from memory, because it is
  byte-identical for every participant of a run;
* the submission handler does its SQLite work in a worker thread, so a write
  never stalls the event loop and with it everybody else's request;
* the request body is capped while it is being read, because this is the one
  endpoint an unauthenticated stranger with the link can post to.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload
from starlette.concurrency import run_in_threadpool

from app.db import get_session
from app.models import Response as ResponseRow
from app.models import Survey, SurveySlot
from app.surveyjs import ValidationProblem, build_definition, validate_payload
from app.templating import templates

router = APIRouter(tags=["participation"])

# A complete response is a few kilobytes. The cap exists because this endpoint is
# public: without it, one POST can push the single worker into swap.
MAX_BODY_BYTES = 256 * 1024


def _load_survey(
    session: Session,
    token: str,
    *,
    with_slots: bool = False,
    with_template: bool = False,
) -> Survey:
    """Load a survey with exactly what the caller is about to touch.

    Submitting and building the definition both walk every slot and every
    choice, which lazily loaded meant one query per lecture slot. The consent
    page reads only the title and the intro text, so it asks for neither —
    eager-loading there would trade one query for three to no purpose.
    """
    statement = select(Survey).where(Survey.public_token == token)
    if with_slots:
        statement = statement.options(
            selectinload(Survey.slots).selectinload(SurveySlot.choices)
        )
    if with_template:
        statement = statement.options(joinedload(Survey.template))

    survey = session.scalars(statement).first()
    if survey is None:
        raise HTTPException(status_code=404, detail="Diese Umfrage existiert nicht.")
    return survey


# --- Definition cache ----------------------------------------------------
#
# Every participant fetches the same definition, and rebuilding it costs a
# database round trip per slot plus a deep copy of the blueprint. The cached
# entry is keyed on both rows' updated_at, so an edit invalidates it by itself —
# there is no invalidation call that a future route could forget to make.

_definition_cache: dict[int, tuple[tuple, bytes]] = {}
_definition_lock = threading.Lock()


def _definition_version(survey: Survey) -> tuple:
    return (survey.updated_at, survey.template.updated_at)


def cached_definition(survey: Survey) -> tuple[bytes, str]:
    """Serialised SurveyJS definition plus an ETag for it."""
    version = _definition_version(survey)
    with _definition_lock:
        entry = _definition_cache.get(survey.id)
        if entry is not None and entry[0] == version:
            return entry[1], _etag(survey.id, version)

    body = json.dumps(build_definition(survey), ensure_ascii=False).encode("utf-8")
    with _definition_lock:
        _definition_cache[survey.id] = (version, body)
    return body, _etag(survey.id, version)


def _etag(survey_id: int, version: tuple) -> str:
    return f'W/"{survey_id}-{abs(hash(version)):x}"'


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
def definition(request: Request, token: str, session: Session = Depends(get_session)):
    survey = _load_survey(session, token, with_slots=True, with_template=True)
    if not survey.is_open:
        raise HTTPException(status_code=410, detail="Diese Umfrage ist geschlossen.")

    body, etag = cached_definition(survey)
    # A reload during the burst — people do refresh — costs a 304 instead of a
    # second copy of the questionnaire.
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(
        content=body,
        media_type="application/json",
        headers={"ETag": etag, "Cache-Control": "no-cache"},
    )


async def _read_body(request: Request) -> bytes:
    """Read the request body, refusing anything oversized while reading it.

    Streamed rather than buffered through ``request.body()``: Content-Length is
    a claim by the client, the running total is the fact.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise HTTPException(
                status_code=413, detail="Die Antwort ist zu groß."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _store_response(session: Session, token: str, payload: Any) -> None:
    """Validate and persist. Synchronous on purpose — the caller runs it in a thread."""
    survey = _load_survey(session, token, with_slots=True)
    if not survey.is_open:
        raise HTTPException(status_code=410, detail="Diese Umfrage ist geschlossen.")

    try:
        cleaned = validate_payload(survey, payload)
    except ValidationProblem as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.add(ResponseRow(survey_id=survey.id, payload_json=cleaned))
    session.commit()


@router.post("/s/{token}/responses")
async def submit(request: Request, token: str, session: Session = Depends(get_session)):
    body = await _read_body(request)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        # A truncated POST from a phone that lost signal lands here. It is the
        # participant's mistake to nobody, but a 500 tells them the survey is
        # broken and buries a traceback in the log for every one of them.
        raise HTTPException(
            status_code=400, detail="Antwort hat kein gültiges Format."
        ) from exc

    # Everything past this point talks to SQLite. Left on the event loop it would
    # hold up every other participant's page load for the duration of the write.
    await run_in_threadpool(_store_response, session, token, payload)
    return JSONResponse({"status": "ok"})


@router.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return templates.TemplateResponse(request, "participation/privacy.html", {})

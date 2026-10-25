"""Analysis dataset list, import, data management and deletion.

The dashboard is analysis-only: datasets are created by uploading a SurveyJS
result export (``POST /imports``), then managed from here — lecturer names
are assigned, data is exported or deleted.
"""

from __future__ import annotations

import json
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.legacy_import import (
    ImportError_,
    derive_slots,
    parse_export,
)
from app.config import settings
from app.db import get_session
from app.models import Response as ResponseRow
from app.models import (
    SlotChoice,
    Survey,
    SurveySlot,
    SurveyStatus,
    User,
    utcnow,
)
from app.security import require_user
from app.templating import templates

router = APIRouter(tags=["dashboard"])


def get_survey(session: Session, survey_id: int) -> Survey:
    survey = session.get(Survey, survey_id)
    if survey is None:
        raise HTTPException(status_code=404, detail="Umfrage nicht gefunden")
    return survey


def response_counts(session: Session) -> dict[int, int]:
    rows = session.execute(
        select(ResponseRow.survey_id, func.count(ResponseRow.id)).group_by(
            ResponseRow.survey_id
        )
    ).all()
    return {survey_id: count for survey_id, count in rows}


@router.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/dashboard", response_class=HTMLResponse)
def survey_list(
    request: Request,
    created: int | None = None,
    error: str | None = None,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    surveys = session.scalars(select(Survey).order_by(Survey.id.desc())).all()
    return templates.TemplateResponse(
        request,
        "dashboard/list.html",
        {
            "user": user,
            "surveys": surveys,
            "counts": response_counts(session),
            "created": created,
            "error": error,
        },
    )


async def read_upload(file: UploadFile) -> bytes:
    """Read an uploaded file into memory, refusing anything oversized.

    ``UploadFile.read()`` with no argument is unbounded: the file is buffered to
    disk by Starlette and then pulled into RAM in full.  A result export is well
    under a megabyte, so a ceiling costs nothing and keeps a mistyped upload
    from taking the process down.
    """
    limit = settings.max_upload_bytes
    chunks: list[bytes] = []
    received = 0
    while chunk := await file.read(64 * 1024):
        received += len(chunk)
        if received > limit:
            raise HTTPException(
                status_code=413,
                detail=f"Die Datei ist größer als {limit // (1024 * 1024)} MB.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/imports")
async def import_analysis_file(
    title: str = Form(""),
    file: UploadFile = File(...),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """Create an analysis dataset directly from a SurveyJS result export."""
    raw = await read_upload(file)
    try:
        payloads = parse_export(raw)
        slots = derive_slots(payloads)
    except ImportError_ as exc:
        return RedirectResponse(
            f"/dashboard?error={quote(str(exc))}", status_code=status.HTTP_303_SEE_OTHER
        )

    clean_title = title.strip() or (file.filename or "Survey results").rsplit(".", 1)[0]
    survey = Survey(
        template_id=None,
        title=clean_title,
        public_token=secrets.token_urlsafe(12),
        created_by_id=user.id,
        status=SurveyStatus.closed,
        closed_at=utcnow(),
    )
    session.add(survey)
    session.flush()
    for position, spec in enumerate(slots):
        slot = SurveySlot(
            survey_id=survey.id,
            key_prefix=spec["key_prefix"],
            page_title=spec["page_title"],
            position=position,
        )
        session.add(slot)
        session.flush()
        for choice_position, value in enumerate(spec["choices"]):
            session.add(SlotChoice(
                slot_id=slot.id, value=value, text="", position=choice_position
            ))
    today = utcnow().date()
    session.add_all(
        ResponseRow(survey_id=survey.id, payload_json=payload, submitted_on=today)
        for payload in payloads
    )
    session.commit()
    return RedirectResponse(
        f"/surveys/{survey.id}/report?created={len(payloads)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/surveys/{survey_id}/lectures", response_class=HTMLResponse)
def lecture_details_page(
    request: Request,
    survey_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    survey = get_survey(session, survey_id)
    return templates.TemplateResponse(
        request,
        "dashboard/lectures.html",
        {"user": user, "survey": survey},
    )


@router.post("/surveys/{survey_id}/lecture-details")
async def update_lecture_details(
    request: Request,
    survey_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    survey = get_survey(session, survey_id)
    form = await request.form()
    for slot in survey.slots:
        for choice in slot.choices:
            lecturer = str(form.get(f"lecturer_{choice.id}", "")).strip()
            title = str(form.get(f"title_{choice.id}", "")).strip()
            if not lecturer or not title:
                return templates.TemplateResponse(
                    request,
                    "dashboard/lectures.html",
                    {
                        "user": user,
                        "survey": survey,
                        "error": "Enter both a lecturer name and lecture title for every row.",
                    },
                    status_code=422,
                )
            choice.lecturer_name = lecturer
            choice.text = title
    survey.updated_at = utcnow()
    session.commit()
    return RedirectResponse(
        f"/surveys/{survey.id}/report?details_updated=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/surveys/{survey_id}/delete")
def delete_survey(
    survey_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """Right to erasure: removes the survey with all responses and analyses."""
    survey = get_survey(session, survey_id)
    session.delete(survey)
    session.commit()
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/surveys/{survey_id}/responses/delete")
def delete_responses(
    survey_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """Delete the answers but keep the survey configuration."""
    survey = get_survey(session, survey_id)
    for response in list(survey.responses):
        session.delete(response)
    for run in list(survey.analysis_runs):
        session.delete(run)
    session.commit()
    return RedirectResponse(
        f"/surveys/{survey_id}/data?deleted=1", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/surveys/{survey_id}/data", response_class=HTMLResponse)
def data_page(
    request: Request,
    survey_id: int,
    deleted: int | None = None,
    error: str | None = None,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    survey = get_survey(session, survey_id)
    count = session.scalar(
        select(func.count(ResponseRow.id)).where(ResponseRow.survey_id == survey_id)
    )
    return templates.TemplateResponse(
        request,
        "dashboard/data.html",
        {
            "user": user,
            "survey": survey,
            "response_count": count or 0,
            "deleted": deleted,
            "error": error,
        },
    )


@router.get("/surveys/{survey_id}/export.json")
def export_responses(
    survey_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    survey = get_survey(session, survey_id)
    payloads = list(
        session.scalars(
            select(ResponseRow.payload_json).where(ResponseRow.survey_id == survey_id)
        )
    )
    # Same envelope the old tool consumed, so exports stay interchangeable.
    document = {"ResultCount": len(payloads), "Data": payloads}
    filename = f"survey-{survey.id}-responses.json"
    return Response(
        content=json.dumps(document, ensure_ascii=False, indent=1),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

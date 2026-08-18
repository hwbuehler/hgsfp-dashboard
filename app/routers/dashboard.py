"""Survey list, creation, data import/export and deletion."""

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
    derive_slot_choices,
    import_into,
    parse_export,
)
from app.db import get_session
from app.models import Response as ResponseRow
from app.models import (
    SlotChoice,
    Survey,
    SurveySlot,
    SurveyStatus,
    SurveyTemplate,
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
    templates_available = session.scalars(
        select(SurveyTemplate).order_by(SurveyTemplate.name)
    ).all()
    return templates.TemplateResponse(
        request,
        "dashboard/list.html",
        {
            "user": user,
            "surveys": surveys,
            "counts": response_counts(session),
            "templates_available": templates_available,
            "created": created,
            "error": error,
        },
    )


def seed_slots(
    session: Session,
    survey: Survey,
    template: SurveyTemplate,
    choices_override: dict[str, list[str]] | None = None,
) -> None:
    """Create the survey's slots from the template blueprint.

    ``choices_override`` replaces the blueprint's lecture list per slot prefix —
    used when the survey is created from a results file, where the lectures in
    the data are what matter, not the ones from the previous run.
    """
    for position, slot_spec in enumerate(template.blueprint_json.get("slots", [])):
        slot = SurveySlot(
            survey_id=survey.id,
            key_prefix=slot_spec["key_prefix"],
            page_title=slot_spec["page_title"],
            position=position,
            dna_value=slot_spec.get("dna_value", "DnA"),
            dna_text=slot_spec.get("dna_text", "did not attend"),
        )
        session.add(slot)
        session.flush()

        if choices_override is None:
            entries = [
                (c["value"], c.get("text", c["value"]))
                for c in slot_spec.get("choices", [])
            ]
        else:
            # The file only carries the stored key; the display text starts out
            # as that key and is edited afterwards.
            entries = [(v, v) for v in choices_override.get(slot.key_prefix, [])]

        for choice_position, (value, text) in enumerate(entries):
            session.add(
                SlotChoice(
                    slot_id=slot.id, value=value, text=text, position=choice_position
                )
            )


@router.post("/surveys")
def create_survey(
    title: str = Form(...),
    template_id: int = Form(...),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    template = session.get(SurveyTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Vorlage nicht gefunden")

    survey = Survey(
        template_id=template.id,
        title=title.strip() or template.name,
        public_token=secrets.token_urlsafe(12),
        created_by_id=user.id,
    )
    session.add(survey)
    session.flush()

    seed_slots(session, survey, template)
    session.commit()
    return RedirectResponse(
        f"/surveys/{survey.id}/edit", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/surveys/from-file")
async def create_survey_from_file(
    title: str = Form(...),
    template_id: int = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """Create a survey straight from a results file.

    Saves the round trip of creating an empty survey, hunting for the import
    page, and then correcting the lecture list by hand.
    """
    template = session.get(SurveyTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Vorlage nicht gefunden")

    raw = await file.read()
    try:
        payloads = parse_export(raw)
    except ImportError_ as exc:
        return RedirectResponse(
            f"/dashboard?error={quote(str(exc))}", status_code=status.HTTP_303_SEE_OTHER
        )

    prefixes = [s["key_prefix"] for s in template.blueprint_json.get("slots", [])]
    survey = Survey(
        template_id=template.id,
        title=title.strip() or template.name,
        public_token=secrets.token_urlsafe(12),
        created_by_id=user.id,
        # Archived data is not open for new responses.
        status=SurveyStatus.closed,
        closed_at=utcnow(),
    )
    session.add(survey)
    session.flush()
    seed_slots(session, survey, template, derive_slot_choices(payloads, prefixes))

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
    imported: int | None = None,
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
            "imported": imported,
            "deleted": deleted,
            "error": error,
        },
    )


@router.post("/surveys/{survey_id}/import")
async def import_responses(
    survey_id: int,
    file: UploadFile = File(...),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    survey = get_survey(session, survey_id)
    raw = await file.read()
    try:
        count = import_into(session, survey, raw)
    except ImportError_ as exc:
        return RedirectResponse(
            f"/surveys/{survey_id}/data?error={exc}", status_code=status.HTTP_303_SEE_OTHER
        )
    return RedirectResponse(
        f"/surveys/{survey_id}/data?imported={count}", status_code=status.HTTP_303_SEE_OTHER
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

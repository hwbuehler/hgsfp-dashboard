"""Publishing a survey and handing out its link."""

from __future__ import annotations

import io

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.models import SurveyStatus, User, utcnow
from app.routers.dashboard import get_survey
from app.security import require_user
from app.analysis.report import slugify
from app.slide import build_slide
from app.surveyjs import validate_for_publish
from app.templating import templates

router = APIRouter(tags=["distribution"])


def participation_url(public_token: str) -> str:
    return f"{settings.base_url.rstrip('/')}/s/{public_token}"


@router.post("/surveys/{survey_id}/publish")
def publish(
    survey_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    survey = get_survey(session, survey_id)
    if validate_for_publish(survey):
        # The editor page lists the reasons; sending the user back there is more
        # helpful than an error page.
        return RedirectResponse(
            f"/surveys/{survey_id}/edit", status_code=status.HTTP_303_SEE_OTHER
        )
    survey.status = SurveyStatus.published
    survey.published_at = utcnow()
    survey.closed_at = None
    session.commit()
    return RedirectResponse(
        f"/surveys/{survey_id}/distribute", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/surveys/{survey_id}/unpublish")
def unpublish(
    survey_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """Back to draft — the link stops working and editing resumes."""
    survey = get_survey(session, survey_id)
    survey.status = SurveyStatus.draft
    session.commit()
    return RedirectResponse(
        f"/surveys/{survey_id}/edit", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/surveys/{survey_id}/close")
def close(
    survey_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """Stop accepting responses, keeping everything collected so far."""
    survey = get_survey(session, survey_id)
    survey.status = SurveyStatus.closed
    survey.closed_at = utcnow()
    session.commit()
    return RedirectResponse(
        f"/surveys/{survey_id}/report", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/surveys/{survey_id}/distribute", response_class=HTMLResponse)
def distribute(
    request: Request,
    survey_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    survey = get_survey(session, survey_id)
    return templates.TemplateResponse(
        request,
        "distribution/show.html",
        {
            "user": user,
            "survey": survey,
            "url": participation_url(survey.public_token),
        },
    )


@router.get("/surveys/{survey_id}/qr.png")
def qr_code(
    survey_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    import qrcode

    survey = get_survey(session, survey_id)
    image = qrcode.make(participation_url(survey.public_token))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return Response(
        content=buffer.getvalue(),
        media_type="image/png",
        headers={
            "Content-Disposition": f'inline; filename="umfrage-{survey.id}-qr.png"'
        },
    )


@router.get("/surveys/{survey_id}/slide.pdf")
def qr_slide(
    survey_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """The invitation slide, ready to project at the end of a lecture."""
    survey = get_survey(session, survey_id)
    filename = f"{slugify(survey.title)}-qr-slide.pdf"
    return Response(
        content=build_slide(participation_url(survey.public_token)),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

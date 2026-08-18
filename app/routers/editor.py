"""Editing the few parameters that change between survey runs.

Everything here returns an HTMX fragment or a redirect; there is no client-side
framework.  The one concept colleagues need to understand is that a lecture has
a *display text* (what participants read) and a *key* (what lands in the result
file) — the editor shows them side by side for that reason.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from sqlalchemy import select

from app.db import get_session
from app.models import Response, SlotChoice, Survey, SurveySlot, User, utcnow
from app.routers.dashboard import get_survey
from app.security import require_user
from app.surveyjs import build_definition, validate_for_publish
from app.templating import templates

router = APIRouter(tags=["editor"])


def _get_slot(session: Session, survey_id: int, slot_id: int) -> SurveySlot:
    slot = session.get(SurveySlot, slot_id)
    if slot is None or slot.survey_id != survey_id:
        raise HTTPException(status_code=404, detail="Slot nicht gefunden")
    return slot


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _touch(survey: Survey) -> None:
    """Record that the survey was edited.

    Editing a slot or a choice writes to a different table, so the survey row's
    onupdate never fires — without this the dashboard's "zuletzt bearbeitet"
    column and the staleness check on the report page would both be wrong.
    """
    survey.updated_at = utcnow()


def _rename_lecture_in_responses(
    session: Session, survey_id: int, prefix: str, old: str, new: str
) -> int:
    """Point already-collected responses at a renamed lecture key.

    The key is stored inside every response that selected this lecture. Leaving
    those untouched would make the report list the lecture under a name that no
    longer exists in the editor, with no hint as to why.
    """
    title_key = f"{prefix}_title"
    changed = 0
    for row in session.scalars(
        select(Response).where(Response.survey_id == survey_id)
    ):
        payload = row.payload_json
        if payload.get(title_key) != old:
            continue
        # A JSON column is only marked dirty when the attribute is reassigned;
        # mutating the dict in place would be silently discarded.
        updated = dict(payload)
        updated[title_key] = new
        row.payload_json = updated
        changed += 1
    return changed


def _saved(request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "editor/_saved.html", {"message": message, "saved_at": _now()}
    )


def _refused(request: Request, reason: str) -> HTMLResponse:
    """Report a rejected edit next to the field instead of as an error page.

    Returned with 200 on purpose: htmx only swaps successful responses, and the
    point here is to put the reason where the person is looking.
    """
    return templates.TemplateResponse(request, "editor/_saved.html", {"error": reason})


def _slot_fragment(
    request: Request,
    slot: SurveySlot,
    saved_message: str | None = None,
    highlight_choice_id: int | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "editor/_slot.html",
        {
            "slot": slot,
            "saved_message": saved_message,
            "saved_at": _now() if saved_message else None,
            "highlight_choice_id": highlight_choice_id,
        },
    )


@router.get("/surveys/{survey_id}/edit", response_class=HTMLResponse)
def edit_survey(
    request: Request,
    survey_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    survey = get_survey(session, survey_id)
    return templates.TemplateResponse(
        request,
        "editor/edit.html",
        {
            "user": user,
            "survey": survey,
            "problems": validate_for_publish(survey),
        },
    )


@router.post("/surveys/{survey_id}/title", response_class=HTMLResponse)
def update_title(
    request: Request,
    survey_id: int,
    title: str = Form(...),
    intro_text: str = Form(""),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    survey = get_survey(session, survey_id)
    survey.title = title.strip()
    survey.intro_text = intro_text.strip()
    _touch(survey)
    session.commit()
    return _saved(request, "Gespeichert")


@router.post("/surveys/{survey_id}/slots/{slot_id}", response_class=HTMLResponse)
def update_slot(
    request: Request,
    survey_id: int,
    slot_id: int,
    page_title: str = Form(...),
    key_prefix: str = Form(...),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    survey = get_survey(session, survey_id)
    slot = _get_slot(session, survey_id, slot_id)

    new_prefix = key_prefix.strip().lower()
    if not new_prefix.isidentifier():
        raise HTTPException(
            status_code=400,
            detail="Der Schlüssel darf nur Buchstaben, Ziffern und _ enthalten.",
        )
    if any(s.key_prefix == new_prefix and s.id != slot.id for s in survey.slots):
        raise HTTPException(status_code=400, detail="Dieser Schlüssel ist bereits vergeben.")

    slot.page_title = page_title.strip()
    slot.key_prefix = new_prefix
    _touch(survey)
    session.commit()

    # Only the status line is swapped, so the fields the user may still be
    # typing in are left alone; the message repeats the resulting key names.
    return _saved(
        request,
        f"Gespeichert — Schlüssel {new_prefix}_title, {new_prefix}_interesting …",
    )


@router.post("/surveys/{survey_id}/slots/{slot_id}/choices", response_class=HTMLResponse)
def add_choice(
    request: Request,
    survey_id: int,
    slot_id: int,
    text: str = Form(...),
    value: str = Form(...),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    get_survey(session, survey_id)
    slot = _get_slot(session, survey_id, slot_id)

    clean_value = value.strip()
    clean_text = text.strip()
    if not clean_value or not clean_text:
        raise HTTPException(status_code=400, detail="Titel und Schlüssel dürfen nicht leer sein.")
    if clean_value == slot.dna_value:
        raise HTTPException(
            status_code=400,
            detail=f'"{slot.dna_value}" ist für „nicht teilgenommen" reserviert.',
        )
    if any(c.value == clean_value for c in slot.choices):
        raise HTTPException(status_code=400, detail="Dieser Schlüssel wird bereits verwendet.")

    next_position = max((c.position for c in slot.choices), default=-1) + 1
    choice = SlotChoice(
        slot_id=slot.id, value=clean_value, text=clean_text, position=next_position
    )
    session.add(choice)
    _touch(session.get(Survey, survey_id))
    session.commit()
    session.refresh(slot)
    return _slot_fragment(
        request,
        slot,
        f"„{clean_text}“ mit Schlüssel „{clean_value}“ gespeichert",
        highlight_choice_id=choice.id,
    )


@router.post(
    "/surveys/{survey_id}/slots/{slot_id}/choices/{choice_id}",
    response_class=HTMLResponse,
)
def update_choice(
    request: Request,
    survey_id: int,
    slot_id: int,
    choice_id: int,
    # Defaulted rather than required: an empty field should reach the check below
    # and come back as a readable sentence, not as a 422 from the framework.
    text: str = Form(""),
    value: str = Form(""),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """Edit a lecture in place.

    Renaming used to mean delete-and-recreate, which also dropped the ordering
    and, when responses already existed, silently orphaned them.
    """
    survey = get_survey(session, survey_id)
    slot = _get_slot(session, survey_id, slot_id)
    choice = session.get(SlotChoice, choice_id)
    if choice is None or choice.slot_id != slot.id:
        raise HTTPException(status_code=404, detail="Option nicht gefunden")

    clean_text = text.strip()
    clean_value = value.strip()
    if not clean_text or not clean_value:
        return _refused(request, "Titel und Schlüssel dürfen nicht leer sein.")
    if clean_value == slot.dna_value:
        return _refused(
            request, f"„{slot.dna_value}“ ist für „nicht teilgenommen“ reserviert."
        )
    if any(c.value == clean_value and c.id != choice.id for c in slot.choices):
        return _refused(request, f"Der Schlüssel „{clean_value}“ ist bereits vergeben.")

    old_value = choice.value
    choice.text = clean_text
    choice.value = clean_value

    migrated = 0
    if clean_value != old_value:
        migrated = _rename_lecture_in_responses(
            session, survey_id, slot.key_prefix, old_value, clean_value
        )

    _touch(survey)
    session.commit()

    message = f"„{clean_text}“ gespeichert, Schlüssel „{clean_value}“"
    if migrated:
        message += f" — {migrated} vorhandene Antworten mit umgestellt"
    return _saved(request, message)


@router.post(
    "/surveys/{survey_id}/slots/{slot_id}/choices/{choice_id}/delete",
    response_class=HTMLResponse,
)
def delete_choice(
    request: Request,
    survey_id: int,
    slot_id: int,
    choice_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    get_survey(session, survey_id)
    slot = _get_slot(session, survey_id, slot_id)
    choice = session.get(SlotChoice, choice_id)
    if choice is None or choice.slot_id != slot.id:
        raise HTTPException(status_code=404, detail="Option nicht gefunden")
    removed = choice.text
    session.delete(choice)
    _touch(session.get(Survey, survey_id))
    session.commit()
    session.refresh(slot)
    return _slot_fragment(request, slot, f"„{removed}“ entfernt")


@router.post("/surveys/{survey_id}/slots", response_class=HTMLResponse)
def add_slot(
    survey_id: int,
    key_prefix: str = Form(...),
    page_title: str = Form(...),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    survey = get_survey(session, survey_id)
    new_prefix = key_prefix.strip().lower()
    if not new_prefix.isidentifier():
        raise HTTPException(status_code=400, detail="Ungültiger Schlüssel.")
    if any(s.key_prefix == new_prefix for s in survey.slots):
        raise HTTPException(status_code=400, detail="Dieser Schlüssel ist bereits vergeben.")

    session.add(
        SurveySlot(
            survey_id=survey.id,
            key_prefix=new_prefix,
            page_title=page_title.strip(),
            position=max((s.position for s in survey.slots), default=-1) + 1,
        )
    )
    session.commit()
    return RedirectResponse(
        f"/surveys/{survey_id}/edit", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/surveys/{survey_id}/slots/{slot_id}/delete")
def delete_slot(
    survey_id: int,
    slot_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    get_survey(session, survey_id)
    slot = _get_slot(session, survey_id, slot_id)
    session.delete(slot)
    session.commit()
    return RedirectResponse(
        f"/surveys/{survey_id}/edit", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/surveys/{survey_id}/definition")
def survey_definition(
    survey_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """The generated SurveyJS JSON, for checking what participants will receive."""
    survey = get_survey(session, survey_id)
    return JSONResponse(build_definition(survey))


@router.get("/surveys/{survey_id}/preview", response_class=HTMLResponse)
def preview_form(
    request: Request,
    survey_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """Render the questionnaire exactly as participants see it, without saving."""
    survey = get_survey(session, survey_id)
    return templates.TemplateResponse(
        request,
        "participation/survey.html",
        {
            "survey": survey,
            "definition_url": f"/surveys/{survey.id}/definition",
            "submit_url": None,
            "is_preview": True,
        },
    )

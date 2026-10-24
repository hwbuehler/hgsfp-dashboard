"""Analysis jobs, report preview and export.

Preview and PDF render the same template from the same cached context, so the
two representations cannot drift apart.
"""

from __future__ import annotations

import threading

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.pdf import PdfUnavailable, build_zip, render_pdf
from app.analysis.pipeline import latest_completed_context, latest_run, run_analysis
from app.analysis.report import ReportContext, ReportSection, slugify
from app.config import settings
from app.db import SessionLocal, get_session
from app.models import AnalysisRun, AnalysisStatus, as_naive_utc
from app.models import Response as ResponseRow
from app.models import User
from app.routers.dashboard import get_survey
from app.security import require_user
from app.templating import templates

router = APIRouter(tags=["reports"])

# Guards the check-and-create below. Two clicks on "Auswertung starten" a moment
# apart both used to pass the "is one already running?" test and queue a job, so
# the box ended up loading the sentence-transformer twice and running two
# analyses against each other — while participants may still be submitting.
# The app runs a single worker by design (see app/main.py), so a process-level
# lock closes the window completely.
_start_lock = threading.Lock()

CLUSTER_NOTE = (
    "Grouping uses the all-MiniLM-L6-v2 sentence-transformer model "
    "(https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2). "
    "The count in brackets is the number of responses in a group."
)


@router.post("/surveys/{survey_id}/analysis")
def start_analysis(
    survey_id: int,
    background: BackgroundTasks,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    survey = get_survey(session, survey_id)

    with _start_lock:
        running = latest_run(session, survey_id)
        if running is not None and running.status in (
            AnalysisStatus.pending,
            AnalysisStatus.running,
        ):
            return RedirectResponse(
                f"/surveys/{survey_id}/report", status_code=status.HTTP_303_SEE_OTHER
            )

        run = AnalysisRun(survey_id=survey.id, status=AnalysisStatus.pending)
        session.add(run)
        session.commit()

    # Clustering loads a sentence-transformer and takes seconds; the request
    # must not wait for it.
    background.add_task(run_analysis, survey.id, run.id, SessionLocal)
    return RedirectResponse(
        f"/surveys/{survey_id}/report", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/surveys/{survey_id}/analysis/status", response_class=HTMLResponse)
def analysis_status(
    request: Request,
    survey_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """Polled by HTMX while a job runs."""
    return templates.TemplateResponse(
        request,
        "report/_status.html",
        {"survey_id": survey_id, "run": latest_run(session, survey_id)},
    )


@router.get("/surveys/{survey_id}/report", response_class=HTMLResponse)
def report_index(
    request: Request,
    survey_id: int,
    created: int | None = None,
    details_updated: int | None = None,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    survey = get_survey(session, survey_id)
    context = latest_completed_context(session, survey_id)
    count = session.scalar(
        select(func.count(ResponseRow.id)).where(ResponseRow.survey_id == survey_id)
    )

    # An edit after the last analysis leaves the cached report showing the old
    # lecture names and numbers, which is worse than showing nothing.
    run = latest_run(session, survey_id)
    edited_at = as_naive_utc(survey.updated_at)
    analysed_at = as_naive_utc(run.finished_at) if run else None
    is_stale = bool(
        context and analysed_at and edited_at and analysed_at < edited_at
    )
    return templates.TemplateResponse(
        request,
        "report/index.html",
        {
            "user": user,
            "survey": survey,
            "context": context,
            "run": run,
            "response_count": count or 0,
            "created": created,
            "details_updated": details_updated,
            "is_stale": is_stale,
        },
    )


def _require_context(session: Session, survey_id: int) -> ReportContext:
    context = latest_completed_context(session, survey_id)
    if context is None:
        raise HTTPException(
            status_code=409,
            detail="Für diese Umfrage liegt noch keine abgeschlossene Analyse vor.",
        )
    return context


def _sections_for(context: ReportContext, scope: str) -> list[ReportSection]:
    if scope == "all":
        return context.sections
    section = context.section(scope)
    if section is None:
        raise HTTPException(status_code=404, detail="Dieser Report-Abschnitt existiert nicht.")
    return [section]


def _render_document(
    request: Request,
    context: ReportContext,
    sections: list[ReportSection],
    scope: str,
    css_href: str,
    screen_actions: dict[str, str] | None = None,
) -> str:
    template = templates.get_template("report/document.html")
    return template.render(
        request=request,
        document_title=f"{context.survey_title} — {sections[0].title if len(sections) == 1 else 'Full Report'}",
        css_href=css_href,
        sections=sections,
        context=context,
        screen_actions=screen_actions,
        # Free-text pages and the collected comments belong to the survey as a
        # whole, not to one lecture.
        include_free_text=scope in ("all", "overall"),
        include_all_comments=scope in ("all", "overall"),
        include_summary=scope in ("all", "overall"),
        cluster_note=CLUSTER_NOTE,
    )


@router.get("/surveys/{survey_id}/report/{scope}", response_class=HTMLResponse)
def report_preview(
    request: Request,
    survey_id: int,
    scope: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    get_survey(session, survey_id)
    context = _require_context(session, survey_id)
    sections = _sections_for(context, scope)
    html = _render_document(
        request,
        context,
        sections,
        scope,
        "/static/css/report.css",
        screen_actions={
            "pdf_url": f"/surveys/{survey_id}/report/{scope}/pdf",
            "dashboard_url": "/dashboard",
            "analysis_url": f"/surveys/{survey_id}/report",
            "details_url": f"/surveys/{survey_id}/lectures",
            "report_pages": [
                {
                    "url": f"/surveys/{survey_id}/report/{section.scope}",
                    "label": (
                        f"{section.title} — {section.subtitle}"
                        if section.scope.startswith("lecture-") and section.subtitle
                        else section.title
                    ),
                }
                for section in context.sections
            ],
        },
    )
    return HTMLResponse(html)


@router.get("/surveys/{survey_id}/report/{scope}/pdf")
def report_pdf(
    request: Request,
    survey_id: int,
    scope: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    survey = get_survey(session, survey_id)
    context = _require_context(session, survey_id)
    sections = _sections_for(context, scope)
    html = _render_document(request, context, sections, scope, "css/report.css")

    try:
        pdf = render_pdf(html, base_url=str(settings.static_dir) + "/")
    except PdfUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    name = "full-report" if scope == "all" else scope
    filename = f"{slugify(survey.title)}-{name}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/surveys/{survey_id}/skill-course-pdf")
def skill_course_lecturer_pdf(
    request: Request,
    survey_id: int,
    lecturer: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """Render every skill-course lecture for one lecturer into one PDF."""
    survey = get_survey(session, survey_id)
    context = _require_context(session, survey_id)
    sections = [
        section
        for section in context.sections
        if section.scope.startswith("lecture-sc-") and section.title == lecturer
    ]
    if not sections:
        raise HTTPException(status_code=404, detail="No skill courses found for this lecturer.")
    html = _render_document(request, context, sections, "skill-courses", "css/report.css")
    try:
        pdf = render_pdf(html, base_url=str(settings.static_dir) + "/")
    except PdfUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    filename = f"{slugify(survey.title)}-{slugify(lecturer)}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/surveys/{survey_id}/reports.zip")
def report_zip(
    request: Request,
    survey_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """Every section as its own PDF, bundled."""
    survey = get_survey(session, survey_id)
    context = _require_context(session, survey_id)

    files: dict[str, bytes] = {}
    included_skill_lecturers: set[str] = set()
    try:
        for index, section in enumerate(context.sections):
            if section.scope.startswith("lecture-sc-"):
                if section.title in included_skill_lecturers:
                    continue
                included_skill_lecturers.add(section.title)
                bundled_sections = [
                    candidate
                    for candidate in context.sections
                    if candidate.scope.startswith("lecture-sc-")
                    and candidate.title == section.title
                ]
                name = f"{index:02d}-skill-courses-{slugify(section.title)}.pdf"
                html = _render_document(
                    request, context, bundled_sections, "skill-courses", "css/report.css"
                )
                files[name] = render_pdf(html, base_url=str(settings.static_dir) + "/")
                continue
            html = _render_document(
                request, context, [section], section.scope, "css/report.css"
            )
            # Numeric prefix keeps the archive in report order once extracted.
            files[f"{index:02d}-{section.scope}.pdf"] = render_pdf(
                html, base_url=str(settings.static_dir) + "/"
            )
    except PdfUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    filename = f"{slugify(survey.title)}-reports.zip"
    return Response(
        content=build_zip(files),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

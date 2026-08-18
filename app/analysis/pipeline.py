"""Run an analysis and cache the result.

Corresponds to _perform_automated_analysis in the original tool, minus every PDF
concern.  Statistics are cheap; clustering loads a sentence-transformer and takes
seconds, which is why this runs as a background job and writes its output to
AnalysisRun.result_json for the preview and the PDF export to read.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.aggregate import SlotConfig, aggregate
from app.analysis.clustering import ClusteredComments, ModelUnavailable, cluster_comments
from app.analysis.report import (
    ReportContext,
    build_comment_groups,
    build_sections,
    build_summary_rows,
)
from app.models import AnalysisRun, AnalysisStatus, Response, Survey, utcnow

logger = logging.getLogger(__name__)


def slot_configs(survey: Survey) -> list[SlotConfig]:
    return [
        SlotConfig(
            key_prefix=slot.key_prefix,
            page_title=slot.page_title,
            dna_value=slot.dna_value,
            choice_texts={c.value: c.text for c in slot.choices},
        )
        for slot in sorted(survey.slots, key=lambda s: s.position)
    ]


def build_context(survey: Survey, payloads: list[dict]) -> ReportContext:
    """Compute a full report context.  Runs the model; call off the request path."""
    slots = slot_configs(survey)
    aggregated = aggregate(payloads, slots)
    sections = build_sections(aggregated, slots)

    clustering_available = True
    clustering_note = ""
    try:
        organization = cluster_comments(aggregated.organization, use_semantic_split=False)
        # Topic suggestions are comma-separated lists far more often than not, so
        # they get split before clustering; organisation comments are prose.
        topics = cluster_comments(aggregated.topics, use_semantic_split=True)
    except ModelUnavailable as exc:
        logger.warning("Clustering skipped: %s", exc)
        clustering_available = False
        clustering_note = str(exc)
        organization = ClusteredComments(raw=aggregated.organization, clustered=[])
        topics = ClusteredComments(raw=aggregated.topics, clustered=[])

    return ReportContext(
        survey_title=survey.title,
        total_responses=aggregated.total_responses,
        sections=sections,
        organization=organization,
        topics=topics,
        summary_rows=build_summary_rows(sections),
        comment_groups=build_comment_groups(aggregated, slots),
        clustering_available=clustering_available,
        clustering_note=clustering_note,
    )


def run_analysis(survey_id: int, run_id: int, session_factory) -> None:
    """Background entry point.  Owns its own session — the request's is long gone."""
    with session_factory() as session:
        run = session.get(AnalysisRun, run_id)
        survey = session.get(Survey, survey_id)
        if run is None or survey is None:
            return

        run.status = AnalysisStatus.running
        session.commit()

        try:
            payloads = list(
                session.scalars(
                    select(Response.payload_json).where(Response.survey_id == survey_id)
                )
            )
            context = build_context(survey, payloads)
            run.result_json = context.to_dict()
            run.response_count = len(payloads)
            run.status = AnalysisStatus.done
            run.error_message = None
        except Exception as exc:  # noqa: BLE001 — surfaced to the dashboard
            logger.exception("Analysis failed for survey %s", survey_id)
            run.status = AnalysisStatus.failed
            run.error_message = f"{type(exc).__name__}: {exc}"
        finally:
            run.finished_at = utcnow()
            session.commit()


def latest_run(session: Session, survey_id: int) -> AnalysisRun | None:
    return session.scalars(
        select(AnalysisRun)
        .where(AnalysisRun.survey_id == survey_id)
        .order_by(AnalysisRun.id.desc())
        .limit(1)
    ).first()


def latest_completed_context(session: Session, survey_id: int) -> ReportContext | None:
    run = session.scalars(
        select(AnalysisRun)
        .where(
            AnalysisRun.survey_id == survey_id,
            AnalysisRun.status == AnalysisStatus.done,
        )
        .order_by(AnalysisRun.id.desc())
        .limit(1)
    ).first()
    if run is None or not run.result_json:
        return None
    return ReportContext.from_dict(run.result_json)

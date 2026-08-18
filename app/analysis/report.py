"""Assemble the data every report page needs.

One ReportContext feeds both the HTML preview and the PDF export, so the two can
never drift apart: they render the same template from the same object.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.analysis.aggregate import AggregatedResults, SlotConfig
from app.analysis.charts import legend_items, render_likert_bar
from app.analysis.clustering import ClusteredComments
from app.analysis.constants import CONSTANTS
from app.analysis.statistics import QuestionStats, compute_stats

OVERALL_SCOPE = "overall"


def slugify(value: str) -> str:
    """Filename- and URL-safe form of a lecture name (keeps umlauts readable)."""
    text = value.strip().lower()
    replacements = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "lecture"


@dataclass
class ReportSection:
    """One page of the report: a heading, six bars, and its comments."""

    scope: str
    title: str
    subtitle: str
    response_count: int
    dna_count: int
    stats: list[QuestionStats]
    comments: list[str] = field(default_factory=list)
    charts: dict[str, str] = field(default_factory=dict)

    @property
    def has_data(self) -> bool:
        return self.response_count > 0

    def legend_for(self, question: str) -> list[dict[str, str]]:
        return legend_items(question)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "title": self.title,
            "subtitle": self.subtitle,
            "response_count": self.response_count,
            "dna_count": self.dna_count,
            "stats": [s.to_dict() for s in self.stats],
            "comments": self.comments,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReportSection":
        stats = [QuestionStats.from_dict(s) for s in data["stats"]]
        section = cls(
            scope=data["scope"],
            title=data["title"],
            subtitle=data["subtitle"],
            response_count=data["response_count"],
            dna_count=data["dna_count"],
            stats=stats,
            comments=data.get("comments", []),
        )
        section.render_charts()
        return section

    def render_charts(self) -> None:
        self.charts = {s.question: render_likert_bar(s) for s in self.stats}


@dataclass
class ReportContext:
    """Everything the report templates read, for one survey."""

    survey_title: str
    total_responses: int
    sections: list[ReportSection]
    organization: ClusteredComments
    topics: ClusteredComments
    summary_rows: list[dict[str, Any]] = field(default_factory=list)
    # Every lecture's comments in one place, grouped by slot — the equivalent of
    # the old tool's comments_all_lectures.pdf.  Without it the comments are only
    # reachable by opening each lecture page in turn.
    comment_groups: list[dict[str, Any]] = field(default_factory=list)
    clustering_available: bool = True
    clustering_note: str = ""

    def section(self, scope: str) -> ReportSection | None:
        return next((s for s in self.sections if s.scope == scope), None)

    @property
    def question_titles(self) -> list[str]:
        return list(CONSTANTS.answer_titles)

    @property
    def total_lecture_comments(self) -> int:
        return sum(
            len(entry["comments"])
            for group in self.comment_groups
            for entry in group["lectures"]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "survey_title": self.survey_title,
            "total_responses": self.total_responses,
            "sections": [s.to_dict() for s in self.sections],
            "organization": self.organization.to_dict(),
            "topics": self.topics.to_dict(),
            "summary_rows": self.summary_rows,
            "comment_groups": self.comment_groups,
            "clustering_available": self.clustering_available,
            "clustering_note": self.clustering_note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReportContext":
        return cls(
            survey_title=data["survey_title"],
            total_responses=data["total_responses"],
            sections=[ReportSection.from_dict(s) for s in data["sections"]],
            organization=ClusteredComments.from_dict(data["organization"]),
            topics=ClusteredComments.from_dict(data["topics"]),
            summary_rows=data.get("summary_rows", []),
            comment_groups=data.get("comment_groups", []),
            clustering_available=data.get("clustering_available", True),
            clustering_note=data.get("clustering_note", ""),
        )


def lecture_scope(prefix: str, value: str) -> str:
    return f"lecture-{prefix}-{slugify(value)}"


def slot_scope(prefix: str) -> str:
    return f"slot-{prefix}"


def build_sections(
    aggregated: AggregatedResults, slots: list[SlotConfig]
) -> list[ReportSection]:
    """Overall first, then each slot's aggregate followed by its lectures."""
    sections: list[ReportSection] = [
        ReportSection(
            scope=OVERALL_SCOPE,
            title="Overall Results",
            subtitle="All lectures combined",
            response_count=aggregated.total_responses,
            dna_count=0,
            stats=compute_stats(aggregated.overall),
        )
    ]

    for slot in slots:
        slot_results = aggregated.by_slot.get(slot.key_prefix, {})
        sections.append(
            ReportSection(
                scope=slot_scope(slot.key_prefix),
                title=f"{slot.page_title} — All Lectures",
                subtitle="Every lecture in this slot combined",
                response_count=len(slot_results.get("interesting", [])),
                dna_count=aggregated.dna_counts.get(slot.key_prefix, 0),
                stats=compute_stats(slot_results),
            )
        )

        for value, results in sorted(aggregated.by_lecture.get(slot.key_prefix, {}).items()):
            sections.append(
                ReportSection(
                    scope=lecture_scope(slot.key_prefix, value),
                    title=value,
                    # The full lecture title is far more informative than the
                    # speaker key the result JSON stores.
                    subtitle=slot.choice_texts.get(value, slot.page_title),
                    response_count=len(results.get("interesting", [])),
                    dna_count=0,
                    stats=compute_stats(results),
                    comments=[c for c in results.get("comments", [])],
                )
            )

    for section in sections:
        section.render_charts()
    return sections


def build_comment_groups(
    aggregated: AggregatedResults, slots: list[SlotConfig]
) -> list[dict[str, Any]]:
    """Lecture comments grouped by slot, in report order.

    Lectures without comments are listed explicitly rather than omitted, so a
    reader can tell "nobody wrote anything" apart from "this is missing".
    """
    groups: list[dict[str, Any]] = []
    for slot in slots:
        lectures = [
            {
                "title": value,
                "subtitle": slot.choice_texts.get(value, ""),
                "scope": lecture_scope(slot.key_prefix, value),
                "comments": list(results.get("comments", [])),
            }
            for value, results in sorted(
                aggregated.by_lecture.get(slot.key_prefix, {}).items()
            )
        ]
        if lectures:
            groups.append({"title": slot.page_title, "lectures": lectures})
    return groups


def build_summary_rows(sections: list[ReportSection]) -> list[dict[str, Any]]:
    """One row per section for the overview table (mean ± std per question)."""
    return [
        {
            "title": section.title,
            "scope": section.scope,
            "n": section.response_count,
            "values": [s.formatted() for s in section.stats],
        }
        for section in sections
    ]

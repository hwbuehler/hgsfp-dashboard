"""Group raw survey responses by lecture.

Ported from _determine_lecture_titles / _fill_results_list / _create_overall_* in
hgsfp-survey-tool/survey_analyzer.py.  Two things changed:

* the hard-coded ml_/al_/il_ branches became a loop over a slot configuration, so
  the number of lecture slots is no longer baked into the code;
* missing keys are tolerated.  The original indexed ``entry["ml_title"]``
  directly, which raises on a partially filled response — something a web form
  can produce far more easily than a hand-curated export.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.analysis.constants import (
    CONSTANTS,
    LECTURE_COMMENTS_KEY,
    ORGANIZATION_KEY,
    TOPICS_KEY,
)


@dataclass(frozen=True)
class SlotConfig:
    """One lecture slot as the analysis needs to see it."""

    key_prefix: str
    page_title: str
    dna_value: str = "DnA"
    # value -> display text, used to label charts with the full lecture title.
    choice_texts: dict[str, str] = field(default_factory=dict)

    @property
    def comment_key(self) -> str:
        return f"{self.key_prefix}_comment"

    @property
    def title_key(self) -> str:
        return f"{self.key_prefix}_title"


def empty_lecture() -> dict[str, list]:
    return {key: [] for key in CONSTANTS.answ_keys}


@dataclass
class AggregatedResults:
    """Everything the statistics and clustering steps need, and nothing else."""

    # slot prefix -> lecture value -> question -> answers
    by_lecture: dict[str, dict[str, dict[str, list]]] = field(default_factory=dict)
    # slot prefix -> question -> answers (all lectures of that slot combined)
    by_slot: dict[str, dict[str, list]] = field(default_factory=dict)
    # question -> answers (everything combined)
    overall: dict[str, list] = field(default_factory=dict)
    # slot prefix -> number of respondents who did not attend
    dna_counts: dict[str, int] = field(default_factory=dict)
    organization: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    total_responses: int = 0


def _append_answers(
    target: dict[str, list],
    prefix: str,
    entry: dict[str, Any],
    comment_key: str,
) -> None:
    for key in CONSTANTS.question_keys:
        value = entry.get(f"{prefix}_{key}")
        if value is not None:
            target[key].append(value)

    comments = entry.get(LECTURE_COMMENTS_KEY)
    if isinstance(comments, dict):
        comment = comments.get(comment_key)
        # Lecture comments are full sentences and are never split apart —
        # only organisation and topic suggestions get segmented later.
        if is_meaningful_comment(comment):
            target["comments"].append(comment)


def is_meaningful_comment(comment: str | None) -> bool:
    """Filter out blanks and lone punctuation that people type to skip a field."""
    if not isinstance(comment, str):
        return False
    stripped = comment.strip()
    if not stripped:
        return False
    return stripped not in (".", "-", ",", ";", ":", "?", "!")


def determine_lecture_values(
    responses: list[dict[str, Any]], slots: list[SlotConfig]
) -> dict[str, list[str]]:
    """Which lectures actually appear in the data, per slot.

    Driven by the responses rather than the configured choices so that imported
    legacy data works even when its lectures are no longer in the dropdown.
    """
    found: dict[str, list[str]] = {}
    for slot in slots:
        seen: list[str] = []
        for entry in responses:
            value = entry.get(slot.title_key)
            if value and value != slot.dna_value and value not in seen:
                seen.append(value)
        found[slot.key_prefix] = sorted(seen)
    return found


def aggregate(
    responses: list[dict[str, Any]], slots: list[SlotConfig]
) -> AggregatedResults:
    result = AggregatedResults(total_responses=len(responses))
    lecture_values = determine_lecture_values(responses, slots)

    for slot in slots:
        result.by_lecture[slot.key_prefix] = {
            value: empty_lecture() for value in lecture_values[slot.key_prefix]
        }
        result.dna_counts[slot.key_prefix] = 0

    for entry in responses:
        for slot in slots:
            value = entry.get(slot.title_key)
            if value is None:
                continue
            if value == slot.dna_value:
                result.dna_counts[slot.key_prefix] += 1
                continue
            bucket = result.by_lecture[slot.key_prefix].get(value)
            if bucket is not None:
                _append_answers(bucket, slot.key_prefix, entry, slot.comment_key)

        organization = entry.get(ORGANIZATION_KEY)
        if is_meaningful_comment(organization):
            result.organization.append(organization)
        topics = entry.get(TOPICS_KEY)
        if is_meaningful_comment(topics):
            result.topics.append(topics)

    # Slot aggregates and the grand total. "comments" is deliberately left out:
    # aggregated lecture comments are reported per lecture, not per slot.
    for slot in slots:
        combined = {question: [] for question in CONSTANTS.question_keys}
        for lecture in result.by_lecture[slot.key_prefix].values():
            for question in CONSTANTS.question_keys:
                combined[question].extend(lecture[question])
        result.by_slot[slot.key_prefix] = combined

    result.overall = {question: [] for question in CONSTANTS.question_keys}
    for slot_results in result.by_slot.values():
        for question in CONSTANTS.question_keys:
            result.overall[question].extend(slot_results[question])

    return result

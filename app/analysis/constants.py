"""Question set and Likert scales.

Taken verbatim from SurveyConstants in hgsfp-survey-tool/survey_analyzer.py so
that reports keep producing the same numbers and wording as before.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SurveyConstants:
    answ_keys: tuple[str, ...] = (
        "interesting",
        "new",
        "expected",
        "exciting",
        "structure",
        "level",
        "comments",
    )
    labels: tuple[str, ...] = (
        "strongly disagree",
        "disagree",
        "neutral",
        "agree",
        "strongly agree",
    )
    labels_level: tuple[str, ...] = (
        "very difficult",
        "difficult",
        "balanced",
        "easy",
        "very easy",
    )
    answer_titles: tuple[str, ...] = (
        "The content was interesting",
        "The content was new to me",
        "The content was as expected",
        "The content was exciting",
        "The course was well-structured",
        "The level of the course was",
    )
    likert_palette: tuple[str, ...] = (
        "#B2182B",
        "#EF8A62",
        "#D9D9D9",
        "#67A9CF",
        "#2166AC",
    )

    @property
    def question_keys(self) -> tuple[str, ...]:
        """The rating questions, i.e. everything but the trailing "comments"."""
        return self.answ_keys[:-1]

    def labels_for(self, question: str) -> tuple[str, ...]:
        return self.labels_level if question == "level" else self.labels


CONSTANTS = SurveyConstants()

# Below this many responses the original tool refuses to report mean/std, on the
# grounds that the number would invite over-interpretation.
MIN_RESPONSES_FOR_STATISTICS = 5

# Keys of the free-text questions that are not tied to a single lecture.
ORGANIZATION_KEY = "sugg_organization"
TOPICS_KEY = "sugg_topics"
LECTURE_COMMENTS_KEY = "sugg_lectures"

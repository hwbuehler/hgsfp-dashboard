"""Mean, standard deviation and Likert distributions.

Ported from _calculate_lecture_statistics / _calculate_overall_statistics in
hgsfp-survey-tool/survey_analyzer.py.  The rules that produce the numbers are
unchanged: sample standard deviation (ddof=1), and no statistics at all when
five or fewer people answered a question.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.analysis.constants import CONSTANTS, MIN_RESPONSES_FOR_STATISTICS


@dataclass(frozen=True)
class QuestionStats:
    question: str
    title: str
    n: int
    mean: float | None
    std: float | None
    # Percentage per Likert value 1..5, always five entries.
    percentages: tuple[float, ...]
    counts: tuple[int, ...]
    labels: tuple[str, ...]

    @property
    def has_statistics(self) -> bool:
        return self.mean is not None

    def formatted(self) -> str:
        if self.mean is None or self.std is None:
            return "n/a"
        return f"{self.mean:.2f} ± {self.std:.2f}"

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "title": self.title,
            "n": self.n,
            "mean": self.mean,
            "std": self.std,
            "percentages": list(self.percentages),
            "counts": list(self.counts),
            "labels": list(self.labels),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "QuestionStats":
        return cls(
            question=data["question"],
            title=data["title"],
            n=data["n"],
            mean=data["mean"],
            std=data["std"],
            percentages=tuple(data["percentages"]),
            counts=tuple(data["counts"]),
            labels=tuple(data["labels"]),
        )


def compute_question_stats(question: str, answers: list[int]) -> QuestionStats:
    index = CONSTANTS.question_keys.index(question)
    n = len(answers)

    if n == 0:
        counts = (0, 0, 0, 0, 0)
        percentages = (0.0,) * 5
    else:
        # bincount rather than unique: a Likert value nobody picked must still
        # get a zero-width bar, otherwise colours and labels shift by one.
        raw = np.bincount(np.asarray(answers, dtype=int), minlength=6)[1:6]
        counts = tuple(int(c) for c in raw)
        percentages = tuple(float(p) for p in np.round((raw / n) * 100, decimals=1))

    if n > MIN_RESPONSES_FOR_STATISTICS:
        mean = float(np.mean(answers))
        std = float(np.std(answers, ddof=1))
    else:
        mean = None
        std = None

    return QuestionStats(
        question=question,
        title=CONSTANTS.answer_titles[index],
        n=n,
        mean=mean,
        std=std,
        percentages=percentages,
        counts=counts,
        labels=CONSTANTS.labels_for(question),
    )


def compute_stats(results: dict[str, list]) -> list[QuestionStats]:
    """Statistics for every rating question of one lecture or aggregate."""
    return [
        compute_question_stats(question, results.get(question, []))
        for question in CONSTANTS.question_keys
    ]

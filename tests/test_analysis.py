"""Prove the ported analysis returns exactly what the original tool returned.

tests/data/golden_statistics.json was produced by running
hgsfp-survey-tool/survey_analyzer.py against reference/sample-survey-results.json.
If any of these fail, the port has changed a number that colleagues rely on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.analysis.aggregate import aggregate, is_meaningful_comment
from app.analysis.constants import CONSTANTS
from app.analysis.statistics import compute_stats

GOLDEN_PATH = Path(__file__).parent / "data" / "golden_statistics.json"

if not GOLDEN_PATH.exists():
    # The fixture is the *original tool's* output, so it cannot be regenerated
    # from this code — doing so would make these tests assert that the port
    # matches itself.  Skip the module instead of failing at import time, which
    # would abort collection and take the whole suite down with it.
    pytest.skip(
        f"{GOLDEN_PATH.relative_to(Path(__file__).parent.parent)} is missing — "
        "produce it by running hgsfp-survey-tool/survey_analyzer.py against "
        "reference/sample-survey-results.json.",
        allow_module_level=True,
    )

GOLDEN = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _stats_by_question(results):
    return {s.question: s for s in compute_stats(results)}


def _all_lectures(aggregated):
    for prefix, lectures in aggregated.by_lecture.items():
        for name, results in lectures.items():
            yield prefix, name, results


def test_lectures_match_the_original(aggregated):
    found = {name for _, name, _ in _all_lectures(aggregated)}
    expected = {k for k in GOLDEN["statistics"] if not k.startswith("Overall")}
    assert found == expected


@pytest.mark.parametrize("question", CONSTANTS.question_keys)
def test_lecture_statistics_match_the_original(aggregated, question):
    for _, name, results in _all_lectures(aggregated):
        stat = _stats_by_question(results)[question]
        gold_mean, gold_std, gold_n = GOLDEN["statistics"][name][question]

        assert stat.n == gold_n, f"{name}/{question}: n"
        if gold_mean is None:
            assert stat.mean is None and stat.std is None
        else:
            assert stat.mean == pytest.approx(gold_mean, abs=1e-9), f"{name}/{question}: mean"
            assert stat.std == pytest.approx(gold_std, abs=1e-9), f"{name}/{question}: std"


@pytest.mark.parametrize(
    "scope,golden_key",
    [
        ("overall", "Overall Results"),
        ("ml", "Overall Morning Lecture Results"),
        ("al", "Overall Afternoon Lecture Results"),
    ],
)
def test_aggregate_statistics_match_the_original(aggregated, scope, golden_key):
    results = aggregated.overall if scope == "overall" else aggregated.by_slot[scope]
    stats = _stats_by_question(results)
    for question in CONSTANTS.question_keys:
        gold_mean, gold_std, gold_n = GOLDEN["statistics"][golden_key][question]
        assert stats[question].n == gold_n, f"{golden_key}/{question}: n"
        if gold_mean is None:
            assert stats[question].mean is None
        else:
            assert stats[question].mean == pytest.approx(gold_mean, abs=1e-9)
            assert stats[question].std == pytest.approx(gold_std, abs=1e-9)


def test_did_not_attend_counts_match_the_original(aggregated):
    assert aggregated.dna_counts == GOLDEN["dna"]


def test_lecture_comment_counts_match_the_original(aggregated):
    for prefix, name, results in _all_lectures(aggregated):
        assert len(results["comments"]) == GOLDEN["comment_counts"][prefix][name], name


def test_free_text_counts_match_the_original(aggregated):
    assert len(aggregated.organization) == GOLDEN["organization_count"]
    assert len(aggregated.topics) == GOLDEN["topics_count"]


def test_total_responses(aggregated):
    assert aggregated.total_responses == GOLDEN["overall_count"]


# --- Behaviour the original did not have -------------------------------------


def test_statistics_suppressed_at_or_below_five_responses(slot_configs):
    """The original refuses to report mean/std for n <= 5; so must the port."""
    responses = [
        {"ml_title": "Solo", "ml_interesting": 4, "al_title": "DnA", "il_title": "DnA"}
        for _ in range(5)
    ]
    result = aggregate(responses, slot_configs)
    stat = _stats_by_question(result.by_lecture["ml"]["Solo"])["interesting"]
    assert stat.n == 5
    assert stat.mean is None and stat.std is None

    result = aggregate(responses + [responses[0]], slot_configs)
    stat = _stats_by_question(result.by_lecture["ml"]["Solo"])["interesting"]
    assert stat.n == 6
    assert stat.mean == pytest.approx(4.0)


def test_partial_responses_do_not_crash(slot_configs):
    """A web form can submit a response with keys missing; the original raised KeyError."""
    responses = [
        {"ml_title": "Ollefs"},  # no ratings at all
        {"al_title": "Warmer", "al_interesting": 3},  # no ml_title
        {},  # entirely empty
    ]
    result = aggregate(responses, slot_configs)
    assert result.by_lecture["ml"]["Ollefs"]["interesting"] == []
    assert result.by_lecture["al"]["Warmer"]["interesting"] == [3]


def test_zero_value_likert_buckets_keep_their_position(slot_configs):
    """bincount, not unique: an unused rating must still get its own zero bar."""
    responses = [
        {"ml_title": "L", "ml_interesting": 5, "al_title": "DnA", "il_title": "DnA"}
        for _ in range(4)
    ]
    result = aggregate(responses, slot_configs)
    stat = _stats_by_question(result.by_lecture["ml"]["L"])["interesting"]
    assert stat.counts == (0, 0, 0, 0, 4)
    assert stat.percentages == (0.0, 0.0, 0.0, 0.0, 100.0)


@pytest.mark.parametrize(
    "text,expected",
    [("", False), ("   ", False), (".", False), ("-", False), (None, False), ("ok", True)],
)
def test_meaningless_comments_are_filtered(text, expected):
    assert is_meaningful_comment(text) is expected

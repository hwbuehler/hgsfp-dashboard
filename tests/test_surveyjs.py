"""The generated questionnaire must not lose anything from the hand-written one.

reference/surveyjs-original.json is the questionnaire as used for the 56th
Graduate Days.  Round-tripping it through blueprint extraction and generation
must reproduce it, apart from the normalisations listed in app/blueprint.py.
"""

from __future__ import annotations

import json

import pytest

from app.analysis.charts import render_likert_bar
from app.analysis.statistics import compute_question_stats
from app.blueprint import strip_locales
from app.models import SlotChoice, Survey, SurveySlot, SurveyTemplate
from app.surveyjs import ValidationProblem, build_definition, validate_payload

# Keys the extraction deliberately drops or rewrites; see EXTRACTION_NOTES.
NORMALISED_KEYS = {"noneText", "logo"}


def _survey_from_blueprint(blueprint: dict, title: str) -> Survey:
    """A detached object graph — no database needed for definition generation."""
    survey = Survey(id=1, title=title, public_token="t", template_id=1)
    survey.template = SurveyTemplate(id=1, name="test", blueprint_json=blueprint)
    survey.slots = [
        SurveySlot(
            id=index + 1,
            survey_id=1,
            key_prefix=spec["key_prefix"],
            page_title=spec["page_title"],
            position=index,
            dna_value=spec["dna_value"],
            dna_text=spec["dna_text"],
            choices=[
                SlotChoice(id=0, slot_id=index + 1, value=c["value"], text=c["text"], position=i)
                for i, c in enumerate(spec["choices"])
            ],
        )
        for index, spec in enumerate(blueprint["slots"])
    ]
    return survey


@pytest.fixture
def regenerated(blueprint) -> dict:
    return build_definition(_survey_from_blueprint(blueprint, blueprint["title"]))


def _strip(node):
    """Remove the keys the extraction intentionally normalises."""
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items() if k not in NORMALISED_KEYS}
    if isinstance(node, list):
        return [_strip(item) for item in node]
    return node


def test_page_structure_round_trips(regenerated, reference_definition):
    original = strip_locales(reference_definition)
    assert [p["name"] for p in regenerated["pages"]] == [
        p["name"] for p in original["pages"]
    ]


def test_questions_round_trip(regenerated, reference_definition):
    """Every question keeps its name, type, wording, scale and visibility rule."""
    original = _strip(strip_locales(reference_definition))
    produced = _strip(regenerated)

    for original_page, produced_page in zip(original["pages"], produced["pages"]):
        original_elements = {e["name"]: e for e in original_page["elements"]}
        produced_elements = {e["name"]: e for e in produced_page["elements"]}
        assert original_elements.keys() == produced_elements.keys()

        for name, original_element in original_elements.items():
            produced_element = produced_elements[name]
            for key, value in original_element.items():
                # Both are unset on a handful of questions in the original and are
                # normalised on extraction; covered by their own tests below.
                if key in ("isRequired", "displayMode"):
                    continue
                assert produced_element.get(key) == value, f"{name}.{key}"


def test_rating_questions_are_uniformly_configured(regenerated):
    """The original left isRequired off ml_level and displayMode off ml_exciting /
    ml_structure, so the same question rendered differently depending on the page."""
    for page in regenerated["pages"][:-1]:
        ratings = [e for e in page["elements"] if e.get("type") == "rating"]
        assert len(ratings) == 6
        for element in ratings:
            assert element["isRequired"] is True, element["name"]
            assert element["displayMode"] == "buttons", element["name"]


def test_root_options_round_trip(regenerated, reference_definition):
    original = _strip(strip_locales(reference_definition))
    for key, value in original.items():
        if key in ("pages", "title", "completedHtml"):
            continue
        assert regenerated[key] == value, key


def test_no_translation_objects_remain(regenerated):
    assert '"de"' not in json.dumps(regenerated, ensure_ascii=False)


def test_logo_is_self_hosted(regenerated):
    """An external logo URL would leak participant IPs to a third-party host."""
    assert regenerated["logo"].startswith("/static/")


def test_completed_message_spacing_repaired(regenerated):
    assert "!You" not in regenerated["completedHtml"]


def test_did_not_attend_is_appended_not_stored(blueprint, regenerated):
    for slot_spec, page in zip(blueprint["slots"], regenerated["pages"]):
        stored = [c["value"] for c in slot_spec["choices"]]
        rendered = [c["value"] for c in page["elements"][0]["choices"]]
        assert slot_spec["dna_value"] not in stored
        assert rendered == stored + [slot_spec["dna_value"]]


def test_comment_items_follow_the_slots(blueprint, regenerated):
    sugg = regenerated["pages"][-1]["elements"][0]
    assert [item["name"] for item in sugg["items"]] == [
        f"{s['key_prefix']}_comment" for s in blueprint["slots"]
    ]


def test_extra_slot_produces_an_extra_page(blueprint):
    """The slot list is configurable — three is the current case, not a limit."""
    survey = _survey_from_blueprint(blueprint, "Four slots")
    survey.slots.append(
        SurveySlot(
            id=99,
            survey_id=1,
            key_prefix="el",
            page_title="Evening Lecture",
            position=3,
            choices=[SlotChoice(id=0, slot_id=99, value="Zimmer", text="Evening talk", position=0)],
        )
    )
    definition = build_definition(survey)
    assert [p["name"] for p in definition["pages"]] == ["ml", "al", "il", "el", "sugg"]
    evening = definition["pages"][3]
    assert evening["elements"][1]["visibleIf"] == "{el_title} != 'DnA'"
    assert [e["name"] for e in evening["elements"]][:2] == ["el_title", "el_interesting"]


# --- Response validation ------------------------------------------------------


@pytest.fixture
def survey(blueprint) -> Survey:
    return _survey_from_blueprint(blueprint, "test")


def test_valid_payload_passes_through(survey):
    payload = {
        "ml_title": "Ollefs",
        "ml_interesting": 5,
        "ml_level": 3,
        "al_title": "DnA",
        "il_title": "DnA",
        "sugg_lectures": {"ml_comment": "Good"},
        "sugg_topics": "Optics",
    }
    cleaned = validate_payload(survey, payload)
    assert cleaned["ml_interesting"] == 5
    assert cleaned["sugg_lectures"] == {"ml_comment": "Good"}


def test_unknown_lecture_rejected(survey):
    with pytest.raises(ValidationProblem):
        validate_payload(survey, {"ml_title": "Nobody"})


@pytest.mark.parametrize("value", [0, 6, -1, "high"])
def test_out_of_range_rating_rejected(survey, value):
    with pytest.raises(ValidationProblem):
        validate_payload(survey, {"ml_title": "Ollefs", "ml_interesting": value})


def test_unknown_fields_are_dropped(survey):
    cleaned = validate_payload(
        survey,
        {"ml_title": "Ollefs", "tracking_id": "abc", "HappendAt": "/Date(1)/"},
    )
    assert set(cleaned) == {"ml_title"}


def test_ratings_ignored_for_non_attendees(survey):
    cleaned = validate_payload(survey, {"ml_title": "DnA", "ml_interesting": 5})
    assert cleaned == {"ml_title": "DnA"}


def test_empty_payload_rejected(survey):
    with pytest.raises(ValidationProblem):
        validate_payload(survey, {})


# --- Charts -------------------------------------------------------------------


def test_chart_renders_every_non_zero_segment():
    stats = compute_question_stats("interesting", [1, 2, 3, 4, 5, 5])
    svg = render_likert_bar(stats)
    assert svg.count("<path") == 5
    assert 'role="img"' in svg and "aria-label" in svg


def test_chart_omits_zero_width_segments():
    stats = compute_question_stats("interesting", [5] * 10)
    svg = render_likert_bar(stats)
    assert svg.count("<path") == 1


def test_chart_handles_no_responses():
    stats = compute_question_stats("interesting", [])
    svg = render_likert_bar(stats)
    assert "no responses" in svg


def test_chart_does_not_distort_text():
    """preserveAspectRatio="none" would stretch the labels; it must stay off."""
    stats = compute_question_stats("interesting", [3, 4, 5, 5, 4, 3])
    assert "preserveAspectRatio" not in render_likert_bar(stats)

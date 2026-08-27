"""Build a SurveyJS definition from a template blueprint plus a survey's slots.

The blueprint (see app/blueprint.py) carries everything that never changes
between runs.  This module clones its lecture page once per slot, substitutes
the key prefix and the dropdown choices, and rebuilds the per-slot comment
fields on the suggestions page.
"""

from __future__ import annotations

import copy
from typing import Any

from app.blueprint import LECTURE_COMMENTS_QUESTION, PLACEHOLDER
from app.models import Survey, SurveySlot


def _substitute(node: Any, prefix: str) -> Any:
    if isinstance(node, dict):
        return {key: _substitute(value, prefix) for key, value in node.items()}
    if isinstance(node, list):
        return [_substitute(item, prefix) for item in node]
    if isinstance(node, str):
        return node.replace(PLACEHOLDER, prefix)
    return node


def slot_choices(slot: SurveySlot) -> list[dict[str, str]]:
    """Dropdown choices for one slot, with "did not attend" appended."""
    choices = [{"value": c.value, "text": c.text} for c in slot.choices]
    choices.append({"value": slot.dna_value, "text": slot.dna_text})
    return choices


def build_slot_page(blueprint: dict[str, Any], slot: SurveySlot) -> dict[str, Any]:
    page = _substitute(copy.deepcopy(blueprint["slot_page"]), slot.key_prefix)
    page["title"] = slot.page_title
    for element in page["elements"]:
        if element.get("name") == f"{slot.key_prefix}_title":
            element["choices"] = slot_choices(slot)
    return page


def build_sugg_page(blueprint: dict[str, Any], slots: list[SurveySlot]) -> dict[str, Any]:
    page = copy.deepcopy(blueprint["sugg_page"])
    for element in page.get("elements", []):
        if element.get("name") == LECTURE_COMMENTS_QUESTION:
            element["items"] = [
                {"name": slot.comment_item_name, "title": slot.page_title}
                for slot in slots
            ]
    return page


def build_definition(survey: Survey) -> dict[str, Any]:
    """Full SurveyJS JSON for a survey, ready to hand to the runtime."""
    blueprint = survey.template.blueprint_json
    slots = sorted(survey.slots, key=lambda s: s.position)

    definition: dict[str, Any] = copy.deepcopy(blueprint["root"])
    definition["title"] = survey.title
    definition["pages"] = [build_slot_page(blueprint, slot) for slot in slots]
    definition["pages"].append(build_sugg_page(blueprint, slots))
    return definition


class ValidationProblem(Exception):
    """Raised when a survey is not fit to be published."""


def validate_for_publish(survey: Survey) -> list[str]:
    """Return human-readable reasons why the survey cannot go live (German UI)."""
    problems: list[str] = []

    if not survey.title.strip():
        problems.append("Die Umfrage hat keinen Titel.")
    if not survey.slots:
        problems.append("Die Umfrage hat keine Vorlesungs-Slots.")

    seen_prefixes: set[str] = set()
    for slot in survey.slots:
        if not slot.choices:
            problems.append(f"„{slot.page_title}“ hat keine Auswahlmöglichkeit.")
        if slot.key_prefix in seen_prefixes:
            problems.append(f"Der Schlüssel „{slot.key_prefix}“ wird mehrfach verwendet.")
        seen_prefixes.add(slot.key_prefix)

        seen_values: set[str] = set()
        for choice in slot.choices:
            if not choice.value.strip():
                problems.append(f"„{slot.page_title}“ enthält eine Option ohne Schlüssel.")
            elif choice.value in seen_values:
                problems.append(
                    f"„{slot.page_title}“ verwendet den Schlüssel „{choice.value}“ mehrfach."
                )
            seen_values.add(choice.value)
            if choice.value == slot.dna_value:
                problems.append(
                    f"„{slot.page_title}“ verwendet „{slot.dna_value}“ als Schlüssel — "
                    "dieser Wert ist für „nicht teilgenommen“ reserviert."
                )

    return problems


# --- Response validation -------------------------------------------------

RATING_KEYS = ("interesting", "new", "expected", "exciting", "structure", "level")
FREE_TEXT_KEYS = ("sugg_organization", "sugg_topics")

# A comment box, not an essay field. Real answers run to a couple of hundred
# characters; the cap is here because the submission endpoint is public, and an
# unbounded string ends up both in the database and in every later analysis run.
MAX_TEXT_LENGTH = 4000


def _rating_value(raw: Any, key: str) -> int:
    """Coerce a submitted rating to 1-5, refusing anything that is not exactly that.

    ``int()`` is too forgiving on its own: it turns ``True`` into a 1 and 4.9
    into a 4, so a malformed client would silently contribute real-looking
    ratings to the statistics.
    """
    if isinstance(raw, bool):
        raise ValidationProblem(f"Ungültiger Wert für {key}.")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationProblem(f"Ungültiger Wert für {key}.") from None
    if isinstance(raw, float) and value != raw:
        raise ValidationProblem(f"Ungültiger Wert für {key}.")
    if not 1 <= value <= 5:
        raise ValidationProblem(f"Wert für {key} liegt außerhalb 1–5.")
    return value


def _free_text(raw: str, key: str) -> str:
    if len(raw) > MAX_TEXT_LENGTH:
        raise ValidationProblem(
            f"Der Text bei {key} ist zu lang (höchstens {MAX_TEXT_LENGTH} Zeichen)."
        )
    return raw


def validate_payload(survey: Survey, payload: dict[str, Any]) -> dict[str, Any]:
    """Reduce a submitted SurveyJS result to the fields we expect and trust.

    Client-side validation is not a security boundary, so anything unknown is
    dropped rather than stored.  Raises ValidationProblem on malformed input.
    """
    if not isinstance(payload, dict):
        raise ValidationProblem("Antwort hat kein gültiges Format.")

    slots = {slot.key_prefix: slot for slot in survey.slots}
    cleaned: dict[str, Any] = {}

    for prefix, slot in slots.items():
        title_key = f"{prefix}_title"
        title = payload.get(title_key)
        if title is None:
            continue
        allowed = {c.value for c in slot.choices} | {slot.dna_value}
        if title not in allowed:
            raise ValidationProblem(f"Unbekannte Auswahl für {title_key}.")
        cleaned[title_key] = title

        if title == slot.dna_value:
            continue  # ratings are hidden for non-attendees

        for key in RATING_KEYS:
            raw = payload.get(f"{prefix}_{key}")
            if raw is None:
                continue
            cleaned[f"{prefix}_{key}"] = _rating_value(raw, f"{prefix}_{key}")

    comments = payload.get(LECTURE_COMMENTS_QUESTION)
    if isinstance(comments, dict):
        allowed_items = {slot.comment_item_name for slot in survey.slots}
        kept = {
            name: _free_text(text, name)
            for name, text in comments.items()
            if name in allowed_items and isinstance(text, str) and text.strip()
        }
        if kept:
            cleaned[LECTURE_COMMENTS_QUESTION] = kept

    for key in FREE_TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            cleaned[key] = _free_text(value, key)

    if not cleaned:
        raise ValidationProblem("Die Antwort enthält keine auswertbaren Angaben.")
    return cleaned

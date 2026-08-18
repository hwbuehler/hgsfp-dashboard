"""Turn the hand-written SurveyJS definition into a reusable blueprint.

``reference/surveyjs-original.json`` is the questionnaire as it was authored for
the 56th Graduate Days.  Everything in it is fixed from run to run except the
survey title and the dropdown choices, so this module strips it down to:

* ``root``       — top-level options (progress bar, width, logo, …)
* ``slot_page``  — one lecture page with ``{prefix}`` placeholders
* ``sugg_page``  — the suggestions page, minus its per-slot comment items
* ``slots``      — the slot configuration read out of the original file

The extraction also normalises a few inconsistencies in the original, see
``EXTRACTION_NOTES``.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

# The questionnaire is English-only, so localisation objects collapse to their
# "default" string.
LOCALE_DEFAULT_KEY = "default"

# Key of the multipletext question holding one free-text comment per slot.
LECTURE_COMMENTS_QUESTION = "sugg_lectures"

# Local copy of the HGSFP logo.  The original points at
# https://hgsfp.uni-heidelberg.de/... — loading it from the participant form
# would send every participant's IP to a third-party host.
LOCAL_LOGO_PATH = "/static/img/hgsfp-logo.svg"

# Every rating question is meant to render as a button row.
RATING_DISPLAY_MODE = "buttons"

EXTRACTION_NOTES = """\
Deliberate differences from reference/surveyjs-original.json:
  * "de" translation objects collapsed to their "default" string (form is English).
  * "noneText" dropped — set inconsistently ("did not attend" vs "None") and
    inert without "showNoneItem".
  * "isRequired" set on every rating question; the original omits it on ml_level.
  * "displayMode": "buttons" set on every rating question; the original omits it on
    ml_exciting and ml_structure, so those two rendered as a star widget while the
    same questions on the afternoon and industry pages rendered as buttons.
  * "completedHtml" missing space after "survey!" repaired.
  * "logo" repointed at the self-hosted copy under /static/img/.
"""

PLACEHOLDER = "{prefix}"


def strip_locales(node: Any) -> Any:
    """Recursively replace ``{"default": x, "de": y}`` objects with ``x``."""
    if isinstance(node, dict):
        if LOCALE_DEFAULT_KEY in node and all(
            isinstance(k, str) and isinstance(v, str) for k, v in node.items()
        ):
            return node[LOCALE_DEFAULT_KEY]
        return {key: strip_locales(value) for key, value in node.items()}
    if isinstance(node, list):
        return [strip_locales(item) for item in node]
    return node


def _placeholderise(node: Any, prefix: str) -> Any:
    """Replace a concrete slot prefix (``ml``) with ``{prefix}`` in names and expressions."""
    if isinstance(node, dict):
        return {key: _placeholderise(value, prefix) for key, value in node.items()}
    if isinstance(node, list):
        return [_placeholderise(item, prefix) for item in node]
    if isinstance(node, str):
        # Only rewrite whole identifiers: "ml_title" -> "{prefix}_title", while
        # leaving prose such as "normally" untouched.
        return re.sub(rf"\b{re.escape(prefix)}_(\w+)", rf"{PLACEHOLDER}_\1", node)
    return node


def _is_slot_page(page: dict[str, Any]) -> bool:
    """A lecture page is one whose first element is the ``<prefix>_title`` dropdown."""
    elements = page.get("elements") or []
    if not elements:
        return False
    first = elements[0]
    return first.get("type") == "dropdown" and first.get("name", "").endswith("_title")


def extract_blueprint(definition: dict[str, Any]) -> dict[str, Any]:
    """Split a concrete SurveyJS definition into blueprint + slot configuration."""
    definition = strip_locales(copy.deepcopy(definition))

    root = {k: v for k, v in definition.items() if k not in ("pages", "title")}
    root["logo"] = LOCAL_LOGO_PATH
    if isinstance(root.get("completedHtml"), str):
        root["completedHtml"] = root["completedHtml"].replace("!You", "! You")

    slot_pages: list[dict[str, Any]] = []
    slots: list[dict[str, Any]] = []
    sugg_page: dict[str, Any] | None = None

    for page in definition.get("pages", []):
        if _is_slot_page(page):
            slot_pages.append(page)
        elif sugg_page is None:
            sugg_page = page

    if not slot_pages:
        raise ValueError("no lecture pages found — is this a Graduate Days questionnaire?")
    if sugg_page is None:
        raise ValueError("no suggestions page found in the definition")

    for page in slot_pages:
        dropdown = page["elements"][0]
        prefix = dropdown["name"].removesuffix("_title")
        dna_value, dna_text = _find_dna_choice(dropdown.get("choices", []))
        slots.append(
            {
                "key_prefix": prefix,
                "page_title": page.get("title", prefix),
                "dna_value": dna_value,
                "dna_text": dna_text,
                "choices": [
                    {"value": c["value"], "text": c.get("text", c["value"])}
                    for c in dropdown.get("choices", [])
                    if c.get("value") != dna_value
                ],
            }
        )

    # Every lecture page carries the same questions, so the first one is the
    # blueprint for all of them.
    template_page = _normalise_slot_page(slot_pages[0], slots[0]["key_prefix"])
    sugg_template = _normalise_sugg_page(sugg_page)

    return {
        "root": root,
        "slot_page": template_page,
        "sugg_page": sugg_template,
        "slots": slots,
        "title": definition.get("title", ""),
    }


def _find_dna_choice(choices: list[dict[str, Any]]) -> tuple[str, str]:
    """Identify the "did not attend" entry, which the analyzer treats specially."""
    for choice in choices:
        if choice.get("value") == "DnA":
            return "DnA", choice.get("text", "did not attend")
    # Fall back to the last entry rather than guessing a value that is not there.
    if choices:
        last = choices[-1]
        return last["value"], last.get("text", last["value"])
    return "DnA", "did not attend"


def _normalise_slot_page(page: dict[str, Any], prefix: str) -> dict[str, Any]:
    page = _placeholderise(copy.deepcopy(page), prefix)
    page["name"] = PLACEHOLDER
    page.pop("title", None)  # supplied per slot

    for element in page["elements"]:
        element.pop("noneText", None)
        if element.get("type") == "dropdown":
            element.pop("choices", None)  # supplied per slot
        elif element.get("type") == "rating":
            # The original leaves these unset on a few questions only, which makes
            # the same question look and behave differently between pages.
            element["isRequired"] = True
            element["displayMode"] = RATING_DISPLAY_MODE
    return page


def _normalise_sugg_page(page: dict[str, Any]) -> dict[str, Any]:
    page = copy.deepcopy(page)
    for element in page.get("elements", []):
        if element.get("name") == LECTURE_COMMENTS_QUESTION:
            element.pop("items", None)  # rebuilt from the slot list
    return page


def load_reference_blueprint(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return extract_blueprint(json.load(handle))

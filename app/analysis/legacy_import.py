"""Import result files produced by the previous survey platform.

The old export format is ``{"ResultCount": n, "Data": [ {...}, ... ]}`` with the
same flat keys the dashboard stores, so importing is mostly a matter of dropping
the operational fields.  ``HappendAt`` (sic) and ``InstanceId`` are deliberately
discarded: an exact submission timestamp is exactly the kind of metadata the
anonymity promise rules out, and it is not needed for any report.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import Response, Survey, utcnow

DISCARDED_KEYS = frozenset({"HappendAt", "HappenedAt", "InstanceId"})

# Some survey forms used a different prefix for the skill-course comment field
# than for the rating fields (``sk_comment`` vs ``sc_title``).  The analysis
# looks for ``{prefix}_comment`` where ``prefix`` comes from ``{prefix}_title``,
# so the mismatched comment keys are renamed on import to keep the comments.
COMMENT_KEY_RENAMES = {"sk_comment": "sc_comment"}


class ImportError_(ValueError):
    """Raised when a file does not look like a survey export."""


def _normalise_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Drop operational metadata and fix known comment-key mismatches."""
    cleaned = {k: v for k, v in entry.items() if k not in DISCARDED_KEYS}
    comments = cleaned.get("sugg_lectures")
    if isinstance(comments, dict):
        renamed = {
            COMMENT_KEY_RENAMES.get(k, k): v for k, v in comments.items()
        }
        if renamed != comments:
            cleaned["sugg_lectures"] = renamed
    return cleaned


def parse_export(raw: str | bytes) -> list[dict[str, Any]]:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ImportError_(f"Die Datei ist kein gültiges JSON: {exc}") from exc

    if not isinstance(document, dict) or "Data" not in document:
        raise ImportError_('Die Datei enthält kein "Data"-Feld.')

    entries = document["Data"]
    if not isinstance(entries, list):
        raise ImportError_('"Data" muss eine Liste von Antworten sein.')

    cleaned: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cleaned.append(_normalise_entry(entry))
    if not cleaned:
        raise ImportError_("Die Datei enthält keine Antworten.")
    return cleaned


def derive_slot_choices(
    payloads: list[dict[str, Any]], slot_prefixes: list[str], dna_value: str = "DnA"
) -> dict[str, list[str]]:
    """Which lectures actually occur in an export, per slot prefix.

    Used when a survey is created from a results file: seeding the dropdowns from
    the template would list last run's lectures, which do not match the data.
    """
    found: dict[str, list[str]] = {}
    for prefix in slot_prefixes:
        seen: list[str] = []
        for entry in payloads:
            value = entry.get(f"{prefix}_title")
            if isinstance(value, str) and value and value != dna_value and value not in seen:
                seen.append(value)
        found[prefix] = sorted(seen)
    return found


def derive_slots(payloads: list[dict[str, Any]], dna_value: str = "DnA") -> list[dict[str, Any]]:
    """Discover analysis slots from SurveyJS ``*_title`` fields.

    Result exports can contain new course types (such as ``sc_``) without a
    dashboard template update. Keep the stored key prefix as the slot identity.
    """
    found = {
        key[:-6] for payload in payloads for key in payload
        if key.endswith("_title") and len(key) > 6
    }
    known_order = {"ml": 0, "al": 1, "sc": 2, "il": 3}
    prefixes = sorted(found, key=lambda prefix: (known_order.get(prefix, 99), prefix))
    if not prefixes:
        raise ImportError_('Die Datei enthält keine Felder im Format "*_title".')
    return [
        {
            "key_prefix": prefix,
            "page_title": {
                "ml": "Morning Lectures",
                "al": "Afternoon Lectures",
                "il": "Industry Lectures",
                "sc": "Skill Courses",
            }.get(prefix, prefix.upper()),
            "choices": derive_slot_choices(payloads, [prefix], dna_value)[prefix],
        }
        for prefix in prefixes
    ]


def import_into(session: Session, survey: Survey, raw: str | bytes) -> int:
    """Append the file's responses to a survey.  Returns how many were stored."""
    payloads = parse_export(raw)
    today = utcnow().date()
    session.add_all(
        Response(survey_id=survey.id, payload_json=payload, submitted_on=today)
        for payload in payloads
    )
    session.commit()
    return len(payloads)

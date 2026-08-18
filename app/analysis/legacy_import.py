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


class ImportError_(ValueError):
    """Raised when a file does not look like a survey export."""


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
        cleaned.append({k: v for k, v in entry.items() if k not in DISCARDED_KEYS})
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

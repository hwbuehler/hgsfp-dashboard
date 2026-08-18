"""Seed the survey template from the reference SurveyJS definition.

Usage:
    python scripts/bootstrap_template.py [--force]

Reads reference/surveyjs-original.json, extracts the reusable blueprint and
stores it as the "Graduate Days Feedback" template.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.blueprint import EXTRACTION_NOTES, load_reference_blueprint  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.models import Base, SurveyTemplate  # noqa: E402

TEMPLATE_NAME = "Graduate Days Feedback"
REFERENCE_FILE = "surveyjs-original.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true", help="overwrite an existing template"
    )
    args = parser.parse_args()

    source = settings.reference_dir / REFERENCE_FILE
    if not source.exists():
        print(f"Missing {source}", file=sys.stderr)
        return 1

    Base.metadata.create_all(engine)
    blueprint = load_reference_blueprint(source)

    with SessionLocal() as session:
        existing = session.scalars(
            select(SurveyTemplate).where(SurveyTemplate.name == TEMPLATE_NAME)
        ).first()

        if existing and not args.force:
            print(f'Template "{TEMPLATE_NAME}" already exists — use --force to replace.')
            return 0

        if existing:
            existing.blueprint_json = blueprint
            print(f'Updated template "{TEMPLATE_NAME}".')
        else:
            session.add(
                SurveyTemplate(
                    name=TEMPLATE_NAME,
                    description=(
                        "Feedback questionnaire for the Heidelberg Physics Graduate "
                        "Days: one page per lecture slot plus a suggestions page."
                    ),
                    blueprint_json=blueprint,
                )
            )
            print(f'Created template "{TEMPLATE_NAME}".')
        session.commit()

    slots = ", ".join(s["key_prefix"] for s in blueprint["slots"])
    print(f"Slots: {slots}")
    print()
    print(EXTRACTION_NOTES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Point the app at a throwaway database before anything imports app.config.
os.environ.setdefault("HGSFP_SECRET_KEY", "test-secret-key-not-used-in-production")
os.environ.setdefault("HGSFP_DATABASE_PATH", "data/test.db")


@pytest.fixture(scope="session")
def reference_definition() -> dict:
    path = PROJECT_ROOT / "reference" / "surveyjs-original.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def sample_responses() -> list[dict]:
    path = PROJECT_ROOT / "reference" / "sample-survey-results.json"
    return json.loads(path.read_text(encoding="utf-8"))["Data"]


@pytest.fixture(scope="session")
def blueprint(reference_definition) -> dict:
    from app.blueprint import extract_blueprint

    return extract_blueprint(reference_definition)


@pytest.fixture(scope="session")
def slot_configs(blueprint):
    from app.analysis.aggregate import SlotConfig

    return [
        SlotConfig(
            key_prefix=spec["key_prefix"],
            page_title=spec["page_title"],
            dna_value=spec["dna_value"],
            choice_texts={c["value"]: c["text"] for c in spec["choices"]},
        )
        for spec in blueprint["slots"]
    ]


@pytest.fixture(scope="session")
def aggregated(sample_responses, slot_configs):
    from app.analysis.aggregate import aggregate

    return aggregate(sample_responses, slot_configs)

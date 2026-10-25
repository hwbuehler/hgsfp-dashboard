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
def sample_responses() -> list[dict]:
    path = PROJECT_ROOT / "reference" / "sample-survey-results.json"
    return json.loads(path.read_text(encoding="utf-8"))["Data"]


@pytest.fixture(scope="session")
def slot_configs() -> list:
    """Slot configuration matching the reference sample data (ml, al, il)."""
    from app.analysis.aggregate import SlotConfig
    from app.analysis.legacy_import import derive_slots

    with open(PROJECT_ROOT / "reference" / "sample-survey-results.json", encoding="utf-8") as f:
        payloads = json.load(f)["Data"]
    specs = derive_slots(payloads)
    return [
        SlotConfig(
            key_prefix=spec["key_prefix"],
            page_title=spec["page_title"],
            dna_value="DnA",
            choice_texts={c: c for c in spec["choices"]},
        )
        for spec in specs
    ]


@pytest.fixture(scope="session")
def aggregated(sample_responses, slot_configs):
    from app.analysis.aggregate import aggregate

    return aggregate(sample_responses, slot_configs)

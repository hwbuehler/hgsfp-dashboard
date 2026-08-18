"""Put the sentence-transformer used for comment clustering in place.

Prefers copying from a local checkout of hgsfp-survey-tool (no download), and
falls back to fetching from Hugging Face.

Usage:
    python scripts/fetch_model.py
    python scripts/fetch_model.py --from ../hgsfp-survey-tool/models/all-MiniLM-L6-v2
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import PROJECT_ROOT, settings  # noqa: E402

MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_SOURCES = [
    PROJECT_ROOT.parent / "hgsfp-survey-tool" / "models" / "all-MiniLM-L6-v2",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", help="local model directory to copy from")
    args = parser.parse_args()

    target = settings.model_dir
    if (target / "config.json").exists():
        print(f"Model already present at {target}")
        return 0

    candidates = [Path(args.source)] if args.source else DEFAULT_SOURCES
    for candidate in candidates:
        if (candidate / "config.json").exists():
            print(f"Copying model from {candidate}")
            shutil.copytree(candidate, target, dirs_exist_ok=True)
            print(f"Done: {target}")
            return 0

    print(f"No local copy found; downloading {MODEL_ID} from Hugging Face…")
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("sentence-transformers is not installed.", file=sys.stderr)
        return 1

    model = SentenceTransformer(MODEL_ID)
    target.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(target))
    print(f"Done: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

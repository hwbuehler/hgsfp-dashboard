"""Fire concurrent submissions at a published survey.

Checks that SQLite's WAL mode plus busy_timeout survives the burst you get when
a lecture hall fills in the questionnaire at once.

Usage (with the server running):
    python scripts/load_test.py http://127.0.0.1:8000/s/<token> --count 60
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402


LECTURE_COMMENTS = [
    "Very clear and well paced.",
    "Too much material for the time available.",
    "The demonstrations were the highlight.",
    "Would have liked more worked examples.",
    "Assumed more background than I have.",
]
ORGANISATION_COMMENTS = [
    "The coffee breaks were too short.",
    "Rooms were easy to find, signage was good.",
    "Please bring back the cake.",
    "Lunch queue took most of the break.",
]
TOPIC_SUGGESTIONS = [
    "Quantum optics",
    "Accelerator physics",
    "Climate modelling",
    "Machine learning for physics, statistics",
]


def build_payload(definition: dict) -> dict:
    """A plausible response — ratings *and* free text.

    Generating ratings only would leave a survey whose report legitimately has
    no comments anywhere, which looks exactly like a broken comment feature.
    """
    payload: dict = {}
    comments: dict[str, str] = {}

    for page in definition["pages"]:
        dropdown = page["elements"][0]
        if dropdown.get("type") != "dropdown":
            continue
        prefix = dropdown["name"].removesuffix("_title")
        choice = random.choice(dropdown["choices"])
        payload[dropdown["name"]] = choice["value"]
        if choice["value"] == "DnA":
            continue
        for element in page["elements"][1:]:
            payload[element["name"]] = random.randint(1, 5)
        # Roughly a third of attendees leave a comment, as in the real data.
        if random.random() < 0.35:
            comments[f"{prefix}_comment"] = random.choice(LECTURE_COMMENTS)

    if comments:
        payload["sugg_lectures"] = comments
    if random.random() < 0.3:
        payload["sugg_organization"] = random.choice(ORGANISATION_COMMENTS)
    if random.random() < 0.4:
        payload["sugg_topics"] = random.choice(TOPIC_SUGGESTIONS)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="participation URL, e.g. http://127.0.0.1:8000/s/<token>")
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()

    base = args.url.rstrip("/")
    definition = httpx.get(f"{base}/definition.json", timeout=30).json()

    results: list[tuple[int, str]] = []

    def submit(_: int) -> None:
        with httpx.Client(timeout=30) as client:
            response = client.post(f"{base}/responses", json=build_payload(definition))
            results.append((response.status_code, response.text[:120]))

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(submit, range(args.count)))
    elapsed = time.perf_counter() - started

    ok = sum(1 for status, _ in results if status == 200)
    print(f"{ok}/{args.count} accepted in {elapsed:.2f}s ({args.count / elapsed:.0f} req/s)")
    failures = [r for r in results if r[0] != 200]
    for status, body in failures[:5]:
        print(f"  {status}: {body}")
    return 0 if ok == args.count else 1


if __name__ == "__main__":
    raise SystemExit(main())

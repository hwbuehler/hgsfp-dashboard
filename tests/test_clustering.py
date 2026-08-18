"""Comment grouping.

The original settings were inert: scikit-learn defaults to euclidean distance
with ward linkage, and the model emits L2-normalised vectors, so the inherited
threshold of 0.5 demanded a cosine similarity of 0.875 — higher than any pair of
real comments in the sample data reaches. Every comment formed its own group.
These tests pin the corrected behaviour.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sentence_transformers")

from app.analysis.clustering import ModelUnavailable, cluster_comments  # noqa: E402
from app.config import settings  # noqa: E402

pytestmark = pytest.mark.skipif(
    not settings.model_dir.exists(),
    reason="sentence-transformer model not installed (scripts/fetch_model.py)",
)

# Deliberately *not* near-duplicates. Wordings this close (cosine ~0.93) merge
# even under the broken euclidean/ward settings, so a fixture built from them
# would pass while the production bug was still present. These three sit around
# cosine 0.69-0.79 — related in meaning, unrelated in wording, exactly the case
# that used to end up as three separate groups.
RELATED_COMMENTS = [
    "Better food at coffee break.",
    "Better snacks in the coffee break like last year",
    "Please more cake and snacks during the coffee breaks",
]
UNRELATED = [
    "The lecture hall was too cold",
    "Quantum computing",
    "Please bring back the cake",
]


def _labels(result):
    return {line.rsplit(" (x", 1)[0] for line in result.clustered}


def _sizes(result):
    return sorted(int(line.rsplit("(x", 1)[1].rstrip(")")) for line in result.clustered)


def test_related_comments_are_merged():
    """The regression test proper: these only merge with cosine + average."""
    result = cluster_comments(RELATED_COMMENTS)
    assert len(result.clustered) < len(RELATED_COMMENTS), (
        "nothing merged — check the metric, not just the threshold"
    )
    assert max(_sizes(result)) >= 2


def test_unrelated_comments_stay_apart():
    result = cluster_comments(UNRELATED)
    assert _sizes(result) == [1, 1, 1]


def test_every_comment_survives_clustering():
    """The raw list is the record of what people wrote; it must stay complete."""
    result = cluster_comments(RELATED_COMMENTS + UNRELATED)
    assert sorted(result.raw) == sorted(RELATED_COMMENTS + UNRELATED)
    assert sum(_sizes(result)) == len(RELATED_COMMENTS + UNRELATED)


def test_group_label_is_one_of_its_members():
    result = cluster_comments(RELATED_COMMENTS)
    assert _labels(result) <= set(RELATED_COMMENTS)


def test_label_does_not_depend_on_input_order():
    """The medoid is stable; the first-member label was an accident of ordering."""
    forward = cluster_comments(RELATED_COMMENTS)
    backward = cluster_comments(list(reversed(RELATED_COMMENTS)))
    assert _labels(forward) == _labels(backward)
    assert _sizes(forward) == _sizes(backward)


def test_topics_only_merge_genuine_duplicates():
    """Short noun phrases: the model reads "sounds like physics" as "same subject".

    A looser bar groups Mathematical Physics with Physical Chemistry, which would
    corrupt the request counts the report prints next to each entry.
    """
    topics = ["2D materials", "2d Materials", "Mathematical physics", "Physical Chemistry"]
    result = cluster_comments(topics, use_semantic_split=True)
    labels = {line.rsplit(" (x", 1)[0]: int(line.rsplit("(x", 1)[1].rstrip(")"))
              for line in result.clustered}
    assert max(labels.values()) == 2, "only the spelling variants may merge"
    merged = [label for label, size in labels.items() if size == 2][0]
    assert "materials" in merged.lower()


def test_topic_threshold_is_stricter_than_the_comment_threshold():
    assert settings.topic_cluster_distance < settings.comment_cluster_distance


def test_empty_and_single_inputs():
    assert cluster_comments([]).clustered == []
    assert cluster_comments(["only one"]).clustered == ["only one (x1)"]

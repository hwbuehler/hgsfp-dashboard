"""Group free-text comments by meaning.

Ported from _comment_grouper / _segment_by_semantic_similarity in
hgsfp-survey-tool/survey_analyzer.py, which in turn follows Tom Aarsen's
agglomerative clustering example for sentence-transformers.

The change that matters here is loading: the original built a SentenceTransformer
in __init__, so merely constructing the analyzer pulled ~90 MB into memory.  The
model is now loaded once per process, on first use, so the web app starts fast
and never loads it at all unless somebody runs an analysis.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

_model = None
_model_lock = threading.Lock()


class ModelUnavailable(RuntimeError):
    """Raised when the sentence-transformer model cannot be loaded."""


def get_model():
    """Load the sentence-transformer once and share it across requests."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                model_dir = settings.model_dir
                if not model_dir.exists():
                    raise ModelUnavailable(
                        f"Model directory {model_dir} not found — "
                        "run scripts/fetch_model.py first."
                    )
                from sentence_transformers import SentenceTransformer

                logger.info("Loading sentence-transformer from %s", model_dir)
                _model = SentenceTransformer(str(model_dir))
    return _model


@dataclass(frozen=True)
class ClusteredComments:
    """Both views the report needs: every comment, and one line per cluster."""

    raw: list[str]
    clustered: list[str]

    def to_dict(self) -> dict:
        return {"raw": self.raw, "clustered": self.clustered}

    @classmethod
    def from_dict(cls, data: dict) -> "ClusteredComments":
        return cls(raw=data.get("raw", []), clustered=data.get("clustered", []))


def segment_by_semantic_similarity(
    text: str | list[str], similarity_threshold: float = 0.0
) -> list[str]:
    """Split text into semantically distinct units.

    Accepts a plain string (split on sentence boundaries) or an already-split
    list, e.g. the comma-separated parts of a topic suggestion.  Adjacent parts
    are merged when their cosine similarity reaches the threshold, so a comma
    that joins one thought does not become two topics.
    """
    if isinstance(text, list):
        sentences = [s.strip() for s in text if s.strip()]
    else:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]

    if len(sentences) <= 1:
        return sentences

    embeddings = get_model().encode(sentences, convert_to_numpy=True)

    segments: list[str] = []
    current = sentences[0]
    for i in range(1, len(sentences)):
        similarity = np.dot(embeddings[i - 1], embeddings[i]) / (
            np.linalg.norm(embeddings[i - 1]) * np.linalg.norm(embeddings[i])
        )
        if similarity < similarity_threshold:
            segments.append(current)
            current = sentences[i]
        else:
            current += " " + sentences[i]
    segments.append(current)
    return segments


def _medoid(members: list[str], vectors) -> str:
    """The member closest to the group's centre, used as its label.

    Taking the first member instead would make the headline depend on the order
    responses happen to arrive in.
    """
    if len(members) == 1:
        return members[0]
    centre = vectors.mean(axis=0)
    centre = centre / (np.linalg.norm(centre) or 1.0)
    return members[int(np.argmax(vectors @ centre))]


def cluster_comments(
    corpus: list[str],
    use_semantic_split: bool = False,
    split_similarity_threshold: float | None = None,
    distance_threshold: float | None = None,
) -> ClusteredComments:
    """Cluster comments and return the raw list alongside one line per cluster.

    ``use_semantic_split`` is for topic suggestions, where a comma may separate
    two distinct topics.  Leave it off for organisation comments, where commas
    are ordinary punctuation.

    ``distance_threshold`` is a *cosine* distance, so 0.45 means "merge while
    average similarity stays at or above 0.55".  Callers pass different values
    for prose and for keyword lists; see the settings for why.
    """
    if split_similarity_threshold is None:
        split_similarity_threshold = settings.topic_split_similarity_threshold
    if distance_threshold is None:
        distance_threshold = (
            settings.topic_cluster_distance
            if use_semantic_split
            else settings.comment_cluster_distance
        )

    prepared: list[str] = []
    for comment in corpus:
        if not isinstance(comment, str):
            continue
        if use_semantic_split:
            parts = [p.strip() for p in comment.split(",") if p.strip()]
            prepared.extend(
                segment_by_semantic_similarity(
                    parts, similarity_threshold=split_similarity_threshold
                )
            )
        else:
            stripped = comment.strip()
            if stripped:
                prepared.append(stripped)

    if not prepared:
        return ClusteredComments(raw=[], clustered=[])
    if len(prepared) == 1:
        return ClusteredComments(raw=prepared, clustered=[f"{prepared[0]} (x1)"])

    from sklearn.cluster import AgglomerativeClustering

    embeddings = get_model().encode(prepared, convert_to_numpy=True)

    # cosine + average, not scikit-learn's euclidean + ward default. The model
    # emits L2-normalised vectors, so a euclidean threshold of t corresponds to
    # a cosine similarity of 1 - t²/2 — the old default of 0.5 therefore demanded
    # 0.875 similarity, which no two real comments in the sample data reach. The
    # clustering could not merge anything, whatever the input.
    model = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=distance_threshold,
        metric="cosine",
        linkage="average",
    )
    model.fit(embeddings)

    clusters: dict[int, list[int]] = {}
    for index, label in enumerate(model.labels_):
        clusters.setdefault(int(label), []).append(index)

    clustered: list[str] = []
    grouped: list[str] = []
    # Largest clusters first — the report reads better when the most common
    # feedback is at the top.
    for indices in sorted(clusters.values(), key=len, reverse=True):
        members = [prepared[i] for i in indices]
        label = _medoid(members, embeddings[indices])
        clustered.append(f"{label} (x{len(members)})")
        grouped.extend(members)

    return ClusteredComments(raw=grouped, clustered=clustered)

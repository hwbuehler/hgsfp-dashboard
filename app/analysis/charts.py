"""Render one Likert distribution as an inline SVG stacked bar.

Replaces _create_likert_figure from hgsfp-survey-tool/survey_analyzer.py, which
drew all six questions into a single A4-landscape matplotlib figure.  Page layout
is now HTML/CSS, so each question renders on its own and the report decides how
they are arranged.

The SVG is emitted directly rather than through matplotlib for three reasons:
the design calls for 2px surface gaps between segments and 4px rounding on the
outer ends only, which matplotlib's barh does not express; inline SVG keeps the
percentages as real text in both the browser and WeasyPrint instead of glyph
outlines; and the output is a few hundred bytes rather than a base64 PNG.

Colour: the five Likert steps are a polarity scale, so the palette is diverging —
two hues either side of a neutral gray midpoint, inherited unchanged from the
original tool (a ColorBrewer RdBu selection).
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from app.analysis.constants import CONSTANTS
from app.analysis.statistics import QuestionStats

# Geometry, in the SVG's own pixel units.  The bar scales to the container width
# with its aspect ratio intact, so the viewBox width sets how tall it ends up:
# at a ~1000pt content width the bar renders at roughly its nominal 24px, which
# keeps a whole section on one printed page.
CHART_WIDTH = 1000
BAR_HEIGHT = 24
CORNER_RADIUS = 4
SEGMENT_GAP = 2
LABEL_FONT_SIZE = 11
# Rough advance width per character for the report's sans stack at 11px; used to
# decide whether a label fits, never to position it.
CHAR_WIDTH_RATIO = 0.58
LABEL_PADDING = 6

SURFACE = "#ffffff"
EMPTY_FILL = "#eceff1"
EMPTY_TEXT = "#6b7280"
INK = "#1f2937"


def _luminance(hex_color: str) -> float:
    """Relative luminance, used to pick readable label ink on a filled segment."""
    value = hex_color.lstrip("#")
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _label_ink(fill: str) -> str:
    return INK if _luminance(fill) > 0.5 else "#ffffff"


def _segment_path(x: float, width: float, round_left: bool, round_right: bool) -> str:
    """A bar segment, rounded only where it is the outer end of the whole bar."""
    y, height = 0.0, float(BAR_HEIGHT)
    radius = min(CORNER_RADIUS, width / 2)
    left = radius if round_left else 0.0
    right = radius if round_right else 0.0

    return (
        f"M{x + left:.2f},{y:.2f}"
        f"H{x + width - right:.2f}"
        + (f"a{right:.2f},{right:.2f} 0 0 1 {right:.2f},{right:.2f}" if right else "")
        + f"V{height - right:.2f}"
        + (f"a{right:.2f},{right:.2f} 0 0 1 -{right:.2f},{right:.2f}" if right else "")
        + f"H{x + left:.2f}"
        + (f"a{left:.2f},{left:.2f} 0 0 1 -{left:.2f},-{left:.2f}" if left else "")
        + f"V{y + left:.2f}"
        + (f"a{left:.2f},{left:.2f} 0 0 1 {left:.2f},-{left:.2f}" if left else "")
        + "Z"
    )


def _fits(text: str, width: float) -> bool:
    return width >= len(text) * LABEL_FONT_SIZE * CHAR_WIDTH_RATIO + 2 * LABEL_PADDING


def render_likert_bar(stats: QuestionStats) -> str:
    """Inline SVG for one question's distribution.

    Returns markup meant to be embedded directly in the report template — not a
    data URI — so that its text stays selectable and themable.
    """
    view_box = f"0 0 {CHART_WIDTH} {BAR_HEIGHT}"
    # No preserveAspectRatio override: stretching the viewBox to the container
    # width would squash the percentages and flatten the corner radii.
    opening = (
        f'<svg class="likert-bar" viewBox="{view_box}" role="img" '
        f'aria-label="{escape(_accessible_label(stats))}">'
    )

    if stats.n == 0:
        body = (
            f'<path d="{_segment_path(0, CHART_WIDTH, True, True)}" fill="{EMPTY_FILL}"/>'
            f'<text x="{CHART_WIDTH / 2:.0f}" y="{BAR_HEIGHT / 2:.0f}" '
            f'dominant-baseline="central" text-anchor="middle" '
            f'font-size="{LABEL_FONT_SIZE}" fill="{EMPTY_TEXT}">no responses</text>'
        )
        return opening + body + "</svg>"

    visible = [i for i, pct in enumerate(stats.percentages) if pct > 0]
    gaps = SEGMENT_GAP * max(len(visible) - 1, 0)
    usable = CHART_WIDTH - gaps

    parts: list[str] = []
    cursor = 0.0
    for position, index in enumerate(visible):
        width = usable * stats.percentages[index] / 100
        fill = CONSTANTS.likert_palette[index]
        parts.append(
            f'<path d="{_segment_path(cursor, width, position == 0, position == len(visible) - 1)}"'
            f' fill="{fill}"/>'
        )

        label = f"{stats.percentages[index]:.0f}%"
        if _fits(label, width):
            parts.append(
                f'<text x="{cursor + width / 2:.2f}" y="{BAR_HEIGHT / 2:.0f}" '
                f'dominant-baseline="central" text-anchor="middle" '
                f'font-size="{LABEL_FONT_SIZE}" fill="{_label_ink(fill)}">{label}</text>'
            )
        cursor += width + SEGMENT_GAP

    return opening + "".join(parts) + "</svg>"


def _accessible_label(stats: QuestionStats) -> str:
    if stats.n == 0:
        return f"{stats.title}: no responses"
    # One decimal here, unlike the in-bar label: a 0.4% slice must not be
    # announced as "0%" to a screen reader.
    pieces = [
        f"{label} {pct:g}%"
        for label, pct in zip(stats.labels, stats.percentages)
        if pct > 0
    ]
    return f"{stats.title}: " + ", ".join(pieces)


def legend_items(question: str) -> list[dict[str, str]]:
    """Swatch colour and wording for the scale a question uses."""
    return [
        {"color": CONSTANTS.likert_palette[i], "label": label}
        for i, label in enumerate(CONSTANTS.labels_for(question))
    ]

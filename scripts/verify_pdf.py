"""Render every report section to PDF and to a ZIP, without a web server.

Used to check the export path on a machine where WeasyPrint's native libraries
are available (the container, or Windows with the GTK runtime installed):

    python scripts/verify_pdf.py
    python scripts/verify_pdf.py --out /tmp/reports
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis.aggregate import SlotConfig, aggregate  # noqa: E402
from app.analysis.clustering import ClusteredComments, ModelUnavailable, cluster_comments  # noqa: E402
from app.analysis.legacy_import import derive_slots, parse_export  # noqa: E402
from app.analysis.pdf import PdfUnavailable, build_zip, render_pdf  # noqa: E402
from app.analysis.report import (  # noqa: E402
    ReportContext,
    build_comment_groups,
    build_sections,
    build_summary_rows,
)
from app.config import settings  # noqa: E402
from app.templating import templates  # noqa: E402

CLUSTER_NOTE = "Grouping uses the all-MiniLM-L6-v2 sentence-transformer model."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="build/pdf-verification")
    parser.add_argument("--skip-clustering", action="store_true")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    raw = (settings.reference_dir / "sample-survey-results.json").read_bytes()
    responses = parse_export(raw)
    specs = derive_slots(responses)
    slots = [
        SlotConfig(
            key_prefix=spec["key_prefix"],
            page_title=spec["page_title"],
            dna_value="DnA",
            choice_texts={c: c for c in spec["choices"]},
        )
        for spec in specs
    ]

    aggregated = aggregate(responses, slots)
    sections = build_sections(aggregated, slots)

    if args.skip_clustering:
        organization = ClusteredComments(raw=aggregated.organization, clustered=[])
        topics = ClusteredComments(raw=aggregated.topics, clustered=[])
    else:
        try:
            organization = cluster_comments(aggregated.organization)
            topics = cluster_comments(aggregated.topics, use_semantic_split=True)
        except ModelUnavailable as exc:
            print(f"clustering skipped: {exc}")
            organization = ClusteredComments(raw=aggregated.organization, clustered=[])
            topics = ClusteredComments(raw=aggregated.topics, clustered=[])

    context = ReportContext(
        survey_title="56th Graduate Days",
        total_responses=aggregated.total_responses,
        sections=sections,
        organization=organization,
        topics=topics,
        summary_rows=build_summary_rows(sections),
        comment_groups=build_comment_groups(aggregated, slots),
    )

    template = templates.get_template("report/document.html")
    base_url = str(settings.static_dir) + "/"

    def render(scope: str, chosen) -> str:
        return template.render(
            request=None,
            document_title=f"{context.survey_title} — {scope}",
            css_href="css/report.css",
            sections=chosen,
            context=context,
            screen_actions=None,
            include_free_text=scope in ("all", "overall"),
            include_all_comments=scope in ("all", "overall"),
            include_summary=scope in ("all", "overall"),
            cluster_note=CLUSTER_NOTE,
        )

    try:
        full = render_pdf(render("all", context.sections), base_url)
    except PdfUnavailable as exc:
        print(f"FAIL  {exc}")
        return 1

    (out / "full-report.pdf").write_bytes(full)
    print(f"PASS  full report                {len(full):>9,} bytes")

    files: dict[str, bytes] = {}
    for index, section in enumerate(context.sections):
        pdf = render_pdf(render(section.scope, [section]), base_url)
        files[f"{index:02d}-{section.scope}.pdf"] = pdf
        print(f"PASS  {section.scope:28s} {len(pdf):>9,} bytes")

    archive = build_zip(files)
    (out / "reports.zip").write_bytes(archive)
    print(f"PASS  zip of {len(files)} reports        {len(archive):>9,} bytes")
    print(f"\nWritten to {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

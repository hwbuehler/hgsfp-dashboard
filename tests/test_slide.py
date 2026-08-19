"""The projection slide.

Rendered with fpdf2, not WeasyPrint: the slide has to be producible on a laptop
shortly before a lecture, and WeasyPrint's native dependencies are not present
on a plain Windows install.
"""

from __future__ import annotations

import pytest

from app.slide import SLIDE_HEIGHT_MM, SLIDE_WIDTH_MM, _latin1, build_slide

URL = "https://umfragen.example.org/s/abc123XYZ_-"


def _page_size_pt(data: bytes):
    pdfium = pytest.importorskip("pypdfium2")
    return pdfium.PdfDocument(data)[0].get_size()


def test_slide_is_a_pdf():
    assert build_slide(URL)[:5] == b"%PDF-"


def test_slide_is_16_by_9_landscape():
    width, height = _page_size_pt(build_slide(URL))
    assert width > height
    assert width / height == pytest.approx(16 / 9, abs=0.02)
    assert width / height == pytest.approx(SLIDE_WIDTH_MM / SLIDE_HEIGHT_MM, abs=0.02)


def test_slide_carries_the_link_as_text():
    """People who cannot scan need to be able to type it in."""
    pdfium = pytest.importorskip("pypdfium2")
    text = pdfium.PdfDocument(build_slide(URL))[0].get_textpage().get_text_range()
    assert URL in text.replace("\n", "")
    assert "Graduate Days" in text


def test_slide_does_not_need_weasyprint(monkeypatch):
    """Importing WeasyPrint raises OSError without GTK; the slide must not care."""
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name.split(".")[0] == "weasyprint":
            raise OSError("no GTK here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    assert build_slide(URL)[:5] == b"%PDF-"


@pytest.mark.parametrize(
    "text,expected",
    [("a — b", "a - b"), ("„quoted“", '"quoted"'), ("it’s", "it's"), ("Grüße", "Grüße")],
)
def test_typographic_characters_survive_the_core_font(text, expected):
    """The core PDF fonts are Latin-1; an em dash used to abort the download."""
    assert _latin1(text) == expected


def test_long_title_characters_do_not_break_the_render():
    assert build_slide(URL, headline="Wie war’s — Rückmeldung erwünscht…")[:5] == b"%PDF-"

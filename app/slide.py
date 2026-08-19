"""The projection slide that invites people to fill in the survey.

A 16:9 landscape PDF: logo, headline, short instruction, QR code and the plain
link, laid out like the slide the Graduate Days have used so far.

Built with fpdf2 rather than WeasyPrint, which renders the reports. WeasyPrint
needs Pango and cairo, so on a plain Windows install it cannot produce anything
— and this slide is exactly what somebody wants to generate on their laptop
shortly before a lecture. fpdf2 is pure Python and draws a fixed layout like
this one without help.
"""

from __future__ import annotations

import io

from fpdf import FPDF

from app.config import settings

# 13.333 x 7.5 inches — the 16:9 canvas presentation software calls "widescreen".
SLIDE_WIDTH_MM = 338.67
SLIDE_HEIGHT_MM = 190.5

HEADLINE = "How did you like this year's Graduate Days?"
# ** ** marks bold; fpdf2 renders it when multi_cell is given markdown=True.
BODY = (
    "Please take a moment to fill in our short survey. It only takes **5 minutes** "
    "and helps us improve future Graduate Days. Simply scan the QR code below "
    "to get started."
)
LINK_PREFIX = "Or go to "

INK = (17, 17, 17)
MUTED = (150, 150, 150)

# The core PDF fonts are Latin-1 only. German umlauts are covered; typographic
# punctuation is not, and would abort the download over a stray character.
TRANSLITERATIONS = {
    "—": "-", "–": "-", "‘": "'", "’": "'",
    "‚": ",", "“": '"', "”": '"', "„": '"',
    "…": "...", " ": " ", "→": "->",
}


def _latin1(text: str) -> str:
    for source, target in TRANSLITERATIONS.items():
        text = text.replace(source, target)
    return text.encode("latin-1", "replace").decode("latin-1")


def _qr_png(url: str) -> io.BytesIO:
    import qrcode

    # High error correction so the code still scans with a logo-sized chunk of a
    # projector beam washed out, and a large box size so it stays crisp at A0.
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_Q, box_size=20, border=1)
    code.add_data(url)
    code.make(fit=True)
    buffer = io.BytesIO()
    code.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def build_slide(url: str, headline: str = HEADLINE, body: str = BODY) -> bytes:
    pdf = FPDF(orientation="landscape", unit="mm", format=(SLIDE_HEIGHT_MM, SLIDE_WIDTH_MM))
    pdf.set_auto_page_break(False)
    pdf.add_page()
    pdf.set_text_color(*INK)

    left_x, right_x = 26.0, 180.0
    right_w = SLIDE_WIDTH_MM - right_x - 26.0

    logo = settings.static_dir / "img" / "hgsfp-logo.svg"
    if logo.exists():
        pdf.image(str(logo), x=left_x, y=20, w=70)

    # Headline sits on the lower half of the left column, as on the printed slide.
    pdf.set_xy(left_x, 120)
    pdf.set_font("Times", "B", 33)
    pdf.multi_cell(w=140, h=15, text=_latin1(headline), align="L")

    pdf.set_xy(right_x, 24)
    pdf.set_font("Times", "", 17)
    pdf.multi_cell(w=right_w, h=9, text=_latin1(body), align="L", markdown=True)

    qr_size = 76.0
    qr_x = right_x + (right_w - qr_size) / 2
    pdf.image(_qr_png(url), x=qr_x, y=66, w=qr_size, h=qr_size)

    pdf.set_xy(right_x, 66 + qr_size + 8)
    pdf.set_font("Times", "", 12)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(w=right_w, h=6, text=_latin1(LINK_PREFIX + url), align="C")

    return bytes(pdf.output())

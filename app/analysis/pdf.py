"""Turn a rendered report page into PDF, and bundle several into a ZIP.

WeasyPrint needs the Pango/GTK libraries, which pip cannot install on Windows.
It is therefore imported lazily: a machine without them still serves the HTML
preview and runs analyses, and only the export button reports the problem.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path


class PdfUnavailable(RuntimeError):
    """Raised when WeasyPrint's native dependencies are missing."""


def _weasyprint():
    try:
        import weasyprint
    except OSError as exc:
        # WeasyPrint raises OSError, not ImportError, when libpango is absent.
        raise PdfUnavailable(
            "WeasyPrint kann die GTK/Pango-Bibliotheken nicht laden. "
            "Unter Windows die MSYS2-GTK-Runtime installieren (siehe README) "
            "oder den PDF-Export im Container ausführen."
        ) from exc
    except ImportError as exc:
        raise PdfUnavailable("WeasyPrint ist nicht installiert.") from exc
    return weasyprint


def render_pdf(html: str, base_url: str | Path) -> bytes:
    """Render a full HTML document to PDF bytes.

    ``base_url`` must point at a location from which the stylesheet and logo
    resolve — pass the app's static root.
    """
    weasyprint = _weasyprint()
    document = weasyprint.HTML(string=html, base_url=str(base_url))
    return document.write_pdf()


def build_zip(files: dict[str, bytes]) -> bytes:
    """Bundle ``{filename: content}`` into an in-memory ZIP."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()

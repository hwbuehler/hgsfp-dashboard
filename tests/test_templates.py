"""Guard against templates and assets disappearing.

Redesigning the dashboard in an external tool once deleted the participant
pages, the report document and two HTMX fragments as collateral. Nothing failed
at import time — the pages only broke when somebody opened them. These tests
turn that into an immediate, obvious failure.
"""

from __future__ import annotations

import re

import pytest

from app.config import PROJECT_ROOT

TEMPLATE_DIR = PROJECT_ROOT / "app" / "templates"
STATIC_DIR = PROJECT_ROOT / "app" / "static"

PY_SOURCES = sorted((PROJECT_ROOT / "app").rglob("*.py"))
TEMPLATES = sorted(TEMPLATE_DIR.rglob("*.html"))


def _python_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in PY_SOURCES)


def _template_names_used_in_python() -> set[str]:
    # Matches the "<dir>/<name>.html" literals handed to TemplateResponse.
    return set(re.findall(r'"([\w/]+\.html)"', _python_text()))


def _referenced_templates() -> set[str]:
    names = _template_names_used_in_python()
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8")
        names |= set(re.findall(r'(?:include|extends|import)\s+"([^"]+)"', text))
    return names


@pytest.mark.parametrize("name", sorted(_referenced_templates()))
def test_referenced_template_exists(name):
    assert (TEMPLATE_DIR / name).is_file(), f"{name} is referenced but missing"


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_template_parses(path):
    from app.templating import templates

    relative = path.relative_to(TEMPLATE_DIR).as_posix()
    templates.get_template(relative)  # raises TemplateSyntaxError on bad markup


def _referenced_static() -> set[str]:
    refs: set[str] = set()
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8")
        refs |= set(re.findall(r'(?:href|src)="/static/([^"]+)"', text))
    # The report stylesheet is addressed relative to the static root for
    # WeasyPrint, so it never appears with a /static/ prefix.
    refs |= set(re.findall(r'"(css/[\w.-]+)"', _python_text()))
    return refs


@pytest.mark.parametrize("asset", sorted(_referenced_static()))
def test_referenced_static_asset_exists(asset):
    assert (STATIC_DIR / asset).is_file(), f"static/{asset} is referenced but missing"


def test_dashboard_css_defines_every_class_the_templates_use():
    """A redesign that renames a class must not leave fragments unstyled."""
    css = (STATIC_DIR / "css" / "dashboard.css").read_text(encoding="utf-8")
    defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", css))

    # Only pages served inside the dashboard chrome; the report and the
    # participant form carry their own stylesheets.
    own_stylesheet = {"report", "participation"}
    missing: dict[str, str] = {}
    for path in TEMPLATES:
        relative = path.relative_to(TEMPLATE_DIR).as_posix()
        if relative.split("/")[0] in own_stylesheet and relative != "report/index.html":
            continue
        for attr in re.findall(r'class="([^"{}]*)"', path.read_text(encoding="utf-8")):
            for cls in attr.split():
                if cls not in defined:
                    missing.setdefault(cls, relative)

    assert not missing, "undefined CSS classes: " + ", ".join(
        f"{cls} ({where})" for cls, where in sorted(missing.items())
    )

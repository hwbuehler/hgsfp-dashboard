"""Shared Jinja2 environment.

Kept out of main.py so routers can import it without a circular dependency.
"""

from __future__ import annotations

from fastapi.templating import Jinja2Templates

from app.config import settings

templates = Jinja2Templates(directory=str(settings.templates_dir))

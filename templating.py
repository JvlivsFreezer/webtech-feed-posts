import json
from datetime import UTC, datetime

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from config import settings


def _page_context(request: Request) -> dict:
    """Variables available in every template (set by routers.pages)."""
    return {
        "current_user": getattr(request.state, "current_user", None),
        "sidebar": getattr(request.state, "sidebar", None),
        "site_name": settings.site_name,
        "require_email_verification": settings.require_email_verification,
        "current_year": datetime.now(UTC).year,
    }


def _isoformat(value: datetime) -> str:
    return value.isoformat()


def _json_script(value) -> Markup:
    """JSON that is safe to embed inside a <script> tag."""
    text = (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return Markup(text)


templates = Jinja2Templates(directory="templates", context_processors=[_page_context])
templates.env.filters["isoformat"] = _isoformat
templates.env.filters["json_script"] = _json_script

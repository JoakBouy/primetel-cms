"""
PDF rendering helper.

WeasyPrint depends on native libs (Pango, Cairo, GDK-Pixbuf) that aren't
present in every dev environment. We try the import lazily; if it fails we
return the rendered HTML as a fallback so the view still works for the user
(they can print from the browser). Production servers should install the
native deps so a real PDF is returned. Failures are logged so a broken
production setup doesn't go unnoticed.
"""
from __future__ import annotations

import logging

from django.http import HttpResponse

logger = logging.getLogger(__name__)


def render_pdf(html: str, filename: str) -> HttpResponse:
    try:
        from weasyprint import HTML  # type: ignore
        pdf = HTML(string=html).write_pdf()
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="{filename}"'
        return resp
    except Exception:
        logger.exception("PDF rendering failed for %s; serving HTML instead", filename)
        resp = HttpResponse(html)
        resp["Content-Disposition"] = (
            f'inline; filename="{filename.replace(".pdf", ".html")}"'
        )
        return resp

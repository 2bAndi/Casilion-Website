"""HTML -> PDF with WeasyPrint, which needs the Pango libraries of the operating system.

On Linux (server, Docker) they come with the system packages. On Windows they have to be
installed once (MSYS2, see README "PDF documents on Windows"); until then every PDF raises
:class:`PdfUnavailable` with that hint instead of a bare ``OSError`` from deep inside cffi."""

from __future__ import annotations

from functools import wraps

from django.http import HttpResponse

HINT = (
    "WeasyPrint cannot load the Pango libraries. On Windows: install MSYS2, run "
    "`pacman -S mingw-w64-ucrt-x86_64-pango` in the MSYS2 UCRT64 shell and set "
    "WEASYPRINT_DLL_DIRECTORIES=C:\\msys64\\ucrt64\\bin (e.g. in .env). See README."
)


class PdfUnavailable(RuntimeError):
    pass


def html_to_pdf(html: str, **options) -> bytes:
    try:
        from weasyprint import HTML
    except OSError as exc:  # missing native libraries (Windows without Pango)
        raise PdfUnavailable(f"{HINT} ({exc})") from exc
    return HTML(string=html).write_pdf(**options)


def pdf_view(view):
    """A view that renders a PDF answers 503 with a readable hint when PDFs cannot be made."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except PdfUnavailable:
            return HttpResponse(
                "PDF documents cannot be created on this computer at the moment.\n\n" + HINT,
                status=503,
                content_type="text/plain; charset=utf-8",
            )

    return wrapper

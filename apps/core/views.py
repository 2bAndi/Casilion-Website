import markdown
from django.db import connection
from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.utils.safestring import mark_safe

from apps.billing.services import public_plans
from shared.corpus import slugify

from . import knowledge

LEGAL_PAGES = {
    "imprint": "Imprint",
    "privacy": "Privacy notice",
    "terms": "Terms and license agreement",
}


def home(request):
    return render(request, "core/home.html", {"plans": public_plans()})


def docs_index(request):
    corpus = knowledge.corpus()
    query = (request.GET.get("q") or "").strip()[:200]
    results = corpus.search(query, 8) if query else []
    return render(
        request,
        "core/docs_index.html",
        {"docs": corpus.docs.values(), "query": query, "results": results, "known_issues": knowledge.known_issues()},
    )


def doc(request, slug):
    page = knowledge.corpus().docs.get(slug)
    if page is None:
        raise Http404
    # Our own documentation files, not user input: rendering them as HTML is safe.
    html = markdown.markdown(
        page.markdown,
        extensions=["toc", "fenced_code", "tables"],
        extension_configs={"toc": {"slugify": slugify}},
    )
    return render(request, "core/doc.html", {"page": page, "html": mark_safe(html), "docs": knowledge.corpus().docs.values()})


def legal(request, slug):
    if slug not in LEGAL_PAGES:
        raise Http404
    return render(request, "core/legal.html", {"title": LEGAL_PAGES[slug]})


def favicon(request):
    """For clients that ask for /favicon.ico without reading the page."""
    return redirect(static("img/favicon.svg"))


def robots_txt(request):
    if settings.SITE_NOINDEX:
        body = "User-agent: *\nDisallow: /\n"
    else:
        # Public pages are open; accounts, shop steps, the backoffice and the machine endpoints are not for crawlers.
        closed = ["/account/", "/auth/", "/billing/", "/ops/", "/internal/", "/mcp", "/i18n/"]
        body = "User-agent: *\n" + "".join(f"Disallow: {path}\n" for path in closed)
    return HttpResponse(body, content_type="text/plain")


def healthz(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"ok": True})

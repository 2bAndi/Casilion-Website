import re

from django import template
from django.urls import NoReverseMatch, reverse
from django.utils.html import urlize
from django.utils.safestring import mark_safe

register = template.Library()

# Who is speaking, as a shape: circle = customer side, square = Cassilion's AI and fixed texts, triangle = a person.
_SHAPES = {
    "customer": "circle",
    "customer_agent": "circle",
    "ai": "square",
    "system": "square",
    "operator": "triangle",
}

_STATUS_TONE = {
    "queued": "info",
    "analysing": "info",
    "needs_info": "action",
    "in_review": "info",
    "bug_candidate": "alert",
    "confirmed": "alert",
    "answered": "ok",
    "closed": "quiet",
    "rejected": "quiet",
}


@register.filter
def shape(author_kind: str) -> str:
    return _SHAPES.get(author_kind, "square")


@register.filter
def tone(status: str) -> str:
    return _STATUS_TONE.get(status, "info")


@register.filter
def spaced(value: str) -> str:
    """usage_error -> usage error"""
    return str(value or "").replace("_", " ")


_INLINE_CODE = re.compile(r"`([^`\n]{1,200})`")


@register.filter
def message_body(message) -> str:
    """A ticket message as safe HTML: always escaped, links made clickable.

    Text from the customer side stays plain. Text from Cassilion (AI, fixed texts,
    people) may mark `code`, which is the only formatting that is rendered.
    """
    html = urlize(message.body, autoescape=True)
    if message.author_kind not in ("customer", "customer_agent"):
        html = _INLINE_CODE.sub(r"<code>\1</code>", html)
    return mark_safe(html)


@register.simple_tag(takes_context=True)
def nav_current(context, *names: str) -> str:
    """Marks the navigation entry of the current section."""
    request = context.get("request")
    if request is None:
        return ""
    for name in names:
        try:
            target = reverse(name)
        except NoReverseMatch:
            continue
        if request.path == target or (target != "/" and request.path.startswith(target)):
            return mark_safe(' aria-current="page"')
    return ""

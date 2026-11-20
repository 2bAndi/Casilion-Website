from django import template

from apps.backoffice import nav

register = template.Library()


@register.inclusion_tag("backoffice/_nav.html", takes_context=True)
def backoffice_nav(context):
    request = context.get("request")
    return nav.build(request.path if request else "", owner=bool(request and request.user.is_superuser))

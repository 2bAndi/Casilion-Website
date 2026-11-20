from allauth.mfa.utils import is_mfa_enabled
from django.conf import settings
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.decorators import account_required

from . import tokens
from .models import AgentToken


def _mfa_missing(user) -> bool:
    return settings.CASSILION["REQUIRE_MFA_FOR_AGENT_TOKENS"] and not is_mfa_enabled(user)


@account_required
def agents(request):
    return render(
        request,
        "mcp_server/tokens.html",
        {
            "tokens": request.account.agent_tokens.filter(kind=AgentToken.Kind.CUSTOMER, revoked_at__isnull=True).select_related("user"),
            "new_token": request.session.pop("new_agent_token", None),
            "mcp_url": settings.CASSILION["SITE_URL"] + "/mcp",
            "mfa_missing": _mfa_missing(request.user),
            "mfa_required": settings.CASSILION["REQUIRE_MFA_FOR_AGENT_TOKENS"],
        },
    )


@account_required
@require_POST
def create(request):
    if _mfa_missing(request.user):
        messages.error(request, "Turn on two-factor authentication first. An agent token gives access to your account.")
        return redirect("mcp_server:agents")
    _record, plaintext = tokens.issue(
        request.user, kind=AgentToken.Kind.CUSTOMER, account=request.account, name=request.POST.get("name", "")
    )
    request.session["new_agent_token"] = plaintext
    return redirect("mcp_server:agents")


@account_required
@require_POST
def revoke(request, token_id):
    record = get_object_or_404(AgentToken, pk=token_id, account=request.account, kind=AgentToken.Kind.CUSTOMER)
    tokens.revoke(record, request.user)
    messages.success(request, "Access revoked. The agent can no longer connect with this token.")
    return redirect("mcp_server:agents")

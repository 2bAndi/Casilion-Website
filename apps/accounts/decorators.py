from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def account_required(view):
    """Signed in and acting for a customer account. Team members without one go to the backoffice."""

    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.account:
            return redirect("backoffice:dashboard" if request.user.is_staff else "core:home")
        return view(request, *args, **kwargs)

    return wrapped

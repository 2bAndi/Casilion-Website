from django.utils.functional import SimpleLazyObject

from .services import account_for


class CurrentAccountMiddleware:
    """Puts the customer account of the signed-in user on the request (lazily)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.account = SimpleLazyObject(lambda: account_for(request.user))
        return self.get_response(request)

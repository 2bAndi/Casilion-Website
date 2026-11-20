from django.apps import AppConfig


class BackofficeConfig(AppConfig):
    name = "apps.backoffice"
    label = "backoffice"

    def ready(self):
        from django.conf import settings
        from django.contrib.auth.signals import user_logged_in

        def shorter_session(sender, request, user, **kwargs):
            # A team member's login opens the backoffice: it ends after a working day, not after two weeks.
            if user.is_staff and request is not None and hasattr(request, "session"):
                request.session.set_expiry(settings.CASSILION["STAFF_SESSION_HOURS"] * 3600)

        user_logged_in.connect(shorter_session, dispatch_uid="backoffice.shorter_session", weak=False)

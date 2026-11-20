"""Create an operator account for the backoffice, or turn an existing account into one.

    python manage.py create_operator you@example.org

Unlike `createsuperuser`, the e-mail address is marked as confirmed, so the account can
sign in at once on a site that requires confirmed addresses.
"""
import getpass
import os

from allauth.account.models import EmailAddress
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User


class Command(BaseCommand):
    help = "Create an operator (staff account with a confirmed e-mail address), or promote an existing account."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--name", default="", help="Full name shown in the backoffice.")

    def handle(self, *args, **options):
        email = User.objects.normalize_email(options["email"]).lower()
        user = User.objects.filter(email__iexact=email).first()
        created = user is None
        if created:
            user = User(email=email, full_name=options["name"])
            password = os.environ.get("OPERATOR_PASSWORD") or self._ask_password(user)
            try:
                validate_password(password, user)
            except ValidationError as exc:
                raise CommandError(" ".join(exc.messages)) from exc
            user.set_password(password)
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.save()
        EmailAddress.objects.update_or_create(user=user, email=user.email, defaults={"verified": True, "primary": True})
        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Promoted'} operator {user.email}. "
            "Sign in on the site, then switch on two-factor authentication under /auth/2fa/."
        ))

    def _ask_password(self, user) -> str:
        first = getpass.getpass("Password: ")
        if first != getpass.getpass("Password (again): "):
            raise CommandError("The two passwords differ.")
        return first
